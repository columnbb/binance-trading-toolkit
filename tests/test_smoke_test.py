"""smoke_test.py 的測試。

重點：安全檢查要在**送出任何訂單之前**就擋下來。用假的 client 確保
「該擋下來」的案例完全不會呼叫到任何下單方法。
"""

from __future__ import annotations

import pytest

from binance_trading_toolkit.ledger import TradeLedger
from decimal import ROUND_FLOOR, Decimal

from binance_trading_toolkit.smoke_test import (
    SmokeTestAborted, quantity_for_notional, run_smoke_test, stop_price_below_entry,
)


class TestQuantityForNotional:
    def test_basic_conversion(self):
        assert quantity_for_notional(0.0001, price=50000.0, notional=100.0) == pytest.approx(0.002)

    def test_rounds_down_to_step(self):
        result = quantity_for_notional(0.001, price=67821.9, notional=100.0)
        assert result == pytest.approx(0.001)

    def test_zero_step_passes_through(self):
        assert quantity_for_notional(0, price=100.0, notional=50.0) == pytest.approx(0.5)

    def test_an_aligned_quantity_is_not_floored_a_lot(self):
        # math.floor(0.043 / 0.001) * 0.001 is 0.042: the old float floor dropped a lot off an aligned quantity
        assert quantity_for_notional(0.001, price=1.0, notional=0.043) == 0.043
        assert quantity_for_notional(0.0001, price=1.0, notional=0.0589) == 0.0589


class TestStopPriceBelowEntry:
    @staticmethod
    def _exact(entry: float, offset: float, tick: float) -> float:
        stop = Decimal(repr(entry)) * (1 - Decimal(repr(offset)))
        return float((stop / Decimal(repr(tick))).to_integral_value(rounding=ROUND_FLOOR) * Decimal(repr(tick)))

    def test_matches_exact_decimal_arithmetic_over_a_whole_range_of_btc_entries(self):
        for k in range(300000, 900000, 7):          # 30,000.0 .. 90,000.0 in steps of 0.7
            entry = k / 10
            assert stop_price_below_entry(entry, 0.05, 0.1) == self._exact(entry, 0.05, 0.1), entry

    def test_the_price_is_clean_not_float_noise(self):
        # 30000.7 * 0.95 floored the old way gave 28500.600000000002
        assert stop_price_below_entry(30000.7, 0.05, 0.1) == 28500.6

    def test_never_above_the_exact_stop_and_below_entry(self):
        for entry in (30000.7, 81639.9, 85721.9):
            stop = stop_price_below_entry(entry, 0.05, 0.1)
            assert stop < entry and stop <= entry * 0.95 + 1e-9

    def test_no_tick_size_keeps_the_exact_product(self):
        assert stop_price_below_entry(100.0, 0.05, None) == 95.0


class FakeClient:
    def __init__(self, positions=None, filters=None, price=50000.0):
        self._positions = positions or []
        self._filters = filters or {"filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "50.0"},
        ]}
        self._price = price
        self.calls = []

    def open_positions(self, symbol=None):
        self.calls.append("open_positions")
        return self._positions

    def symbol_filters(self, symbol):
        self.calls.append(("symbol_filters", symbol))
        return self._filters

    def ticker_price(self, symbol):
        self.calls.append(("ticker_price", symbol))
        return self._price

    def market_order(self, *args, **kwargs):
        self.calls.append("market_order")
        raise AssertionError("should never place an order once a safety check should have aborted first")

    def stop_market_close_position(self, *args, **kwargs):
        self.calls.append("stop_market_close_position")
        raise AssertionError("should never place a stop once a safety check should have aborted first")


@pytest.fixture
def ledger(tmp_path):
    return TradeLedger(str(tmp_path / "ledger.jsonl"))


class TestSafetyGates:
    def test_confirm_false_aborts_before_any_call(self, ledger):
        client = FakeClient()
        with pytest.raises(SmokeTestAborted, match="confirm"):
            run_smoke_test(client, ledger, symbol="BTCUSDT", confirm=False)
        assert client.calls == []

    def test_existing_open_position_aborts(self, ledger):
        client = FakeClient(positions=[{"symbol": "BTCUSDT", "positionAmt": "0.01"}])
        with pytest.raises(SmokeTestAborted, match="未平倉部位"):
            run_smoke_test(client, ledger, symbol="BTCUSDT", confirm=True)
        assert client.calls == ["open_positions"]

    def test_notional_cap_aborts(self, ledger):
        client = FakeClient(positions=[], price=100000.0)
        with pytest.raises(SmokeTestAborted, match="上限"):
            run_smoke_test(client, ledger, symbol="BTCUSDT", confirm=True,
                            notional_usdt=200.0, max_notional_usdt=150.0)

    def test_invalid_price_aborts(self, ledger):
        client = FakeClient(positions=[], price=0.0)
        with pytest.raises(SmokeTestAborted, match="報價"):
            run_smoke_test(client, ledger, symbol="BTCUSDT", confirm=True)

    def test_none_of_the_abort_paths_place_orders(self, ledger):
        client = FakeClient(positions=[{"symbol": "BTCUSDT", "positionAmt": "0.01"}])
        with pytest.raises(SmokeTestAborted):
            run_smoke_test(client, ledger, symbol="BTCUSDT", confirm=True)
        assert "market_order" not in client.calls
        assert "stop_market_close_position" not in client.calls
