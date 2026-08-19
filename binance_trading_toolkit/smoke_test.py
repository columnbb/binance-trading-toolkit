"""真實小額開倉 → 掛原生停損（Algo Order）→ 查詢確認 → 取消 → 平倉，驗證
``BinanceFuturesClient`` 對真實 API 真的能用，不是只驗證過參數格式。

這是 2026-08-20 用 Binance Futures Demo Trading 測試網帳號實際跑出來的
流程，已知的兩個 Binance 特有行為都寫進來了：

  1. 原生停損走的是 Algo Order API，不是一般下單端點——已經是
     ``client.stop_market_close_position()`` 內部處理，這裡不用管。
  2. **市價單的下單回應不會同步帶回成交價/量**（``executedQty``/
     ``avgPrice`` 常是空的）。所以開倉後這裡會另外呼叫
     ``open_positions()`` 查詢真正的成交價/量，不是直接信任下單回應。

跟 ``mexc-futures-toolkit`` 的 ``run_smoke_test()`` 一樣，``confirm`` 沒有
預設值，呼叫端必須明確傳 ``True`` 才會送出任何訂單。**這個函式會送出
真實訂單，執行前務必自己確認情境安全**（測試網帳號，或帳上沒有其他
部位的小額子帳戶）。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from .client import BinanceFuturesClient, extract_filters
from .ledger import TradeLedger

LOG = logging.getLogger(__name__)


class SmokeTestAborted(RuntimeError):
    """執行前的安全檢查沒過，直接拒絕送出任何訂單。"""


def quantity_for_notional(step_size: float | None, price: float, notional: float) -> float:
    """依合約規格（最小變動單位）把目標名目金額換算成可下單的數量。"""
    raw = notional / price
    if not step_size or step_size <= 0:
        return raw
    import math
    return math.floor(raw / step_size) * step_size


def _market(
    client: BinanceFuturesClient, ledger: TradeLedger, *, symbol: str, side: str, quantity: float,
    action: str, reduce_only: bool = False,
) -> tuple[Any, dict[str, Any]]:
    external_id = f"smoke-{uuid.uuid4().hex[:24]}"
    attempt = ledger.order_attempt(
        action=action, symbol=symbol, side=side, volume=quantity, live=True,
        external_id=external_id, parameters={"reduce_only": reduce_only},
    )
    result = client.market_order(symbol, side, quantity, reduce_only=reduce_only,
                                  new_client_order_id=external_id)
    if not result.ok:
        ledger.order_result(
            attempt_event_id=attempt, action=action, symbol=symbol, side=side, volume=quantity,
            status="failed", error=result.error, external_id=external_id,
        )
        raise RuntimeError(f"{action} 下單失敗：{result.error}")

    ledger.order_result(
        attempt_event_id=attempt, action=action, symbol=symbol, side=side, volume=quantity,
        status="accepted", response=result.raw, order_id=result.order_id, external_id=external_id,
    )
    return result.order_id, result.raw


def run_smoke_test(
    client: BinanceFuturesClient, ledger: TradeLedger, *, symbol: str, confirm: bool,
    notional_usdt: float = 100.0, max_notional_usdt: float = 200.0,
    stop_offset_pct: float = 0.05, position_side: str | None = None,
) -> dict[str, Any]:
    """跑一輪真實的「開倉 → 掛原生停損 → 查詢確認 → 取消 → 平倉」，回傳訂單 ID。

    測試網 ``BTCUSDT`` 的 ``MIN_NOTIONAL`` 大約是 50 USDT，預設抓 100 USDT
    留一點餘裕；正式帳號請自行依風險承受度調整。
    """
    if not confirm:
        raise SmokeTestAborted("安全檢查未通過：confirm 必須明確傳 True 才會送出真實訂單")

    positions = client.open_positions(symbol)
    if positions:
        raise SmokeTestAborted(f"安全檢查未通過：{symbol} 帳上已經有未平倉部位，拒絕執行：{positions}")

    filters_raw = client.symbol_filters(symbol)
    tick_size, step_size, min_notional = extract_filters(filters_raw)
    price = client.ticker_price(symbol)
    if price <= 0:
        raise SmokeTestAborted(f"{symbol} 的即時報價異常：{price}")

    target_notional = max(notional_usdt, min_notional or 0.0)
    quantity = quantity_for_notional(step_size, price, target_notional)
    if quantity <= 0:
        raise SmokeTestAborted(f"換算出的數量為 0（price={price}, step_size={step_size}）")
    actual_notional = quantity * price
    if actual_notional > max_notional_usdt:
        raise SmokeTestAborted(f"安全檢查未通過：換算出的名目金額 {actual_notional} 超過上限 {max_notional_usdt}")

    LOG.info("smoke_preflight symbol=%s price=%s quantity=%s notional=%s", symbol, price, quantity, actual_notional)

    trade_id = uuid.uuid4().hex
    entry_order_id = None
    algo_order_id = None
    position_open = False

    try:
        entry_order_id, _ = _market(client, ledger, symbol=symbol, side="BUY", quantity=quantity,
                                     action="smoke_open_long")
        position_open = True

        # 市價單回應不會同步帶回成交明細，查部位拿真正的進場價/量。
        time.sleep(1)
        positions = client.open_positions(symbol)
        if not positions:
            raise RuntimeError("開倉後查不到部位，可能還在處理中或本身有問題")
        entry_price = float(positions[0]["entryPrice"])
        entry_qty = abs(float(positions[0]["positionAmt"]))

        ledger.trade_open(
            trade_id=trade_id, symbol=symbol, volume=entry_qty, price=entry_price,
            contract_size=1.0, leverage=int(float(positions[0].get("leverage", 1))),
            fee=0.0, order_id=entry_order_id, source="binance_trading_toolkit_smoke_test",
        )

        stop_price = entry_price * (1 - stop_offset_pct)
        if tick_size:
            import math
            stop_price = math.floor(stop_price / tick_size) * tick_size
        stop_attempt = ledger.order_attempt(
            action="smoke_place_stop", symbol=symbol, side="SELL", volume=0, live=True,
            order_type="STOP_MARKET", parameters={"stop_price": stop_price, "position_side": position_side},
        )
        stop = client.stop_market_close_position(symbol, "SELL", stop_price, position_side=position_side)
        if not stop.ok:
            ledger.order_result(attempt_event_id=stop_attempt, action="smoke_place_stop", symbol=symbol,
                                 side="SELL", volume=0, status="failed", error=stop.error)
            raise RuntimeError(f"掛原生停損失敗：{stop.error}")
        algo_order_id = stop.order_id
        ledger.order_result(attempt_event_id=stop_attempt, action="smoke_place_stop", symbol=symbol,
                             side="SELL", volume=0, status="accepted", response=stop.raw, order_id=algo_order_id)

        open_algo = client.open_algo_orders(symbol)
        verified = any(o.get("algoId") == algo_order_id for o in open_algo)
        ledger.append("smoke_native_stop_verified", trade_id=trade_id, symbol=symbol,
                       algo_order_id=algo_order_id, stop_price=stop_price, verified_in_open_orders=verified)
        if not verified:
            LOG.warning("smoke_native_stop_not_found_in_open_orders algo_order_id=%s", algo_order_id)

        cancel_attempt = ledger.order_attempt(
            action="smoke_cancel_stop", symbol=symbol, side="SELL", volume=0, live=True,
            order_type="algo_cancel", parameters={"algo_order_id": algo_order_id},
        )
        cancel = client.cancel_algo_order(symbol, algo_order_id)
        ledger.order_result(attempt_event_id=cancel_attempt, action="smoke_cancel_stop", symbol=symbol,
                             side="SELL", volume=0, status="accepted" if cancel.ok else "failed",
                             response=cancel.raw, error=cancel.error or None, order_id=algo_order_id)
        algo_order_id = None

        close_order_id, close_raw = _market(client, ledger, symbol=symbol, side="SELL", quantity=entry_qty,
                                             action="smoke_close_long", reduce_only=True)
        position_open = False

        # 平倉單的回應一樣不會帶回成交價，用平倉當下的即時報價當近似值
        # ——跟部位查詢不同，平倉後帳上已經沒有部位可查了，這是唯一
        # 拿得到的估計方式（MEXC 那邊的 smoke test 也是同樣退回邏輯）。
        exit_price = client.ticker_price(symbol)
        time.sleep(1)
        remaining = client.open_positions(symbol)
        ledger.trade_close(
            trade_id=trade_id, symbol=symbol, entry_volume=entry_qty, exit_volume=entry_qty,
            entry_price=entry_price, exit_price=exit_price, contract_size=1.0,
            leverage=int(float(positions[0].get("leverage", 1))), entry_fee=0.0, exit_fee=0.0,
            order_id=close_order_id, source="binance_trading_toolkit_smoke_test",
        )

        if remaining:
            raise RuntimeError(f"驗證結束但帳上還有未平倉部位：{remaining}")

        LOG.info("smoke_complete symbol=%s entry_order=%s close_order=%s", symbol, entry_order_id, close_order_id)
        return {"trade_id": trade_id, "entry_order_id": entry_order_id, "close_order_id": close_order_id}

    except Exception:
        LOG.exception("smoke_test_failed")
        if algo_order_id:
            try:
                client.cancel_algo_order(symbol, algo_order_id)
            except Exception:
                LOG.exception("smoke_cleanup_stop_failed")
        if position_open:
            try:
                positions = client.open_positions(symbol)
                if positions:
                    qty = abs(float(positions[0]["positionAmt"]))
                    _market(client, ledger, symbol=symbol, side="SELL", quantity=qty,
                            action="smoke_emergency_close_long", reduce_only=True)
            except Exception:
                LOG.exception("smoke_emergency_close_failed")
        raise
