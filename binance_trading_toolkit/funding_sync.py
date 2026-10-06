"""定期查詢並記錄資金費結算（funding fee）到本機帳本——Binance 版。

跟 ``mexc-futures-toolkit`` 的 ``funding_sync.sync_funding_fees`` 同一個
介面與同一種帳本事件（``event_type="funding_fee"``，``funding`` 欄位加總
就是資金費淨額），差別只在資料來源：Binance 把資金費放在
``GET /fapi/v1/income?incomeType=FUNDING_FEE``，不是成交紀錄。

只負責「查、去重、寫進帳本」；多久跑一次是呼叫端 systemd timer 的事。
符號層級記錄，不嘗試歸屬到特定 trade_id（一次持倉可能跨好幾次結算）。

``income``：正＝收到、負＝付出。分頁用固定時間窗加 ``page``，起點含頭並靠
``record_id`` 去重，寫入前先整批驗證（見 ``sync_funding_fees``）。官方只回最近 3 個月，所以第一次執行的
預設回看是 85 天（留一點餘裕給時間差），不是 MEXC 版的 30 天——這樣
上線前已經累積、但從沒被記過的資金費也補得回來（補得回來的前提是還在
3 個月內）。非 USDT 計價的列照記但標出 ``asset``，不會被當成 USDT 加總。
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from typing import Any

from .client import BinanceFuturesClient
from .ledger import TradeLedger

DEFAULT_LOOKBACK_MS = 85 * 24 * 60 * 60 * 1000
PAGE_SIZE = 1000
MAX_PAGES = 1000  # safety valve: more than this means something is wrong, not "keep going"
SOURCE = "binance_income"


def _iso_from_epoch_ms(value: Any) -> str | None:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return None
    if milliseconds <= 0:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _record_id(row: dict[str, Any], symbol: str) -> str:
    """tranId 在不同收益類型之間會重複（同一筆成交的手續費與已實現損益共用），
    所以用 symbol＋時間＋tranId 組成去重鍵，不單靠 tranId。"""
    return f"{row.get('symbol') or symbol}:{row.get('time')}:{row.get('tranId')}"


def _existing_funding_state(ledger: TradeLedger, symbol: str) -> tuple[set[Any], int | None]:
    existing_ids: set[Any] = set()
    max_settle_ms: int | None = None
    if not ledger.path.exists():
        return existing_ids, max_settle_ms
    for line in ledger.path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event_type") != "funding_fee" or event.get("symbol") != symbol:
            continue
        if event.get("source") != SOURCE:
            continue
        record_id = event.get("record_id")
        if record_id is not None:
            existing_ids.add(record_id)
        settle_ms = event.get("settle_time_ms")
        if isinstance(settle_ms, (int, float)) and (max_settle_ms is None or settle_ms > max_settle_ms):
            max_settle_ms = int(settle_ms)
    return existing_ids, max_settle_ms


def _fetch_window(client, symbol: str, start_time_ms: int, end_time_ms: int, page_size: int) -> list[dict[str, Any]]:
    """同一個固定時間窗用 ``page`` 往後翻到最後一頁；整個窗口的資料都拿到才回傳。
    不把 startTime 往前推——同一毫秒可以有好幾列，往前推會漏掉同毫秒尾端。"""
    rows: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        got = client.income_history(
            income_type="FUNDING_FEE", symbol=symbol,
            start_time_ms=start_time_ms, end_time_ms=end_time_ms, limit=page_size, page=page,
        )
        rows.extend(got)
        if len(got) < page_size:
            return rows
    raise RuntimeError(f"funding income for {symbol} exceeds {MAX_PAGES} pages; refusing a partial sync")


def _validate(row: Any, symbol: str) -> tuple[str, float, int]:
    """寫入前驗證；任何一列壞掉整輪就不寫。回傳 (record_id, funding, settle_ms)。"""
    if not isinstance(row, dict):
        raise ValueError(f"unreadable funding row for {symbol}: {row!r}")
    settle_ms = row.get("time")
    if isinstance(settle_ms, bool) or not isinstance(settle_ms, (int, float)) or not math.isfinite(settle_ms) or settle_ms <= 0:
        raise ValueError(f"unreadable funding time {settle_ms!r} for {symbol}")
    try:
        funding = float(row.get("income"))
    except (TypeError, ValueError):
        raise ValueError(f"unreadable funding income {row.get('income')!r} for {symbol}")
    if not math.isfinite(funding):
        raise ValueError(f"non-finite funding income {row.get('income')!r} for {symbol}")
    return _record_id(row, symbol), funding, int(settle_ms)


def sync_funding_fees(
    client: BinanceFuturesClient, ledger: TradeLedger, symbol: str,
    *, lookback_ms: int = DEFAULT_LOOKBACK_MS, now_ms: int | None = None,
    page_size: int = PAGE_SIZE,
) -> int:
    """查詢自上次記錄以來的新資金費結算，寫進帳本，回傳新寫入的筆數。

    三個保證：
    1. 整個時間窗（用 ``page`` 翻頁）全部取回、每一列都驗證通過才開始寫；
       任何一列壞掉（金額不是有限數字、時間不可讀）整輪丟 ValueError、什麼都不寫。
    2. 起點是已記錄的最大結算時間「本身」（含頭），不是＋1：同一毫秒還沒記到的
       列下次會再被查到，靠 ``record_id`` 去重；寫入按時間由舊到新，所以中途被
       中斷時，沒寫到的列時間一定不小於已寫的最大時間，重跑補得回來。
    3. 只認 ``source="binance_income"`` 的既有事件決定起點，別的來源的列不會把
       起點推到未來。
    """
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    existing_ids, max_settle_ms = _existing_funding_state(ledger, symbol)
    start_time_ms = max_settle_ms if max_settle_ms is not None else (now_ms - lookback_ms)

    rows = _fetch_window(client, symbol, start_time_ms, now_ms, page_size)
    pending: list[tuple[int, str, float, dict[str, Any]]] = []
    seen = set(existing_ids)
    for row in rows:
        if isinstance(row, dict) and row.get("incomeType") not in (None, "FUNDING_FEE"):
            continue
        record_id, funding, settle_ms = _validate(row, symbol)
        if record_id in seen:
            continue
        seen.add(record_id)
        pending.append((settle_ms, record_id, funding, row))

    pending.sort(key=lambda item: (item[0], item[1]))
    for settle_ms, record_id, funding, row in pending:
        ledger.append(
            "funding_fee",
            record_id=record_id,
            symbol=row.get("symbol") or symbol,
            funding=funding,
            asset=row.get("asset"),
            tran_id=row.get("tranId"),
            settle_time_ms=settle_ms,
            settle_time=_iso_from_epoch_ms(settle_ms),
            source=SOURCE,
        )
    return len(pending)


def total_funding_fee(ledger_path: str, *, asset: str = "USDT") -> float:
    """帳本裡該資產的資金費淨額（只加總 ``asset`` 吻合或未標資產的列）。"""
    total = 0.0
    path = TradeLedger(ledger_path).path
    if not path.exists():
        return total
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event_type") != "funding_fee":
            continue
        if event.get("asset") not in (None, asset):
            continue
        total += float(event.get("funding") or 0.0)
    return total
