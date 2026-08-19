"""report.py 的測試 —— 純資料轉換，不打網路。"""

from __future__ import annotations

import pytest

from binance_trading_toolkit.ledger import TradeLedger
from binance_trading_toolkit.report import generate_report, load_events


class TestLoadEvents:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_events(str(tmp_path / "nope.jsonl")) == []

    def test_round_trips_ledger_output(self, tmp_path):
        ledger = TradeLedger(str(tmp_path / "ledger.jsonl"))
        ledger.append("order_attempt", symbol="BTCUSDT")
        events = load_events(str(tmp_path / "ledger.jsonl"))
        assert len(events) == 1
        assert events[0]["symbol"] == "BTCUSDT"

    def test_corrupt_line_raises_with_line_number(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        path.write_text('{"event_type": "order_attempt"}\nnot json\n', encoding="utf-8")
        with pytest.raises(ValueError, match="第 2 行"):
            load_events(str(path))


class TestGenerateReport:
    def test_empty_ledger_reports_zero_trades(self):
        report = generate_report([])
        assert "已完成交易數 | 0" in report
        assert "尚無已完成交易" in report

    def test_summarises_wins_losses_and_pnl(self):
        events = [
            {"event_type": "trade_close", "trade_id": "a" * 20, "symbol": "BTCUSDT",
             "entry_price": 100.0, "exit_price": 110.0, "exit_volume": 1.0,
             "net_pnl": 9.0, "gross_pnl": 10.0, "total_fees": 1.0, "source": "test"},
            {"event_type": "trade_close", "trade_id": "b" * 20, "symbol": "BTCUSDT",
             "entry_price": 100.0, "exit_price": 95.0, "exit_volume": 1.0,
             "net_pnl": -6.0, "gross_pnl": -5.0, "total_fees": 1.0, "source": "test"},
        ]
        report = generate_report(events, generated_at="2026-08-20T00:00:00+00:00")
        assert "已完成交易數 | 2" in report
        assert "獲利交易數 | 1" in report
        assert "虧損交易數 | 1" in report
        assert "淨損益（USDT） | **3.000000**" in report
        assert "aaaaaaaaaaaa" in report

    def test_counts_failures_and_dry_runs_separately_from_closes(self):
        events = [
            {"event_type": "order_result", "status": "failed"},
            {"event_type": "order_result", "status": "failed"},
            {"event_type": "order_result", "status": "dry_run_skipped"},
        ]
        report = generate_report(events)
        assert "下單失敗事件 | 2" in report
        assert "dry-run 未送單事件 | 1" in report
