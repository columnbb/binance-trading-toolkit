"""附加式的本機交易帳本 —— 記下單嘗試、結果、成交、開倉、平倉。

跟 ``mexc-futures-toolkit`` 的 ``ledger.py`` 是同一份設計（純粹是「事情
發生了，記一筆」，不含交易所或策略邏輯），故意保持欄位/介面一致，方便
之後如果有專案同時串接兩個交易所，帳本格式不用另外轉換。

## 已知限制

``trade_close()`` 目前只用多單公式算損益：
``(出場價 - 進場價) × 成交量 × contract_size - 手續費``。做空單要自己在
丟進來的 ``entry_price``/``exit_price`` 上處理方向。Binance USDⓈ-M 合約
的數量本身就是計價資產單位（不像 MEXC 有獨立的「一口合約等於多少幣」
換算），所以呼叫端通常直接傳 ``contract_size=1.0``。
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TradeLedger:
    """附加式的本機稽核帳本，記下單/成交/已完成交易。"""

    def __init__(self, path: str = "audit/trading_ledger.jsonl") -> None:
        self.path = Path(path)

    def append(self, event_type: str, **fields: Any) -> str:
        event_id = uuid.uuid4().hex
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "event_time": datetime.now(timezone.utc).isoformat(),
            "event_epoch_ms": int(time.time() * 1000),
            **fields,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return event_id

    def order_attempt(
        self, *, action: str, symbol: str, side: str, volume: float, live: bool,
        external_id: str | None = None, order_type: str = "market",
        parameters: dict[str, Any] | None = None,
    ) -> str:
        return self.append(
            "order_attempt", action=action, symbol=symbol, side=side, volume=volume,
            live=live, status="submitted" if live else "dry_run_skipped",
            external_id=external_id, order_type=order_type, parameters=parameters or {},
        )

    def order_result(
        self, *, attempt_event_id: str, action: str, symbol: str, side: str, volume: float,
        status: str, response: Any = None, error: str | None = None,
        order_id: str | int | None = None, external_id: str | None = None,
    ) -> str:
        return self.append(
            "order_result", attempt_event_id=attempt_event_id, action=action, symbol=symbol,
            side=side, volume=volume, status=status, response=response, error=error,
            order_id=order_id, external_id=external_id,
        )

    def fill(
        self, *, order_id: str | int | None, action: str, symbol: str, side: str, volume: float,
        price: float | None, fee: float = 0.0, raw: dict[str, Any] | None = None,
    ) -> str:
        return self.append(
            "fill", order_id=order_id, action=action, symbol=symbol, side=side, volume=volume,
            price=price, fee=fee, raw=raw or {},
        )

    def trade_open(
        self, *, trade_id: str, symbol: str, volume: float, price: float, contract_size: float,
        leverage: int, fee: float, order_id: str | int | None, source: str,
    ) -> str:
        return self.append(
            "trade_open", trade_id=trade_id, symbol=symbol, volume=volume, price=price,
            contract_size=contract_size, leverage=leverage, fee=fee, order_id=order_id, source=source,
        )

    def trade_close(
        self, *, trade_id: str, symbol: str, entry_volume: float, exit_volume: float,
        entry_price: float, exit_price: float, contract_size: float, leverage: int,
        entry_fee: float, exit_fee: float, order_id: str | int | None, source: str,
    ) -> str:
        gross_pnl = (exit_price - entry_price) * min(entry_volume, exit_volume) * contract_size
        total_fees = entry_fee + exit_fee
        net_pnl = gross_pnl - total_fees
        margin = entry_price * entry_volume * contract_size / leverage if leverage else 0.0
        return self.append(
            "trade_close", trade_id=trade_id, symbol=symbol, entry_volume=entry_volume,
            exit_volume=exit_volume, entry_price=entry_price, exit_price=exit_price,
            contract_size=contract_size, leverage=leverage, entry_fee=entry_fee, exit_fee=exit_fee,
            total_fees=total_fees, gross_pnl=gross_pnl, net_pnl=net_pnl,
            return_on_margin=(net_pnl / margin if margin else None),
            order_id=order_id, source=source,
        )
