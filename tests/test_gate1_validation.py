"""gate1_validation.py 的測試。

跟 smoke_test.py 的測試同一個精神：安全檢查要在送出任何真實請求之前就擋
下來，並且要驗證掛單一律用「不可能成交」的安全價位（BUY 掛在市價一半以
下），不是隨便選個數字。
"""

from __future__ import annotations

import pytest

from binance_trading_toolkit.gate1_validation import Gate1ValidationAborted, run_gate1_validation
from binance_trading_toolkit.ledger import TradeLedger


class FakeOrderResult:
    def __init__(self, ok=True, order_id=1, error="", raw=None):
        self.ok = ok
        self.order_id = order_id
        self.error = error
        self.raw = raw or {}


class FakeClient:
    def __init__(self, positions=None, filters=None, price=50000.0, balance_usdt=1000.0):
        self._positions = positions or []
        self._filters = filters or {"filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "50.0"},
        ]}
        self._price = price
        self._balance_usdt = balance_usdt
        self.calls: list = []
        self.limit_orders: list = []
        self.cancelled_order_ids: list = []
        self.next_order_id = 100
        self.limit_order_should_fail = False
        self.cancel_should_fail = False

    def open_positions(self, symbol=None):
        self.calls.append("open_positions")
        return self._positions

    def symbol_filters(self, symbol):
        self.calls.append(("symbol_filters", symbol))
        return self._filters

    def ticker_price(self, symbol):
        self.calls.append(("ticker_price", symbol))
        return self._price

    def change_leverage(self, symbol, leverage):
        self.calls.append(("change_leverage", symbol, leverage))
        return {"leverage": leverage, "symbol": symbol}

    def change_margin_type(self, symbol, margin_type):
        self.calls.append(("change_margin_type", symbol, margin_type))
        return {"code": 200, "msg": "success"}

    def limit_order(self, symbol, side, quantity, price, *, position_side=None, new_client_order_id=None):
        self.limit_orders.append({"side": side, "quantity": quantity, "price": price,
                                   "new_client_order_id": new_client_order_id})
        if self.limit_order_should_fail:
            return FakeOrderResult(ok=False, error="rejected as configured by test")
        self.next_order_id += 1
        return FakeOrderResult(ok=True, order_id=self.next_order_id, raw={"orderId": self.next_order_id})

    def cancel_order(self, symbol, order_id):
        self.cancelled_order_ids.append(order_id)
        if self.cancel_should_fail:
            return FakeOrderResult(ok=False, error="cancel failed as configured by test")
        return FakeOrderResult(ok=True, order_id=order_id)

    def open_orders(self, symbol=None):
        return []

    def account_balance(self):
        return [{"asset": "USDT", "availableBalance": str(self._balance_usdt)}]

    def market_order(self, *args, **kwargs):
        raise AssertionError("gate1 validation should never place a market order")


@pytest.fixture
def ledger(tmp_path):
    return TradeLedger(str(tmp_path / "ledger.jsonl"))


class TestSafetyGates:
    def test_confirm_false_aborts_before_any_call(self, ledger):
        client = FakeClient()
        with pytest.raises(Gate1ValidationAborted, match="confirm"):
            run_gate1_validation(client, ledger, symbol="BTCUSDT", confirm=False)
        assert client.calls == []

    def test_existing_open_position_aborts(self, ledger):
        client = FakeClient(positions=[{"symbol": "BTCUSDT", "positionAmt": "0.01"}])
        with pytest.raises(Gate1ValidationAborted, match="未平倉部位"):
            run_gate1_validation(client, ledger, symbol="BTCUSDT", confirm=True)
        assert client.calls == ["open_positions"]
        assert client.limit_orders == []

    def test_invalid_price_aborts(self, ledger):
        client = FakeClient(positions=[], price=0.0)
        with pytest.raises(Gate1ValidationAborted, match="報價"):
            run_gate1_validation(client, ledger, symbol="BTCUSDT", confirm=True)
        assert client.limit_orders == []


class TestOrderPricingNeverRisksAFill:
    """The one property that matters most: every limit order this module ever
    sends must be priced where a BUY genuinely cannot fill."""

    def test_all_orders_priced_at_or_below_half_market(self, ledger):
        client = FakeClient(positions=[], price=50000.0)
        run_gate1_validation(client, ledger, symbol="BTCUSDT", confirm=True)
        safe_ceiling = 50000.0 * 0.5
        assert client.limit_orders, "expected at least the cancel-test order to be placed"
        for order in client.limit_orders:
            assert order["side"] == "BUY"
            assert order["price"] <= safe_ceiling + 0.5  # small tolerance for the precision-error case's tick offset

    def test_below_min_notional_case_shrinks_quantity_not_raises_price(self, ledger):
        client = FakeClient(positions=[], price=50000.0)
        run_gate1_validation(client, ledger, symbol="BTCUSDT", confirm=True)
        safe_price = 50000.0 * 0.5
        # None of the recorded order prices should exceed the safe ceiling —
        # in particular the min-notional case must not have pushed price up
        # to compensate for a small quantity.
        assert all(o["price"] <= safe_price + 0.5 for o in client.limit_orders)


class TestClientOrderIdLength:
    """Regression test for a real bug hit during a live demo-account run: a
    client_order_id longer than Binance's 36-char limit made every error-case
    order get rejected for the wrong reason (id-too-long), masking the actual
    error the case was meant to trigger."""

    def test_every_generated_client_order_id_is_36_chars_or_fewer(self, ledger):
        client = FakeClient(positions=[])
        run_gate1_validation(client, ledger, symbol="BTCUSDT", confirm=True)
        assert client.limit_orders, "expected at least one limit order to be placed"
        for order in client.limit_orders:
            client_order_id = order["new_client_order_id"]
            assert client_order_id is not None
            assert len(client_order_id) <= 36, f"{client_order_id!r} is {len(client_order_id)} chars, exceeds Binance's limit"


class TestCancelOrderFlow:
    def test_places_then_cancels_via_cancel_order_not_algo(self, ledger):
        client = FakeClient(positions=[])
        result = run_gate1_validation(client, ledger, symbol="BTCUSDT", confirm=True)
        assert client.cancelled_order_ids, "cancel_order should have been called at least once"
        assert result["cancel_order"]["ok"] is True

    def test_leftover_order_id_is_retried_in_finally_block_on_cancel_failure(self, ledger):
        client = FakeClient(positions=[])
        client.cancel_should_fail = True
        run_gate1_validation(client, ledger, symbol="BTCUSDT", confirm=True)
        # First attempt (main flow) + retry in the finally block.
        assert len(client.cancelled_order_ids) >= 2

    def test_cancel_test_order_notional_clears_min_notional_after_step_rounding(self, ledger):
        """Regression test for a real bug hit during a live demo-account run:
        flooring the quantity to a coarse step_size undershot back below
        min_notional and Binance rejected the order (HTTP 400 'notional must
        be no smaller than 50'). Must round up, with margin, instead."""
        client = FakeClient(positions=[], price=66000.0, filters={"filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "50.0"},
        ]})
        run_gate1_validation(client, ledger, symbol="BTCUSDT", confirm=True)
        cancel_test_order = client.limit_orders[0]
        assert cancel_test_order["quantity"] * cancel_test_order["price"] >= 50.0


class TestLeverageAndMarginType:
    def test_calls_both_with_configured_values(self, ledger):
        client = FakeClient(positions=[])
        result = run_gate1_validation(client, ledger, symbol="BTCUSDT", confirm=True,
                                       leverage=5, margin_type="ISOLATED")
        assert ("change_leverage", "BTCUSDT", 5) in client.calls
        assert ("change_margin_type", "BTCUSDT", "ISOLATED") in client.calls
        assert result["change_leverage"]["ok"] is True
        assert result["change_margin_type"]["ok"] is True

    def test_margin_type_failure_does_not_abort_the_rest(self, ledger):
        class FailingMarginClient(FakeClient):
            def change_margin_type(self, symbol, margin_type):
                raise RuntimeError("No need to change margin type.")

        client = FailingMarginClient(positions=[])
        result = run_gate1_validation(client, ledger, symbol="BTCUSDT", confirm=True)
        assert result["change_margin_type"]["ok"] is False
        # Should still proceed to the cancel-order and error-case steps.
        assert "cancel_order" in result
        assert result["error_cases"]
