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
    confirmed_fill,
    extract_filters,
    round_to_step,
)


@pytest.fixture
def client():
    return BinanceFuturesClient(BinanceConfig(api_key="test-key", api_secret="test-secret"))


def _stub_precision(monkeypatch, client, *, tick_size=None, step_size=None):
    """market_order()/limit_order()/stop_market_close_position() 現在每次下單
    前都會呼叫 _precision_for() 查即時精度規則（見 client.py 的說明）——這是
    一個獨立的公開 GET 請求，不是原本測試已經在 monkeypatch 的 _session.request
    (那個只覆蓋 _signed() 用的方法)。這裡直接 stub 掉 _precision_for() 本身，
    而不是連帶 stub _session.get，這樣既有測試只需要多一行就能繼續假設「精度
    查詢回傳的值不影響它們原本要驗證的東西」（tick_size/step_size 預設 None，
    round_to_step() 對 None 是 no-op，行為等同這個防護加上去之前）。"""
    monkeypatch.setattr(client, "_precision_for", lambda symbol: (tick_size, step_size))


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
        _stub_precision(monkeypatch, client)

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
        _stub_precision(monkeypatch, client)
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

    def test_market_order_rounds_quantity_to_live_step_size(self, client, monkeypatch):
        """2026-08-21 真實事故：呼叫端算出的數量（例如用一個過期的 step 假設）
        不是交易所目前 stepSize 的整數倍時，Binance 會以 "Precision is over
        the maximum defined for this asset" 拒單。這裡驗證 market_order()
        會用即時查到的 stepSize 重新捨去，不是照單全收呼叫端傳進來的裸數字。"""
        _stub_precision(monkeypatch, client, step_size=0.001)
        captured = {}

        def fake_request(method, url, timeout):
            captured["url"] = url
            return FakeResponse({"orderId": 1, "status": "NEW", "executedQty": "0", "avgPrice": "0"})

        monkeypatch.setattr(client._session, "request", fake_request)
        client.market_order("BTCUSDT", "BUY", 0.0018)  # 不是 0.001 的整數倍

        params = parse_qs(urlparse(captured["url"]).query)
        assert params["quantity"] == ["0.001"]  # 捨去到 0.001，不是拒單也不是照原樣送 0.0018

    def test_limit_order_sends_price_and_time_in_force(self, client, monkeypatch):
        captured = {}
        _stub_precision(monkeypatch, client)

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
        _stub_precision(monkeypatch, client)
        monkeypatch.setattr(
            client._session, "request",
            lambda method, url, timeout: FakeResponse({"code": -2019, "msg": "Margin is insufficient."}, 400),
        )
        result = client.market_order("BTCUSDT", "BUY", 1e9)
        assert result.ok is False
        assert "insufficient" in result.error

    def test_order_fails_cleanly_when_precision_lookup_itself_fails(self, client, monkeypatch):
        """精度查詢本身失敗（網路問題等）時，跟下單失敗走同一條路徑——回傳
        OrderResult(ok=False)，不是讓例外整個往外炸、繞過呼叫端既有的失敗
        處理邏輯（emergency flatten／SAFE_HALT 等都是接在 OrderResult.ok
        上判斷的）。"""
        def boom(symbol):
            raise BinanceAPIError("HTTP 500：查詢失敗")
        monkeypatch.setattr(client, "_precision_for", boom)
        result = client.market_order("BTCUSDT", "BUY", 0.01)
        assert result.ok is False
        assert "查詢失敗" in result.error

    def test_stop_market_close_position_sends_close_all_params(self, client, monkeypatch):
        captured = {}
        _stub_precision(monkeypatch, client)

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

    def test_stop_market_close_position_rounds_trigger_price_to_live_tick_size(self, client, monkeypatch):
        """2026-08-21 真實發現：ATR 算出來的停損價幾乎不可能剛好是 tickSize
        的整數倍，裸浮點數直接送出去很容易撞上跟數量精度一樣的
        "Precision is over the maximum" 拒單，此前完全沒做這個防護。"""
        _stub_precision(monkeypatch, client, tick_size=0.10)
        captured = {}

        def fake_request(method, url, timeout):
            captured["url"] = url
            return FakeResponse({"algoId": 8, "algoStatus": "NEW"})

        monkeypatch.setattr(client._session, "request", fake_request)
        client.stop_market_close_position("BTCUSDT", "SELL", 60000.37, position_side="LONG")

        params = parse_qs(urlparse(captured["url"]).query)
        assert params["triggerPrice"] == ["60000.3"]

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


class TestUserTrades:
    def test_user_trades_filters_by_order_id_param(self, client, monkeypatch):
        captured = {}

        def fake_request(method, url, timeout):
            captured["method"] = method
            captured["url"] = url
            return FakeResponse([{"orderId": 42, "price": "63500.0", "qty": "0.01",
                                   "commission": "0.254", "commissionAsset": "USDT"}])

        monkeypatch.setattr(client._session, "request", fake_request)
        trades = client.user_trades("BTCUSDT", order_id=42)

        assert captured["method"] == "GET"
        assert "/fapi/v1/userTrades" in captured["url"]
        params = parse_qs(urlparse(captured["url"]).query)
        assert params["orderId"] == ["42"]
        assert trades[0]["orderId"] == 42

    def test_user_trades_without_order_id_omits_the_param(self, client, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            client._session, "request",
            lambda method, url, timeout: (captured.__setitem__("url", url), FakeResponse([]))[1],
        )
        client.user_trades("BTCUSDT")
        params = parse_qs(urlparse(captured["url"]).query)
        assert "orderId" not in params


class TestConfirmedFill:
    def test_aggregates_multiple_fills_into_weighted_average(self):
        trades = [
            {"orderId": 42, "price": "63000.0", "qty": "0.006", "commission": "0.15", "commissionAsset": "USDT"},
            {"orderId": 42, "price": "63100.0", "qty": "0.004", "commission": "0.10", "commissionAsset": "USDT"},
            {"orderId": 99, "price": "10.0", "qty": "100", "commission": "1.0", "commissionAsset": "USDT"},  # different order, must be excluded
        ]
        result = confirmed_fill(trades, 42)
        assert result is not None
        assert result["quantity"] == pytest.approx(0.01)
        assert result["avg_price"] == pytest.approx((63000.0 * 0.006 + 63100.0 * 0.004) / 0.01)
        assert result["commission"] == pytest.approx(0.25)
        assert result["commission_asset_mismatch"] is False
        assert result["trade_count"] == 2

    def test_returns_none_when_order_id_not_found(self):
        assert confirmed_fill([{"orderId": 1, "price": "1", "qty": "1", "commission": "0", "commissionAsset": "USDT"}], 999) is None

    def test_returns_none_for_empty_trade_list(self):
        assert confirmed_fill([], 42) is None

    def test_non_usdt_commission_is_flagged_not_silently_summed(self):
        trades = [
            {"orderId": 42, "price": "63000.0", "qty": "0.01", "commission": "0.0001", "commissionAsset": "BNB"},
        ]
        result = confirmed_fill(trades, 42)
        assert result["commission_asset_mismatch"] is True
        assert result["commission"] is None
        # avg_price/quantity are still trustworthy even when the fee currency isn't USDT
        assert result["quantity"] == pytest.approx(0.01)


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
