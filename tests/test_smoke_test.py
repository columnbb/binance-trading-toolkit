"""smoke_test.py 的測試。

重點：安全檢查要在**送出任何訂單之前**就擋下來。用假的 client 確保
「該擋下來」的案例完全不會呼叫到任何下單方法。
"""

from __future__ import annotations

import pytest

from binance_trading_toolkit.ledger import TradeLedger
from binance_trading_toolkit.smoke_test import SmokeTestAborted, quantity_for_notional, run_smoke_test


class TestQuantityForNotional:
    def test_basic_conversion(self):
        assert quantity_for_notional(0.0001, price=50000.0, notional=100.0) == pytest.approx(0.002)

    def test_rounds_down_to_step(self):
        result = quantity_for_notional(0.001, price=67821.9, notional=100.0)
        assert result == pytest.approx(0.001)

    def test_zero_step_passes_through(self):
        assert quantity_for_notional(0, price=100.0, notional=50.0) == pytest.approx(0.5)


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
