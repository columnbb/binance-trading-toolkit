"""BinanceFuturesClient 的測試 —— 不打網路。

簽章機制沿用 sepa_vcp_screener/execution/testnet_client.py 已經在
Binance 現貨測試網驗證過的實作，這裡的簽章測試手法（手算 HMAC 比對）
直接對應那邊的 TestSigning，確保這裡的實作沒有走樣。

**端點本身（合約 API 特有的參數/回應格式）目前沒有真實或測試網驗證過**
——這些測試能保證的是「程式邏輯符合官方文件描述」，不能保證「Binance
真的會這樣回應」。正式使用前務必先在測試網跑過一輪。
"""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import parse_qs, urlparse

import pytest

from binance_trading_toolkit.client import (
    BinanceAPIError,
    BinanceConfig,
    BinanceFuturesClient,
    OrderResult,
    extract_filters,
    round_to_step,
)


@pytest.fixture
def client():
    return BinanceFuturesClient(BinanceConfig(api_key="test-key", api_secret="test-secret"))


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


class TestSigning:
    def test_signature_matches_hmac_of_query(self, client, monkeypatch):
        """跟 sepa_vcp_screener 的 TestSigning.test_signature_matches_hmac_of_query 對應。"""
        captured = {}

        def fake_request(method, url, timeout):
            captured["url"] = url
            return FakeResponse({"orderId": 1, "status": "NEW"})

        monkeypatch.setattr(client._session, "request", fake_request)
        client.market_order("BTCUSDT", "BUY", 0.01)

        url = captured["url"]
        query, _, signature = url.partition("?")[2].rpartition("&signature=")
        expected = hmac.new(b"test-secret", query.encode(), hashlib.sha256).hexdigest()
        assert signature == expected

    def test_request_includes_timestamp_and_recv_window(self, client, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            client._session, "request",
            lambda method, url, timeout: (captured.__setitem__("url", url), FakeResponse([]))[1],
        )
        client.account_balance()
        params = parse_qs(urlparse(captured["url"]).query)
        assert "timestamp" in params
        assert "recvWindow" in params

    def test_api_key_goes_in_header(self, client):
        assert client._session.headers["X-MBX-APIKEY"] == "test-key"

    def test_error_message_excludes_signed_url(self, client, monkeypatch):
        monkeypatch.setattr(
            client._session, "request",
            lambda method, url, timeout: FakeResponse({"code": -1121, "msg": "Invalid symbol."}, 400),
        )
        with pytest.raises(BinanceAPIError) as info:
            client.account_balance()
        assert "signature" not in str(info.value)
        assert "Invalid symbol" in str(info.value)

    def test_missing_credentials_rejected(self):
        client = BinanceFuturesClient(BinanceConfig())
        with pytest.raises(BinanceAPIError, match="api_key"):
            client.account_balance()

    def test_public_endpoint_does_not_require_credentials(self, monkeypatch):
        client = BinanceFuturesClient(BinanceConfig())
        monkeypatch.setattr(client._session, "get", lambda url, **kw: FakeResponse({"symbols": []}))
        assert client.exchange_info() == {"symbols": []}

    def test_ticker_price(self, monkeypatch):
        client = BinanceFuturesClient(BinanceConfig())
        monkeypatch.setattr(client._session, "get", lambda url, **kw: FakeResponse({"symbol": "BTCUSDT", "price": "63500.10"}))
        assert client.ticker_price("BTCUSDT") == pytest.approx(63500.10)


class TestOrders:
    def test_market_order_parses_fill(self, client, monkeypatch):
        monkeypatch.setattr(
            client._session, "request",
            lambda method, url, timeout: FakeResponse({
                "orderId": 42, "status": "FILLED", "executedQty": "0.010", "avgPrice": "63500.0",
            }),
        )
        result = client.market_order("BTCUSDT", "BUY", 0.01)
        assert result.ok
        assert result.order_id == 42
        assert result.executed_qty == pytest.approx(0.01)
        assert result.avg_price == pytest.approx(63500.0)

    def test_limit_order_sends_price_and_time_in_force(self, client, monkeypatch):
        captured = {}

        def fake_request(method, url, timeout):
            captured["url"] = url
            return FakeResponse({"orderId": 99, "status": "NEW", "executedQty": "0", "avgPrice": "0"})

        monkeypatch.setattr(client._session, "request", fake_request)
        result = client.limit_order("BTCUSDT", "BUY", 0.01, 25000.0)

        params = parse_qs(urlparse(captured["url"]).query)
        assert params["type"] == ["LIMIT"]
        assert params["price"] == ["25000"]
        assert params["timeInForce"] == ["GTC"]
        assert result.ok
        assert result.order_id == 99
        assert result.avg_price is None

    def test_failed_order_returns_result_not_exception(self, client, monkeypatch):
        monkeypatch.setattr(
            client._session, "request",
            lambda method, url, timeout: FakeResponse({"code": -2019, "msg": "Margin is insufficient."}, 400),
        )
        result = client.market_order("BTCUSDT", "BUY", 1e9)
        assert result.ok is False
        assert "insufficient" in result.error

    def test_stop_market_close_position_sends_close_all_params(self, client, monkeypatch):
        captured = {}

        def fake_request(method, url, timeout):
            captured["method"] = method
            captured["url"] = url
            # 2026-08-20 用真實測試網驗證過的實際回應形狀（沒有 status 欄位，是 algoStatus）
            return FakeResponse({"algoId": 7, "algoStatus": "NEW"})

        monkeypatch.setattr(client._session, "request", fake_request)
        result = client.stop_market_close_position("BTCUSDT", "SELL", 60000.0, position_side="LONG")

        assert captured["method"] == "POST"
        assert "/fapi/v1/algoOrder" in captured["url"]
        params = parse_qs(urlparse(captured["url"]).query)
        assert params["algoType"] == ["CONDITIONAL"]
        assert params["type"] == ["STOP_MARKET"]
        assert params["closePosition"] == ["true"]
        assert params["triggerPrice"] == ["60000"]
        assert params["positionSide"] == ["LONG"]
        assert "quantity" not in params
        assert "stopPrice" not in params
        assert result.ok
        assert result.order_id == 7
        assert result.status == "NEW"

    def test_cancel_algo_order_uses_algo_endpoint(self, client, monkeypatch):
        captured = {}

        def fake_request(method, url, timeout):
            captured["method"] = method
            captured["url"] = url
            # 2026-08-20 用真實測試網驗證過的實際回應形狀
            return FakeResponse({"algoId": 7, "clientAlgoId": "x", "code": "200", "msg": "success"})

        monkeypatch.setattr(client._session, "request", fake_request)
        result = client.cancel_algo_order("BTCUSDT", 7)

        assert captured["method"] == "DELETE"
        assert "/fapi/v1/algoOrder" in captured["url"]
        params = parse_qs(urlparse(captured["url"]).query)
        assert params["algoId"] == ["7"]
        assert result.ok
        assert result.status == "success"

    def test_open_algo_orders(self, client, monkeypatch):
        monkeypatch.setattr(
            client._session, "request",
            lambda method, url, timeout: FakeResponse([{"algoId": 7, "symbol": "BTCUSDT"}]),
        )
        orders = client.open_algo_orders("BTCUSDT")
        assert len(orders) == 1
        assert orders[0]["algoId"] == 7

    def test_cancel_order(self, client, monkeypatch):
        monkeypatch.setattr(
            client._session, "request",
            lambda method, url, timeout: FakeResponse({"orderId": 7, "status": "CANCELED", "side": "SELL"}),
        )
        result = client.cancel_order("BTCUSDT", 7)
        assert result.ok
        assert result.status == "CANCELED"


class TestPositions:
    def test_open_positions_filters_zero_amount(self, client, monkeypatch):
        monkeypatch.setattr(
            client._session, "request",
            lambda method, url, timeout: FakeResponse([
                {"symbol": "BTCUSDT", "positionAmt": "0"},
                {"symbol": "ETHUSDT", "positionAmt": "1.5"},
            ]),
        )
        positions = client.open_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "ETHUSDT"


class TestFiltersAndRounding:
    def test_extract_filters(self):
        info = {"filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
        ]}
        tick, step, notional = extract_filters(info)
        assert tick == pytest.approx(0.10)
        assert step == pytest.approx(0.001)
        assert notional == pytest.approx(5.0)

    def test_extract_filters_missing_entries_are_none(self):
        assert extract_filters({"filters": []}) == (None, None, None)

    def test_round_to_step(self):
        assert round_to_step(1.23456, 0.001) == pytest.approx(1.234)

    def test_round_to_step_zero_passes_through(self):
        assert round_to_step(1.2345, 0) == pytest.approx(1.2345)


def test_order_result_defaults():
    result = OrderResult(ok=False, symbol="X", side="BUY", error="boom")
    assert result.raw == {}
