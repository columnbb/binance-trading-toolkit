"""Independent Debug-AI review supplement for candidate commit 25be602.

These tests are NOT part of the development candidate. They were added by an
independent reviewer to close negative-test coverage gaps required by the
review plan:

  * fixed-ID validation must fire (local ValueError, zero transport calls) for
    EVERY entry point that accepts a caller-supplied client ID, not only the
    three already covered by TestStableClientIdentifiers
    (market_order / order_by_client_id / algo_order_by_client_id).
  * ``is_order_not_found`` must treat -1006, a bare -1007, a generic HTTP
    error and a raw transport exception as "unknown", never as "absent".
  * cancel_order / cancel_algo_order ambiguity must be rejected before any
    request is attempted (assert the fake transport is never called).

All tests use a fake transport; no Binance connection or credential is used.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
import requests

from binance_trading_toolkit.client import (
    BinanceAPIError,
    BinanceConfig,
    BinanceFuturesClient,
)


@pytest.fixture
def client():
    return BinanceFuturesClient(BinanceConfig(api_key="test-key", api_secret="test-secret"))


@pytest.fixture
def no_network(client, monkeypatch):
    """Any HTTP attempt through the client's session fails the test loudly."""
    calls = []

    def forbidden(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append((args, kwargs))
        raise AssertionError("transport was invoked but the call should have been rejected locally")

    monkeypatch.setattr(client._session, "request", forbidden)
    monkeypatch.setattr(client._session, "get", forbidden)
    # _precision_for would be the first network hop for the order builders;
    # stub it so a *missing* validation would actually reach the transport
    # stub above (and fail) instead of dying earlier on a network call.
    monkeypatch.setattr(client, "_precision_for", lambda symbol: (0.1, 0.001))
    return calls


# --------------------------------------------------------------------------
# 1. Fixed-ID validation fires on every entry point, before any request.
# --------------------------------------------------------------------------
BAD_IDS = ["bad id with spaces", "", "   ", "a" * 37, "tab\tinside", "new\nline", "bad*char"]


@pytest.mark.parametrize("bad", BAD_IDS)
def test_limit_order_rejects_bad_client_id_before_request(client, no_network, bad):
    with pytest.raises(ValueError, match="new_client_order_id"):
        client.limit_order("BTCUSDT", "BUY", 0.01, 25_000.0, new_client_order_id=bad)
    assert no_network == []


@pytest.mark.parametrize("bad", BAD_IDS)
def test_market_order_rejects_bad_client_id_before_request(client, no_network, bad):
    with pytest.raises(ValueError, match="new_client_order_id"):
        client.market_order("BTCUSDT", "BUY", 0.01, new_client_order_id=bad)
    assert no_network == []


@pytest.mark.parametrize("bad", BAD_IDS)
def test_stop_close_rejects_bad_client_algo_id_before_request(client, no_network, bad):
    with pytest.raises(ValueError, match="client_algo_id"):
        client.stop_market_close_position("BTCUSDT", "SELL", 60_000.0, client_algo_id=bad)
    assert no_network == []


@pytest.mark.parametrize("bad", BAD_IDS)
def test_cancel_order_rejects_bad_client_id_before_request(client, no_network, bad):
    with pytest.raises(ValueError, match="orig_client_order_id"):
        client.cancel_order("BTCUSDT", orig_client_order_id=bad)
    assert no_network == []


@pytest.mark.parametrize("bad", BAD_IDS)
def test_cancel_algo_order_rejects_bad_client_algo_id_before_request(client, no_network, bad):
    with pytest.raises(ValueError, match="client_algo_id"):
        client.cancel_algo_order("BTCUSDT", client_algo_id=bad)
    assert no_network == []


@pytest.mark.parametrize("bad", BAD_IDS)
def test_order_by_client_id_rejects_bad_client_id_before_request(client, no_network, bad):
    with pytest.raises(ValueError):
        client.order_by_client_id("BTCUSDT", bad)
    assert no_network == []


@pytest.mark.parametrize("bad", BAD_IDS)
def test_algo_order_by_client_id_rejects_bad_client_id_before_request(client, no_network, bad):
    with pytest.raises(ValueError):
        client.algo_order_by_client_id("BTCUSDT", bad)
    assert no_network == []


def test_valid_client_id_with_allowed_punctuation_is_accepted(client, monkeypatch):
    """A well-formed ID (dots, colon, slash, dash, underscore) must pass and be
    forwarded verbatim as newClientOrderId."""
    monkeypatch.setattr(client, "_precision_for", lambda symbol: (0.1, 0.001))
    captured = {}
    monkeypatch.setattr(
        client._session, "request",
        lambda method, url, timeout: (captured.update(url=url),
                                      _FakeResp({"orderId": 1, "status": "NEW"}))[1],
    )
    ok_id = "seykota:entry/2026-08-27_abc.1"
    client.market_order("BTCUSDT", "BUY", 0.01, new_client_order_id=ok_id)
    params = parse_qs(urlparse(captured["url"]).query)
    assert params["newClientOrderId"] == [ok_id]


# --------------------------------------------------------------------------
# 2. Ambiguous / missing cancel identifiers are rejected before any request.
# --------------------------------------------------------------------------
def test_cancel_order_ambiguity_never_touches_transport(client, no_network):
    with pytest.raises(ValueError, match="exactly one"):
        client.cancel_order("BTCUSDT")
    with pytest.raises(ValueError, match="exactly one"):
        client.cancel_order("BTCUSDT", 7, orig_client_order_id="seykota.entry.abc-1")
    assert no_network == []


def test_cancel_algo_order_ambiguity_never_touches_transport(client, no_network):
    with pytest.raises(ValueError, match="exactly one"):
        client.cancel_algo_order("BTCUSDT")
    with pytest.raises(ValueError, match="exactly one"):
        client.cancel_algo_order("BTCUSDT", 7, client_algo_id="seykota.stop.abc-1")
    assert no_network == []


# --------------------------------------------------------------------------
# 3. Only an explicit -2013 counts as "order absent". Everything else stays
#    an unknown outcome.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("err", [
    "HTTP 400 code -1006：Unexpected response from server.",
    "HTTP 503 code -1007：Timeout waiting for response.",
    "HTTP 401 code -2015：Invalid API-key, IP, or permissions for action.",
    "HTTP 500：Internal error",
    "HTTP 418：banned",
    "HTTP 400 code -2011：Unknown order sent.",  # cancel-path unknown-order != query -2013
])
def test_non_2013_errors_are_never_treated_as_absent(err):
    assert BinanceFuturesClient.is_order_not_found(BinanceAPIError(err)) is False


def test_raw_transport_exception_is_never_treated_as_absent():
    assert BinanceFuturesClient.is_order_not_found(
        requests.ConnectionError("Connection aborted")) is False
    assert BinanceFuturesClient.is_order_not_found(
        requests.Timeout("Read timed out")) is False


@pytest.mark.parametrize("err", [
    "HTTP 400 code -2013：Order does not exist.",
    "HTTP 400 code -2013",
    "order NO_SUCH_ORDER on symbol",
])
def test_explicit_2013_is_recognised_as_absent(err):
    assert BinanceFuturesClient.is_order_not_found(BinanceAPIError(err)) is True


def test_2013_query_surfaces_as_error_and_is_classifiable(client, monkeypatch):
    """order_by_client_id must still RAISE on -2013 (an error stays an error);
    the caller uses is_order_not_found() on that error to classify it."""
    monkeypatch.setattr(
        client._session, "request",
        lambda method, url, timeout: _FakeResp({"code": -2013, "msg": "Order does not exist."}, 400),
    )
    with pytest.raises(BinanceAPIError) as info:
        client.order_by_client_id("BTCUSDT", "seykota.entry.001")
    assert BinanceFuturesClient.is_order_not_found(info.value) is True


def test_unknown_error_on_query_is_not_swallowed(client, monkeypatch):
    monkeypatch.setattr(
        client._session, "request",
        lambda method, url, timeout: _FakeResp({"code": -1006, "msg": "Unexpected response."}, 400),
    )
    with pytest.raises(BinanceAPIError) as info:
        client.algo_order_by_client_id("BTCUSDT", "seykota.stop.001")
    assert BinanceFuturesClient.is_order_not_found(info.value) is False


# --------------------------------------------------------------------------
# 4. A full create -> observe -> cancel cycle reuses ONE id on each API and
#    never emits an exchange-side id or a freshly generated one.
# --------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def test_ordinary_order_lifecycle_uses_one_stable_id(client, monkeypatch):
    monkeypatch.setattr(client, "_precision_for", lambda symbol: (0.1, 0.001))
    seen = []

    def fake_request(method, url, timeout):
        seen.append((method, url))
        return _FakeResp({"orderId": 55, "clientOrderId": "seykota.entry.abc-1",
                          "status": "NEW", "side": "BUY"})

    monkeypatch.setattr(client._session, "request", fake_request)
    cid = "seykota.entry.abc-1"
    client.market_order("BTCUSDT", "BUY", 0.01, new_client_order_id=cid)
    client.order_by_client_id("BTCUSDT", cid)
    client.cancel_order("BTCUSDT", orig_client_order_id=cid)

    create_q = parse_qs(urlparse(seen[0][1]).query)
    query_q = parse_qs(urlparse(seen[1][1]).query)
    cancel_q = parse_qs(urlparse(seen[2][1]).query)
    assert create_q["newClientOrderId"] == [cid]
    assert query_q["origClientOrderId"] == [cid] and "orderId" not in query_q
    assert cancel_q["origClientOrderId"] == [cid] and "orderId" not in cancel_q


def test_algo_order_lifecycle_uses_one_stable_id(client, monkeypatch):
    monkeypatch.setattr(client, "_precision_for", lambda symbol: (0.1, 0.001))
    seen = []

    def fake_request(method, url, timeout):
        seen.append((method, url))
        return _FakeResp({"algoId": 77, "clientAlgoId": "seykota.stop.abc-1",
                          "algoStatus": "NEW", "msg": "success", "side": "SELL"})

    monkeypatch.setattr(client._session, "request", fake_request)
    aid = "seykota.stop.abc-1"
    client.stop_market_close_position("BTCUSDT", "SELL", 60_000.0, client_algo_id=aid)
    client.algo_order_by_client_id("BTCUSDT", aid)
    client.cancel_algo_order("BTCUSDT", client_algo_id=aid)

    create_q = parse_qs(urlparse(seen[0][1]).query)
    query_q = parse_qs(urlparse(seen[1][1]).query)
    cancel_q = parse_qs(urlparse(seen[2][1]).query)
    assert create_q["clientAlgoId"] == [aid]
    assert query_q["clientAlgoId"] == [aid]
    assert cancel_q["clientAlgoId"] == [aid] and "algoId" not in cancel_q
    # no second, toolkit-generated identifier of any kind
    for q in (create_q, query_q, cancel_q):
        assert "newClientOrderId" not in q


def test_no_algo_modify_put_endpoint_exists(client, monkeypatch):
    """The toolkit must not expose an undocumented Algo modify (PUT) path."""
    monkeypatch.setattr(client._session, "request",
                        lambda method, url, timeout: _FakeResp({}, 200))
    for name in dir(client):
        if name.startswith("_"):
            continue
        attr = getattr(client, name)
        if callable(attr) and any(w in name for w in ("modify", "replace", "amend", "edit", "update")):
            pytest.fail(f"unexpected mutation-style method on client: {name}")
