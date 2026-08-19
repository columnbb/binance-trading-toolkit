"""TradeLedger 的測試 —— 純本機檔案 I/O，不打網路。"""

from __future__ import annotations

import json

import pytest

from binance_trading_toolkit.ledger import TradeLedger


@pytest.fixture
def ledger(tmp_path):
    return TradeLedger(str(tmp_path / "ledger.jsonl"))


class TestAppend:
    def test_creates_parent_directory(self, tmp_path):
        ledger = TradeLedger(str(tmp_path / "nested" / "dir" / "ledger.jsonl"))
        ledger.append("order_attempt", symbol="BTCUSDT")
        assert ledger.path.exists()

    def test_appends_jsonl_rows(self, ledger):
        ledger.append("order_attempt", symbol="BTCUSDT")
        ledger.append("order_result", symbol="BTCUSDT")
        lines = ledger.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event_type"] == "order_attempt"


class TestTradeClose:
    def test_gross_pnl_long_formula(self, ledger):
        ledger.trade_close(
            trade_id="t1", symbol="BTCUSDT", entry_volume=0.01, exit_volume=0.01,
            entry_price=60000.0, exit_price=61000.0, contract_size=1.0, leverage=1,
            entry_fee=0.0, exit_fee=0.0, order_id="1", source="test",
        )
        row = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[0])
        assert row["gross_pnl"] == pytest.approx(10.0)
        assert row["net_pnl"] == pytest.approx(10.0)

    def test_net_pnl_subtracts_fees(self, ledger):
        ledger.trade_close(
            trade_id="t1", symbol="BTCUSDT", entry_volume=1, exit_volume=1,
            entry_price=100.0, exit_price=110.0, contract_size=1.0, leverage=1,
            entry_fee=0.5, exit_fee=0.5, order_id="1", source="test",
        )
        row = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[0])
        assert row["net_pnl"] == pytest.approx(9.0)

    def test_return_on_margin_uses_leverage(self, ledger):
        ledger.trade_close(
            trade_id="t1", symbol="BTCUSDT", entry_volume=1, exit_volume=1,
            entry_price=100.0, exit_price=110.0, contract_size=1.0, leverage=2,
            entry_fee=0.0, exit_fee=0.0, order_id="1", source="test",
        )
        row = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[0])
        assert row["return_on_margin"] == pytest.approx(0.2)

    def test_zero_leverage_gives_no_margin_ratio(self, ledger):
        ledger.trade_close(
            trade_id="t1", symbol="BTCUSDT", entry_volume=1, exit_volume=1,
            entry_price=100.0, exit_price=110.0, contract_size=1.0, leverage=0,
            entry_fee=0.0, exit_fee=0.0, order_id="1", source="test",
        )
        row = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[0])
        assert row["return_on_margin"] is None
