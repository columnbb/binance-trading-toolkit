"""Binance 版資金費同步的測試 —— 不打網路，用假客戶端回放收益紀錄。"""

from __future__ import annotations

import json

import pytest

from binance_trading_toolkit.funding_sync import (
    DEFAULT_LOOKBACK_MS,
    PAGE_SIZE,
    sync_funding_fees,
    total_funding_fee,
)
from binance_trading_toolkit.ledger import TradeLedger

NOW = 1_800_000_000_000


def _row(time_ms, income="-0.01", tran=1, symbol="BTCUSDT", income_type="FUNDING_FEE", asset="USDT"):
    return {"symbol": symbol, "incomeType": income_type, "income": income, "asset": asset,
            "info": "FUNDING_FEE", "time": time_ms, "tranId": tran, "tradeId": ""}


class FakeClient:
    """依 startTime／endTime 過濾，模擬 Binance 由舊到新、單頁上限的行為。"""

    def __init__(self, rows, page_size=PAGE_SIZE):
        self.rows = sorted(rows, key=lambda r: r["time"])
        self.page_size = page_size
        self.calls = []

    def income_history(self, *, income_type=None, symbol=None, start_time_ms=None,
                       end_time_ms=None, limit=1000):
        self.calls.append({"income_type": income_type, "symbol": symbol,
                           "start": start_time_ms, "end": end_time_ms, "limit": limit})
        out = [r for r in self.rows
               if (symbol is None or r["symbol"] == symbol)
               and (income_type is None or r["incomeType"] == income_type)
               and (start_time_ms is None or r["time"] >= start_time_ms)
               and (end_time_ms is None or r["time"] <= end_time_ms)]
        return out[: min(limit, self.page_size)]


@pytest.fixture
def ledger(tmp_path):
    return TradeLedger(str(tmp_path / "ledger.jsonl"))


def _events(ledger):
    return [json.loads(l) for l in ledger.path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_writes_one_event_per_settlement_with_signed_amount(ledger):
    client = FakeClient([_row(NOW - 5000, "-0.0123", tran=11), _row(NOW - 4000, "0.0456", tran=12)])
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 2
    events = _events(ledger)
    assert [e["funding"] for e in events] == [-0.0123, 0.0456]
    assert all(e["event_type"] == "funding_fee" and e["source"] == "binance_income" for e in events)
    assert events[0]["symbol"] == "BTCUSDT" and events[0]["asset"] == "USDT"
    assert events[0]["settle_time_ms"] == NOW - 5000
    assert events[0]["settle_time"].endswith("Z")


def test_second_run_writes_nothing_and_resumes_after_last_settlement(ledger):
    client = FakeClient([_row(NOW - 5000, tran=11)])
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 1
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 0
    assert client.calls[-1]["start"] == NOW - 5000 + 1
    assert len(_events(ledger)) == 1


def test_first_run_looks_back_default_window(ledger):
    client = FakeClient([])
    sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW)
    assert client.calls[0]["start"] == NOW - DEFAULT_LOOKBACK_MS
    assert client.calls[0]["income_type"] == "FUNDING_FEE" and client.calls[0]["symbol"] == "BTCUSDT"
    assert DEFAULT_LOOKBACK_MS < 90 * 24 * 3600 * 1000  # Binance only serves ~3 months


def test_same_settlement_returned_twice_is_not_duplicated(ledger):
    client = FakeClient([_row(NOW - 100, tran=7)])
    sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW)
    # start boundary overlap: re-serve the same row on a later run
    client.rows.append(dict(client.rows[0]))
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW, lookback_ms=10**9) == 0


def test_same_tran_id_at_different_times_or_symbols_are_distinct(ledger):
    rows = [_row(NOW - 300, tran=5), _row(NOW - 200, tran=5), _row(NOW - 300, tran=5, symbol="ETHUSDT")]
    client = FakeClient(rows)
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 2
    assert sync_funding_fees(client, ledger, "ETHUSDT", now_ms=NOW) == 1


def test_pages_forward_when_a_page_is_full(ledger):
    rows = [_row(NOW - 10_000 + i * 10, tran=100 + i) for i in range(5)]
    client = FakeClient(rows, page_size=2)
    import binance_trading_toolkit.funding_sync as mod
    old = mod.PAGE_SIZE
    mod.PAGE_SIZE = 2
    try:
        assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 5
    finally:
        mod.PAGE_SIZE = old
    assert len(client.calls) == 3
    assert [e["tran_id"] for e in _events(ledger)] == [100, 101, 102, 103, 104]


def test_ignores_other_income_types(ledger):
    client = FakeClient([_row(NOW - 100, income_type="COMMISSION", tran=1), _row(NOW - 90, tran=2)])
    # fake filters by type, but a stray row must still be skipped by the module
    client.income_history = lambda **kw: [_row(NOW - 100, income_type="COMMISSION", tran=1), _row(NOW - 90, tran=2)]
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 1


def test_unreadable_amount_raises_instead_of_writing_zero(ledger):
    client = FakeClient([_row(NOW - 100, income="n/a")])
    with pytest.raises(ValueError):
        sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW)
    assert not ledger.path.exists() or _events(ledger) == []


def test_other_sources_do_not_push_the_start_time_forward(ledger):
    ledger.append("funding_fee", record_id=1, symbol="BTCUSDT", funding=-1.0,
                  settle_time_ms=NOW + 10**9)  # an old-exchange row dated "in the future"
    client = FakeClient([_row(NOW - 100, tran=9)])
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 1


def test_total_sums_only_usdt_funding(ledger):
    sync_funding_fees(FakeClient([_row(NOW - 3, "-0.5", tran=1), _row(NOW - 2, "0.2", tran=2),
                                   _row(NOW - 1, "9.0", tran=3, asset="BNB")]),
                      ledger, "BTCUSDT", now_ms=NOW)
    ledger.append("trade_open", trade_id="t", symbol="BTCUSDT")
    assert total_funding_fee(str(ledger.path)) == pytest.approx(-0.3)
    assert total_funding_fee(str(ledger.path.parent / "missing.jsonl")) == 0.0
