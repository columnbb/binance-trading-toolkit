"""驗證三個還沒被 ``run_smoke_test()`` 涵蓋到的真實 API 行為：調整槓桿／
保證金模式、真的下一張限價單再用 ``cancel_order()``（非 algo 端點）取消、
以及三種常見錯誤情境的真實回應格式（低於 ``MIN_NOTIONAL``、價格精度錯誤、
保證金不足）。

跟 ``run_smoke_test()`` 一樣的安全設計：``confirm`` 沒有預設值，必須明確
傳 ``True``；執行前檢查帳上沒有既有部位；限價單一律掛在故意不會成交的
價位（BUY 掛在市價一半以下，SELL 掛在市價兩倍以上），把「成交風險」直接
歸零，剩下的唯一風險是「掛單後取消失敗」，所以 finally 區塊會強制再檢查
一次未平倉委託並嘗試清乾淨。

跟本檔案要驗證的 Gate 1 待辦項目一一對應：
  1. ``change_leverage``/``change_margin_type`` 對真實 API 送過請求
  2. ``cancel_order``（一般單，非 algo）對真實 API 驗證
  3. 真實錯誤情境的回應格式：保證金不足、低於 ``MIN_NOTIONAL``、精度錯誤
"""

from __future__ import annotations

import logging
import math
import uuid
from typing import Any

from .client import BinanceFuturesClient, extract_filters
from .ledger import TradeLedger

LOG = logging.getLogger(__name__)


class Gate1ValidationAborted(RuntimeError):
    """執行前的安全檢查沒過，直接拒絕送出任何訂單。"""


def _record_error_case(
    ledger: TradeLedger, *, case: str, symbol: str, side: str, quantity: float, price: float,
    client: BinanceFuturesClient, position_side: str | None,
) -> dict[str, Any]:
    """送出一張預期會被拒絕的限價單，記錄真實回應。若交易所意外接受了這張單
    （代表這次的參數其實沒觸發預期的錯誤），立刻嘗試取消，並把這個意外標記
    在紀錄裡而不是靜默吞掉——這種「以為會失敗結果成功」的落差本身就是重要
    資訊，不該被隱藏。"""
    attempt = ledger.order_attempt(
        action=f"gate1_error_case_{case}", symbol=symbol, side=side, volume=quantity, live=True,
        order_type="LIMIT", parameters={"price": price, "expected": "rejected"},
    )
    result = client.limit_order(symbol, side, quantity, price, position_side=position_side,
                                 new_client_order_id=f"gate1-{case}-{uuid.uuid4().hex[:16]}")
    ledger.order_result(
        attempt_event_id=attempt, action=f"gate1_error_case_{case}", symbol=symbol, side=side,
        volume=quantity, status="unexpectedly_accepted" if result.ok else "rejected_as_expected",
        response=result.raw, error=result.error or None, order_id=result.order_id,
    )
    if result.ok and result.order_id:
        LOG.warning("gate1_error_case_unexpectedly_accepted case=%s order_id=%s — cancelling", case, result.order_id)
        cancel = client.cancel_order(symbol, result.order_id)
        ledger.append("gate1_error_case_cleanup_cancel", case=case, order_id=result.order_id,
                       cancelled=cancel.ok, error=cancel.error or None)
    return {"case": case, "unexpectedly_accepted": result.ok, "error": result.error, "raw": result.raw}


def run_gate1_validation(
    client: BinanceFuturesClient, ledger: TradeLedger, *, symbol: str, confirm: bool,
    leverage: int = 5, margin_type: str = "ISOLATED", position_side: str | None = None,
) -> dict[str, Any]:
    """跑一輪帳戶設定＋真實限價單取消＋三種錯誤情境驗證，回傳每一步的結果。

    ``leverage``/``margin_type`` 預設值刻意對齊 ed-seykota 積極版設定
    （5 倍、逐倉）——正常情況下這兩個呼叫應該是冪等的（帳上已經是這個值），
    不會改變任何真實曝險。
    """
    if not confirm:
        raise Gate1ValidationAborted("安全檢查未通過：confirm 必須明確傳 True 才會送出任何真實請求")

    positions = client.open_positions(symbol)
    if positions:
        raise Gate1ValidationAborted(f"安全檢查未通過：{symbol} 帳上已經有未平倉部位，拒絕執行：{positions}")

    filters_raw = client.symbol_filters(symbol)
    tick_size, step_size, min_notional = extract_filters(filters_raw)
    price = client.ticker_price(symbol)
    if price <= 0:
        raise Gate1ValidationAborted(f"{symbol} 的即時報價異常：{price}")

    results: dict[str, Any] = {"symbol": symbol}

    # ------------------------------------------------------------------
    # 1. 槓桿／保證金模式——不涉及下單，冪等操作
    # ------------------------------------------------------------------
    lev_attempt = ledger.order_attempt(action="gate1_change_leverage", symbol=symbol, side="", volume=0,
                                        live=True, parameters={"leverage": leverage})
    try:
        lev_raw = client.change_leverage(symbol, leverage)
        ledger.order_result(attempt_event_id=lev_attempt, action="gate1_change_leverage", symbol=symbol,
                             side="", volume=0, status="ok", response=lev_raw)
        results["change_leverage"] = {"ok": True, "raw": lev_raw}
    except Exception as exc:
        ledger.order_result(attempt_event_id=lev_attempt, action="gate1_change_leverage", symbol=symbol,
                             side="", volume=0, status="failed", error=str(exc))
        results["change_leverage"] = {"ok": False, "error": str(exc)}

    margin_attempt = ledger.order_attempt(action="gate1_change_margin_type", symbol=symbol, side="", volume=0,
                                           live=True, parameters={"margin_type": margin_type})
    try:
        margin_raw = client.change_margin_type(symbol, margin_type)
        ledger.order_result(attempt_event_id=margin_attempt, action="gate1_change_margin_type", symbol=symbol,
                             side="", volume=0, status="ok", response=margin_raw)
        results["change_margin_type"] = {"ok": True, "raw": margin_raw}
    except Exception as exc:
        # 已經是目標保證金模式時，Binance 會回錯誤（"No need to change margin type"）
        # ——這是預期中的正常結果，不是失敗，照樣記錄原始訊息方便之後查證格式。
        ledger.order_result(attempt_event_id=margin_attempt, action="gate1_change_margin_type", symbol=symbol,
                             side="", volume=0, status="failed_or_already_set", error=str(exc))
        results["change_margin_type"] = {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # 2. 真的掛一張限價單，再用 cancel_order()（非 algo 端點）取消
    #    掛在市價的一半（BUY）——正常行情不可能瞬間腰斬，成交風險視為零。
    # ------------------------------------------------------------------
    safe_price = price * 0.5
    if tick_size:
        safe_price = math.floor(safe_price / tick_size) * tick_size
    # 20% buffer over min_notional, then round UP (not down) to the step
    # size — flooring here was the bug: for a coarse step_size relative to
    # price, flooring can undershoot back below min_notional (e.g. step=0.001
    # BTC at a ~$30k safe_price is a ~$30 jump per step, easily enough to
    # floor a $50-notional target down to $20-30 and get rejected).
    target_notional = max(min_notional or 0.0, 20.0) * 1.2
    quantity = target_notional / safe_price
    if step_size:
        quantity = math.ceil(quantity / step_size) * step_size

    cancel_order_id = None
    try:
        place_attempt = ledger.order_attempt(action="gate1_place_cancel_test_order", symbol=symbol, side="BUY",
                                              volume=quantity, live=True, order_type="LIMIT",
                                              parameters={"price": safe_price})
        placed = client.limit_order(symbol, "BUY", quantity, safe_price, position_side=position_side,
                                     new_client_order_id=f"gate1-cancel-{uuid.uuid4().hex[:16]}")
        if not placed.ok:
            ledger.order_result(attempt_event_id=place_attempt, action="gate1_place_cancel_test_order",
                                 symbol=symbol, side="BUY", volume=quantity, status="failed", error=placed.error)
            raise RuntimeError(f"驗證用限價單掛不上去：{placed.error}")
        cancel_order_id = placed.order_id
        ledger.order_result(attempt_event_id=place_attempt, action="gate1_place_cancel_test_order",
                             symbol=symbol, side="BUY", volume=quantity, status="accepted",
                             response=placed.raw, order_id=cancel_order_id)

        cancel_attempt = ledger.order_attempt(action="gate1_cancel_order", symbol=symbol, side="BUY", volume=0,
                                               live=True, parameters={"order_id": cancel_order_id})
        cancelled = client.cancel_order(symbol, cancel_order_id)
        ledger.order_result(attempt_event_id=cancel_attempt, action="gate1_cancel_order", symbol=symbol,
                             side="BUY", volume=0, status="ok" if cancelled.ok else "failed",
                             response=cancelled.raw, error=cancelled.error or None)
        results["cancel_order"] = {"ok": cancelled.ok, "raw": cancelled.raw, "error": cancelled.error}
        if cancelled.ok:
            cancel_order_id = None
    finally:
        # 保險：不管上面成功或丟例外，最後都再查一次未平倉委託，確認真的沒有
        # 殘留掛單——這是唯一一個真的有殘留掛單風險的步驟，比照
        # run_smoke_test() 的例外處理精神,寧可多查一次。
        if cancel_order_id is not None:
            try:
                client.cancel_order(symbol, cancel_order_id)
            except Exception:
                LOG.exception("gate1_cancel_cleanup_failed order_id=%s — 請人工到交易所介面確認並取消", cancel_order_id)
        remaining = client.open_orders(symbol)
        if remaining:
            LOG.warning("gate1_validation_finished_with_open_orders symbol=%s remaining=%s", symbol, remaining)
            ledger.append("gate1_open_orders_remaining", symbol=symbol, remaining=remaining)

    # ------------------------------------------------------------------
    # 3. 三種預期會被拒絕的錯誤情境——全部掛在不可能成交的價位
    # ------------------------------------------------------------------
    error_cases: list[dict[str, Any]] = []

    if min_notional:
        # Keep price fixed at the already-safe (never-fills) level and shrink
        # quantity to force the notional below the threshold — NOT the other
        # way around. Raising price toward market to hit a notional target
        # would reintroduce fill risk on a BUY order, which defeats the
        # entire point of using `safe_price` in the first place.
        step = step_size or 0.001
        tiny_qty = math.floor((min_notional * 0.5 / safe_price) / step) * step
        if tiny_qty <= 0:
            LOG.warning("gate1_min_notional_case_skipped symbol=%s reason=step_size_too_coarse_to_go_below_min_notional_at_safe_price", symbol)
        else:
            error_cases.append(_record_error_case(
                ledger, case="below_min_notional", symbol=symbol, side="BUY",
                quantity=tiny_qty, price=safe_price, client=client, position_side=position_side,
            ))

    if tick_size:
        bad_precision_price = safe_price + tick_size / 3
        error_cases.append(_record_error_case(
            ledger, case="price_precision", symbol=symbol, side="BUY",
            quantity=step_size or 0.001, price=bad_precision_price, client=client, position_side=position_side,
        ))

    balances = client.account_balance()
    available = 0.0
    for b in balances:
        if b.get("asset") == "USDT":
            available = float(b.get("availableBalance", 0) or 0)
            break
    oversized_notional = max(available * 100.0, 1_000_000.0)
    oversized_qty = oversized_notional / safe_price
    if step_size:
        oversized_qty = math.floor(oversized_qty / step_size) * step_size
    error_cases.append(_record_error_case(
        ledger, case="insufficient_margin", symbol=symbol, side="BUY",
        quantity=oversized_qty, price=safe_price, client=client, position_side=position_side,
    ))

    results["error_cases"] = error_cases
    LOG.info("gate1_validation_complete symbol=%s results=%s", symbol, results)
    return results
