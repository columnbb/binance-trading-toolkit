"""Binance 版資金費同步的測試 —— 不打網路，用假客戶端回放收益紀錄。"""

from __future__ import annotations

import json
import math

import pytest

import binance_trading_toolkit.funding_sync as mod
from binance_trading_toolkit.funding_sync import (
    DEFAULT_LOOKBACK_MS,
    FundingLedgerError,
    sync_funding_fees,
    total_funding_fee,
)
from binance_trading_toolkit.ledger import TradeLedger

NOW = 1_800_000_000_000


def _row(time_ms, income="-0.01", tran=1, symbol="BTCUSDT", income_type="FUNDING_FEE", asset="USDT"):
    return {"symbol": symbol, "incomeType": income_type, "income": income, "asset": asset,
            "info": "FUNDING_FEE", "time": time_ms, "tranId": tran, "tradeId": ""}


class FakeClient:
    """依 startTime／endTime（含頭尾）過濾、再用 page／limit 切頁，
    由舊到新排列——跟 Binance 的行為一致（含「同一毫秒可以有很多列」）。"""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def income_history(self, *, income_type=None, symbol=None, start_time_ms=None,
                       end_time_ms=None, limit=1000, page=None):
        self.calls.append({"income_type": income_type, "symbol": symbol,
                           "start": start_time_ms, "end": end_time_ms, "limit": limit, "page": page})
        out = sorted(
            (r for r in self.rows
             if (symbol is None or r["symbol"] == symbol)
             and (income_type is None or r["incomeType"] == income_type)
             and (start_time_ms is None or r["time"] >= start_time_ms)
             and (end_time_ms is None or r["time"] <= end_time_ms)),
            key=lambda r: (r["time"], r["tranId"]))
        p = page or 1
        return out[(p - 1) * limit: p * limit]


@pytest.fixture
def ledger(tmp_path):
    return TradeLedger(str(tmp_path / "ledger.jsonl"))


def _events(ledger):
    if not ledger.path.exists():
        return []
    return [json.loads(l) for l in ledger.path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _tran_ids(ledger):
    return [e["tran_id"] for e in _events(ledger)]


def test_writes_one_event_per_settlement_with_signed_amount(ledger):
    client = FakeClient([_row(NOW - 5000, "-0.0123", tran=11), _row(NOW - 4000, "0.0456", tran=12)])
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 2
    events = _events(ledger)
    assert [e["funding"] for e in events] == [-0.0123, 0.0456]
    assert all(e["event_type"] == "funding_fee" and e["source"] == "binance_income" for e in events)
    assert events[0]["symbol"] == "BTCUSDT" and events[0]["asset"] == "USDT"
    assert events[0]["settle_time_ms"] == NOW - 5000
    assert events[0]["settle_time"].endswith("Z")


def test_first_run_looks_back_default_window_and_queries_funding_only(ledger):
    client = FakeClient([])
    sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW)
    call = client.calls[0]
    assert call["start"] == NOW - DEFAULT_LOOKBACK_MS and call["end"] == NOW
    assert call["income_type"] == "FUNDING_FEE" and call["symbol"] == "BTCUSDT" and call["page"] == 1
    assert DEFAULT_LOOKBACK_MS < 90 * 24 * 3600 * 1000  # Binance only serves ~3 months


def test_rerun_with_overlapping_data_writes_nothing(ledger):
    client = FakeClient([_row(NOW - 5000, tran=11), _row(NOW - 4000, tran=12)])
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 2
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 0
    # start is inclusive of the newest recorded settlement, so the overlap row IS re-served
    assert client.calls[-1]["start"] == NOW - 4000
    assert any(r["time"] == NOW - 4000 for r in client.income_history(start_time_ms=NOW - 4000, end_time_ms=NOW))
    assert _tran_ids(ledger) == [11, 12]


def test_duplicate_rows_inside_one_response_are_written_once(ledger):
    row = _row(NOW - 100, tran=7)
    assert sync_funding_fees(FakeClient([row, dict(row)]), ledger, "BTCUSDT", now_ms=NOW) == 1
    assert _tran_ids(ledger) == [7]


def test_same_tran_id_at_different_times_or_symbols_are_distinct(ledger):
    rows = [_row(NOW - 300, tran=5), _row(NOW - 200, tran=5), _row(NOW - 300, tran=5, symbol="ETHUSDT")]
    client = FakeClient(rows)
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 2
    assert sync_funding_fees(client, ledger, "ETHUSDT", now_ms=NOW) == 1


def test_pages_use_page_param_over_a_fixed_window(ledger):
    rows = [_row(NOW - 10_000 + i * 10, tran=100 + i) for i in range(5)]
    client = FakeClient(rows)
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW, page_size=2) == 5
    assert [c["page"] for c in client.calls] == [1, 2, 3]
    assert len({(c["start"], c["end"]) for c in client.calls}) == 1  # window never moves
    assert _tran_ids(ledger) == [100, 101, 102, 103, 104]


def test_many_rows_on_the_same_millisecond_across_full_pages_are_all_recorded(ledger):
    """審閱 F1：1001 列同一毫秒，第一頁 1000 列後不能漏掉第 1001 列。"""
    same_ms = NOW - 5000
    client = FakeClient([_row(same_ms, tran=i) for i in range(1, 1002)])
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 1001
    assert sorted(_tran_ids(ledger)) == list(range(1, 1002))
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 0


def test_page_boundary_exactly_full_last_page_is_followed_by_an_empty_page(ledger):
    client = FakeClient([_row(NOW - 100 + i, tran=i) for i in range(4)])
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW, page_size=2) == 4
    assert [c["page"] for c in client.calls] == [1, 2, 3]


def test_interrupted_write_is_recovered_on_rerun_even_on_the_same_millisecond(ledger, monkeypatch):
    """審閱 F1/F2：寫到一半被中斷，重跑要補回沒寫到的列（含同一毫秒的）。"""
    t = NOW - 5000
    client = FakeClient([_row(t - 10, tran=1), _row(t, tran=2), _row(t, tran=3), _row(t + 10, tran=4)])
    real_append = ledger.append
    state = {"n": 0}

    def flaky(event_type, **fields):
        state["n"] += 1
        if state["n"] == 3:  # dies while writing the 3rd row (second row on millisecond t)
            raise OSError("disk full")
        return real_append(event_type, **fields)

    monkeypatch.setattr(ledger, "append", flaky)
    with pytest.raises(OSError):
        sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW)
    assert _tran_ids(ledger) == [1, 2]
    monkeypatch.undo()
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 2
    assert _tran_ids(ledger) == [1, 2, 3, 4]


def test_bad_row_after_good_rows_writes_nothing_and_a_fixed_rerun_records_everything(ledger):
    """審閱 F2：好列→壞列→修復重跑。"""
    t = NOW - 5000
    rows = [_row(t, income="-0.01", tran=1), _row(t, income="n/a", tran=2)]
    client = FakeClient(rows)
    with pytest.raises(ValueError):
        sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW)
    assert _events(ledger) == []
    rows[1]["income"] = "-0.02"
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 2
    assert _tran_ids(ledger) == [1, 2]


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "nan", "inf", None, "", "n/a"])
def test_non_finite_or_unreadable_amount_fails_closed(ledger, bad):
    """審閱 F3。"""
    client = FakeClient([_row(NOW - 200, income="-0.01", tran=1), _row(NOW - 100, income=bad, tran=2)])
    with pytest.raises(ValueError):
        sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW)
    assert _events(ledger) == []


@pytest.mark.parametrize("bad_time", [None, "x", 0, -5, float("nan"), float("inf"), True])
def test_unreadable_time_fails_closed(ledger, bad_time):
    client = FakeClient([_row(NOW - 100, tran=1)])
    client.rows[0]["time"] = bad_time
    client.income_history = lambda **kw: [dict(_row(NOW - 100, tran=1), time=bad_time)]
    with pytest.raises(ValueError):
        sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW)
    assert _events(ledger) == []


def test_exceeding_the_page_cap_fails_instead_of_syncing_partially(ledger, monkeypatch):
    monkeypatch.setattr(mod, "MAX_PAGES", 3)
    client = FakeClient([_row(NOW - 1000 + i, tran=i) for i in range(10)])
    with pytest.raises(RuntimeError):
        sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW, page_size=2)
    assert _events(ledger) == []


def test_ignores_other_income_types_and_writes_only_the_funding_row(ledger):
    client = FakeClient([])
    client.income_history = lambda **kw: [_row(NOW - 100, "-0.5", income_type="COMMISSION", tran=1),
                                           _row(NOW - 90, "-0.02", tran=2)]
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 1
    (event,) = _events(ledger)
    assert event["tran_id"] == 2 and event["funding"] == -0.02


def test_other_sources_do_not_push_the_start_time_forward(ledger):
    ledger.append("funding_fee", record_id=1, symbol="BTCUSDT", funding=-1.0,
                  settle_time_ms=NOW + 10**9)  # an old-exchange row dated "in the future"
    client = FakeClient([_row(NOW - 100, tran=9)])
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 1


def test_total_sums_usdt_and_unlabelled_funding_but_not_other_assets(ledger):
    sync_funding_fees(FakeClient([_row(NOW - 3, "-0.5", tran=1), _row(NOW - 2, "0.2", tran=2),
                                   _row(NOW - 1, "9.0", tran=3, asset="BNB")]),
                      ledger, "BTCUSDT", now_ms=NOW)
    ledger.append("funding_fee", record_id="legacy", symbol="BTCUSDT", funding=0.1)  # no asset label
    ledger.append("trade_open", trade_id="t", symbol="BTCUSDT")
    assert total_funding_fee(str(ledger.path)) == pytest.approx(-0.2)
    assert total_funding_fee(str(ledger.path), asset="BNB") == pytest.approx(9.1)
    assert total_funding_fee(str(ledger.path.parent / "missing.jsonl")) == 0.0


# ---- 審閱 F4：帳本本身被半筆寫入弄壞時要 fail closed，不能黏行、不能回報成功 ----

def _tear_tail(ledger, partial='{"event_type": "funding_fee", "record_id": "BTCUSDT:'):
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write(partial)  # no closing brace, no newline: what a killed append leaves behind


def test_torn_tail_fails_closed_without_touching_the_ledger_or_the_exchange(ledger):
    sync_funding_fees(FakeClient([_row(NOW - 30, tran=1)]), ledger, "BTCUSDT", now_ms=NOW)
    _tear_tail(ledger)
    before = ledger.path.read_bytes()
    client = FakeClient([_row(NOW - 30, tran=1), _row(NOW - 20, tran=2), _row(NOW - 10, tran=3)])
    with pytest.raises(FundingLedgerError):
        sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW)
    assert ledger.path.read_bytes() == before
    assert client.calls == []  # failed before asking the exchange


def test_unreadable_interior_line_fails_closed(ledger):
    sync_funding_fees(FakeClient([_row(NOW - 30, tran=1)]), ledger, "BTCUSDT", now_ms=NOW)
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")
    sync_funding_fees_client = FakeClient([_row(NOW - 20, tran=2)])
    with pytest.raises(FundingLedgerError):
        sync_funding_fees(sync_funding_fees_client, ledger, "BTCUSDT", now_ms=NOW)


def test_complete_json_without_trailing_newline_also_fails_closed(ledger):
    ledger.path.write_text(json.dumps({"event_type": "trade_open", "trade_id": "t"}), encoding="utf-8")
    with pytest.raises(FundingLedgerError):
        sync_funding_fees(FakeClient([_row(NOW - 20, tran=2)]), ledger, "BTCUSDT", now_ms=NOW)


def test_non_object_line_fails_closed(ledger):
    ledger.path.write_text("[1, 2]\n", encoding="utf-8")
    with pytest.raises(FundingLedgerError):
        sync_funding_fees(FakeClient([]), ledger, "BTCUSDT", now_ms=NOW)


def test_real_partial_append_is_never_reported_as_success_and_recovers_after_repair(ledger, monkeypatch):
    """append 寫出一半 bytes 才失敗：重跑必須拒絕（不得得到 valid=[1,3]＋壞行＋回報成功）；
    人把半筆尾巴修掉之後重跑要補齊 1、2、3。"""
    t = NOW - 5000
    client = FakeClient([_row(t - 10, tran=1), _row(t, tran=2), _row(t + 10, tran=3)])
    real_append = ledger.append
    state = {"n": 0}

    def torn(event_type, **fields):
        state["n"] += 1
        if state["n"] == 2:
            line = json.dumps({"event_type": event_type, **fields}, sort_keys=True)
            with ledger.path.open("a", encoding="utf-8") as handle:
                handle.write(line[: len(line) // 2])  # half a line, then the "disk fills up"
            raise OSError("no space left on device")
        return real_append(event_type, **fields)

    monkeypatch.setattr(ledger, "append", torn)
    with pytest.raises(OSError):
        sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW)
    monkeypatch.undo()

    with pytest.raises(FundingLedgerError):
        sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW)
    assert _tran_ids_safe(ledger) == [1]  # nothing was glued on, nothing new written

    raw = ledger.path.read_bytes()  # the repair a person would do: cut the torn tail
    ledger.path.write_bytes(raw[: raw.rfind(b"\n") + 1])
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 2
    assert _tran_ids(ledger) == [1, 2, 3]


def _tran_ids_safe(ledger):
    """只數完整的行（壞尾巴不算）。"""
    out = []
    for line in ledger.path.read_bytes().split(b"\n")[:-1]:
        out.append(json.loads(line)["tran_id"])
    return out


def test_rows_are_written_oldest_first_even_if_the_exchange_returns_them_shuffled(ledger):
    client = FakeClient([])
    client.income_history = lambda **kw: [_row(NOW - 10, tran=3), _row(NOW - 30, tran=1), _row(NOW - 20, tran=2)]
    assert sync_funding_fees(client, ledger, "BTCUSDT", now_ms=NOW) == 3
    assert _tran_ids(ledger) == [1, 2, 3]


def test_total_refuses_a_damaged_ledger_instead_of_returning_a_short_number(ledger):
    sync_funding_fees(FakeClient([_row(NOW - 30, "-0.5", tran=1)]), ledger, "BTCUSDT", now_ms=NOW)
    _tear_tail(ledger)
    with pytest.raises(FundingLedgerError):
        total_funding_fee(str(ledger.path))
