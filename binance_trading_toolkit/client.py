"""Binance USDⓈ-M 合約（futures）REST API 客戶端。

## 這份程式碼從哪裡來、驗證到什麼程度

簽章機制（HMAC-SHA256 對查詢字串、``X-MBX-APIKEY`` header、把 ``timestamp``/
``recvWindow`` 併進參數）是直接沿用 `sepa_vcp_screener`
（`Mexc_MarkMinervini` 專案）的 ``execution/testnet_client.py``——那份程式
已經在 Binance **現貨測試網**上真的送單驗證過，簽章邏輯正確。Binance 的
簽章機制在現貨／合約／保證金 API 之間是一致的，所以這裡直接沿用。

**但端點本身（合約 API 特有的部分）目前沒有用真實／測試網帳號實際跑過**
——不像 `mexc-futures-toolkit` 有兩個專案的真實下單驗證撐腰。這裡的端點
路徑、參數名稱是根據 Binance 官方文件（2026-08-19 查證，見各函式的來源
連結）寫的，屬於「照文件寫、尚未實測」。**正式串接前，請先用 Binance
Futures 測試網（見下方 ``BinanceConfig.base_url``）跑過至少一輪
下單→查詢→取消／平倉，確認回應格式跟這裡的解析邏輯吻合。**

## 官方文件
- 簽章說明: https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
- 下單: https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order
- 合約資訊: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import requests


class BinanceAPIError(RuntimeError):
    """Binance 回應非 200，或 payload 帶有 ``code``/``msg`` 形式的錯誤。"""


@dataclass(frozen=True)
class BinanceConfig:
    base_url: str = "https://fapi.binance.com"
    """USDⓈ-M 合約正式站。

    測試網有兩個候選網址，官方文件（2026-08-19 查證）目前列的是
    ``https://demo-fapi.binance.com``；坊間套件與教學普遍還在用較舊的
    ``https://testnet.binancefuture.com``。兩個都沒有實測過，正式串接
    前請自己先驗證哪一個能通。
    """
    api_key: str = ""
    api_secret: str = ""
    timeout_seconds: int = 20
    recv_window_ms: int = 10_000


@dataclass
class OrderResult:
    """下單/平倉結果的精簡包裝。"""

    ok: bool
    symbol: str
    side: str
    order_id: int | None = None
    status: str = ""
    executed_qty: float = 0.0
    avg_price: float | None = None
    error: str = ""
    raw: dict = field(default_factory=dict)


class BinanceFuturesClient:
    """Binance USDⓈ-M 合約客戶端。金鑰只存在建構時傳入的 ``BinanceConfig``
    裡，呼叫端負責只從環境變數讀取、不要寫死。"""

    def __init__(self, config: BinanceConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self._secret = config.api_secret.encode()
        self._session = session or requests.Session()
        if config.api_key:
            self._session.headers.update({"X-MBX-APIKEY": config.api_key})

    # ------------------------------------------------------------------
    # 底層：簽章與請求
    #
    # 簽章公式跟參數組裝方式沿用 sepa_vcp_screener/execution/testnet_client.py
    # 已驗證過的實作：對「送出去的那個確切查詢字串」計算 HMAC，不強制排序
    # ——只要簽的字串跟送的字串一致，Binance 的驗證就會過。
    # ------------------------------------------------------------------
    def _signed(self, method: str, path: str, params: dict[str, Any]) -> Any:
        params = {
            k: v for k, v in {
                **params,
                "timestamp": int(time.time() * 1000),
                "recvWindow": self.config.recv_window_ms,
            }.items() if v is not None
        }
        query = urlencode(params)
        signature = hmac.new(self._secret, query.encode(), hashlib.sha256).hexdigest()
        url = f"{self.config.base_url}{path}?{query}&signature={signature}"

        if not self.config.api_key or not self.config.api_secret:
            raise BinanceAPIError("私有端點需要 api_key / api_secret")

        response = self._session.request(method, url, timeout=self.config.timeout_seconds)
        return self._decode(response)

    def _public(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._session.get(
            f"{self.config.base_url}{path}",
            params={k: v for k, v in (params or {}).items() if v is not None},
            timeout=self.config.timeout_seconds,
        )
        return self._decode(response)

    def _decode(self, response: requests.Response) -> Any:
        try:
            payload = response.json()
        except ValueError:
            payload = {"msg": response.text[:200]}
        if response.status_code != 200:
            # 只記錄交易所回的訊息，不記錄 URL（URL 含簽章）
            raise BinanceAPIError(f"HTTP {response.status_code}：{payload.get('msg', payload)}")
        return payload

    # ------------------------------------------------------------------
    # 公開端點：行情
    # ------------------------------------------------------------------
    def klines(self, symbol: str, interval: str = "4h", limit: int = 500,
               start_time_ms: int | None = None, end_time_ms: int | None = None) -> list[list[Any]]:
        """回傳官方原始格式：每根 K 棒是一個 12 欄位的 list
        （開盤時間、開高低收、量……見官方文件），不做欄位轉換。"""
        return self._public("/fapi/v1/klines", {
            "symbol": symbol, "interval": interval, "limit": limit,
            "startTime": start_time_ms, "endTime": end_time_ms,
        })

    def exchange_info(self, symbol: str | None = None) -> dict:
        return self._public("/fapi/v1/exchangeInfo", {"symbol": symbol})

    def ticker_price(self, symbol: str) -> float:
        data = self._public("/fapi/v1/ticker/price", {"symbol": symbol})
        return float(data["price"])

    def symbol_filters(self, symbol: str) -> dict[str, Any]:
        """從 ``exchange_info`` 找出指定合約的完整規格（含 filters 陣列）。"""
        info = self.exchange_info(symbol)
        symbols = info.get("symbols") or []
        for entry in symbols:
            if entry.get("symbol") == symbol:
                return entry
        raise BinanceAPIError(f"exchangeInfo 找不到合約：{symbol}")

    # ------------------------------------------------------------------
    # 私有端點：帳戶 / 部位
    # ------------------------------------------------------------------
    def account_balance(self) -> list[dict[str, Any]]:
        return self._signed("GET", "/fapi/v2/balance", {})

    def position_information(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self._signed("GET", "/fapi/v2/positionRisk", {"symbol": symbol})

    def open_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """跟 ``position_information`` 一樣呼叫 positionRisk，但只回傳
        ``positionAmt`` 不為 0 的列——方便直接拿來做「帳上有沒有部位」的
        安全檢查，不用呼叫端自己再過濾一次。"""
        return [p for p in self.position_information(symbol) if float(p.get("positionAmt") or 0) != 0]

    def open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self._signed("GET", "/fapi/v1/openOrders", {"symbol": symbol})

    def change_leverage(self, symbol: str, leverage: int) -> dict:
        return self._signed("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})

    def change_margin_type(self, symbol: str, margin_type: str) -> dict:
        """``margin_type``：``ISOLATED`` 或 ``CROSSED``。"""
        if margin_type not in ("ISOLATED", "CROSSED"):
            raise ValueError("margin_type 必須是 'ISOLATED' 或 'CROSSED'")
        return self._signed("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type})

    # ------------------------------------------------------------------
    # 私有端點：下單 / 取消
    # ------------------------------------------------------------------
    def market_order(self, symbol: str, side: str, quantity: float, *,
                      reduce_only: bool = False, position_side: str | None = None,
                      new_client_order_id: str | None = None) -> OrderResult:
        """市價單。``side``：``BUY``/``SELL``。"""
        try:
            data = self._signed("POST", "/fapi/v1/order", {
                "symbol": symbol, "side": side, "type": "MARKET",
                "quantity": _trim(quantity),
                "reduceOnly": "true" if reduce_only else None,
                "positionSide": position_side,
                "newClientOrderId": new_client_order_id,
            })
        except BinanceAPIError as exc:
            return OrderResult(ok=False, symbol=symbol, side=side, error=str(exc))

        return OrderResult(
            ok=True, symbol=symbol, side=side,
            order_id=data.get("orderId"), status=data.get("status", ""),
            executed_qty=float(data.get("executedQty", 0) or 0),
            avg_price=float(data["avgPrice"]) if data.get("avgPrice") not in (None, "0") else None,
            raw=data,
        )

    def limit_order(self, symbol: str, side: str, quantity: float, price: float, *,
                     time_in_force: str = "GTC", reduce_only: bool = False,
                     position_side: str | None = None, new_client_order_id: str | None = None) -> OrderResult:
        """限價單。``side``：``BUY``/``SELL``。跟 ``market_order`` 走同一個
        ``/fapi/v1/order`` 端點，只是 ``type=LIMIT`` 並多帶 ``price``/
        ``timeInForce``。用途包括：真的掛一張刻意不會成交的單（驗證
        ``cancel_order()``），或刻意送出會被拒絕的參數（驗證錯誤格式）
        時，比 ``market_order`` 安全——不會有意外成交的風險。"""
        try:
            data = self._signed("POST", "/fapi/v1/order", {
                "symbol": symbol, "side": side, "type": "LIMIT",
                "timeInForce": time_in_force,
                "quantity": _trim(quantity), "price": _trim(price),
                "reduceOnly": "true" if reduce_only else None,
                "positionSide": position_side,
                "newClientOrderId": new_client_order_id,
            })
        except BinanceAPIError as exc:
            return OrderResult(ok=False, symbol=symbol, side=side, error=str(exc))

        return OrderResult(
            ok=True, symbol=symbol, side=side,
            order_id=data.get("orderId"), status=data.get("status", ""),
            executed_qty=float(data.get("executedQty", 0) or 0),
            avg_price=float(data["avgPrice"]) if data.get("avgPrice") not in (None, "0") else None,
            raw=data,
        )

    def stop_market_close_position(self, symbol: str, side: str, stop_price: float, *,
                                    position_side: str | None = None,
                                    working_type: str = "MARK_PRICE") -> OrderResult:
        """掛一張交易所端原生停損單，觸發時**市價平掉整個部位**
        （``closePosition=true``，Binance 官方的「Close-All」機制，等同
        MEXC 那邊「綁在部位上的原生停損單」概念，但參數不同）。

        ``side`` 是平倉方向：多單用 ``SELL``、空單用 ``BUY``（觸發後反向
        平倉）。**對沖模式（Hedge Mode）下，``closePosition`` 不能跟
        ``LONG`` 部位配 ``BUY``、也不能跟 ``SHORT`` 部位配 ``SELL``**
        ——這是官方文件明確記載的限制，呼叫端要自己確保方向正確。

        ``quantity`` 刻意不提供——``closePosition=true`` 時官方文件說明
        不能同時帶數量，交易所會自己抓當下的完整部位量。

        **2026-08-20 用真實測試網帳號驗證過**：條件單（STOP_MARKET 等）
        已經被 Binance 移到獨立的 Algo Order API（``/fapi/v1/algoOrder``），
        原本文件裡寫的 ``/fapi/v1/order`` + ``type=STOP_MARKET`` 直接送
        會被拒絕，回應 ``HTTP 400：Order type not supported for this
        endpoint. Please use the Algo Order API endpoints instead.``——
        這裡已經改用正確的端點，且參數名稱也不同：``triggerPrice`` 取代
        ``stopPrice``。回傳的識別碼欄位是 ``algoId``，放進
        ``OrderResult.order_id`` 讓呼叫端不用管底層欄位名稱差異。
        """
        try:
            data = self._signed("POST", "/fapi/v1/algoOrder", {
                "algoType": "CONDITIONAL",
                "symbol": symbol, "side": side, "type": "STOP_MARKET",
                "triggerPrice": _trim(stop_price), "closePosition": "true",
                "positionSide": position_side, "workingType": working_type,
            })
        except BinanceAPIError as exc:
            return OrderResult(ok=False, symbol=symbol, side=side, error=str(exc))

        return OrderResult(
            ok=True, symbol=symbol, side=side,
            order_id=data.get("algoId"), status=data.get("algoStatus", ""), raw=data,
        )

    def cancel_algo_order(self, symbol: str, algo_id: int) -> OrderResult:
        """取消一張用 ``stop_market_close_position()`` 掛的條件單。
        走 Algo Order API，跟一般訂單的 ``cancel_order()`` 是不同端點。

        **2026-08-20 用真實測試網驗證過**：取消成功的回應是
        ``{"algoId": ..., "code": "200", "msg": "success"}``，沒有
        ``algoStatus`` 欄位——狀態改看 ``msg``。"""
        try:
            data = self._signed("DELETE", "/fapi/v1/algoOrder", {"symbol": symbol, "algoId": algo_id})
        except BinanceAPIError as exc:
            return OrderResult(ok=False, symbol=symbol, side="", error=str(exc))
        return OrderResult(ok=True, symbol=symbol, side=data.get("side", ""),
                            order_id=data.get("algoId", algo_id), status=str(data.get("msg", "")), raw=data)

    def open_algo_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """查詢目前掛著的條件單（含這裡掛的原生停損）。"""
        data = self._signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol})
        orders = data.get("orders", data) if isinstance(data, dict) else data
        return orders if isinstance(orders, list) else []

    def cancel_order(self, symbol: str, order_id: int) -> OrderResult:
        """取消一般訂單（市價/限價單）。條件單／原生停損請用 ``cancel_algo_order()``。"""
        try:
            data = self._signed("DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id})
        except BinanceAPIError as exc:
            return OrderResult(ok=False, symbol=symbol, side="", error=str(exc))
        return OrderResult(ok=True, symbol=symbol, side=data.get("side", ""),
                            order_id=data.get("orderId"), status=data.get("status", ""), raw=data)

    # ------------------------------------------------------------------
    # 私有端點：已實現成交明細
    # ------------------------------------------------------------------
    def user_trades(self, symbol: str, *, order_id: int | None = None,
                     start_time_ms: int | None = None, end_time_ms: int | None = None,
                     limit: int = 500) -> list[dict[str, Any]]:
        """``GET /fapi/v1/userTrades``——查詢已經結算過的真實成交紀錄，含
        ``commission``／``commissionAsset``／``realizedPnl`` 這些下單當下的
        即時回應（``market_order()`` 回傳的 ``OrderResult``）完全沒有的欄位。

        2026-08-21 加：跟 mexc-futures-toolkit／my-crypto-bot 已經踩過的坑
        一樣——``market_order()`` 送出後**立即**拿到的回應，``avgPrice``
        不保證已經有值（成交確認可能有些微延遲），而且從來就沒有手續費
        欄位。呼叫端如果直接拿下單當下的回應記帳（尤其是真錢交易），記錄
        下來的價格/手續費可能是不完整或缺漏的估計值，不是交易所真正結算
        的數字。這個方法搭配 ``confirmed_fill()`` 讓呼叫端可以在下單後
        另外查一次，用交易所已經確認的真實成交資料入帳，取代自己假設的
        滑價/手續費模型。

        用 ``order_id`` 篩選只回傳屬於指定訂單的成交筆數（一張市價單可能
        分好幾筆撮合成交）；不帶就回傳整個 symbol 最近的成交紀錄。
        """
        return self._signed("GET", "/fapi/v1/userTrades", {
            "symbol": symbol, "orderId": order_id,
            "startTime": start_time_ms, "endTime": end_time_ms, "limit": limit,
        })


def _trim(value: float) -> str:
    """去掉尾端的零——Binance 對過長的小數位會拒單（跟 MEXC 一樣的坑）。"""
    return f"{value:.8f}".rstrip("0").rstrip(".")


def extract_filters(symbol_info: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """從 ``symbol_filters()`` 回傳的合約規格取出 (tick_size, step_size, min_notional)。

    官方文件明確警告不要直接用 ``pricePrecision``/``quantityPrecision``，
    要從 ``filters`` 陣列裡的 ``PRICE_FILTER``/``LOT_SIZE``/``MIN_NOTIONAL``
    抓 ``tickSize``/``stepSize``/``notional``。
    """
    tick_size = step_size = min_notional = None
    for f in symbol_info.get("filters", []):
        ftype = f.get("filterType")
        if ftype == "PRICE_FILTER":
            tick_size = float(f["tickSize"])
        elif ftype == "LOT_SIZE":
            step_size = float(f["stepSize"])
        elif ftype == "MIN_NOTIONAL":
            min_notional = float(f["notional"])
    return tick_size, step_size, min_notional


def round_to_step(quantity: float, step: float | None) -> float:
    """把數量無條件捨去到最小變動單位（跟 sepa_vcp_screener/risk/position.py 一致）。"""
    if not step or step <= 0:
        return quantity
    import math
    return math.floor(quantity / step) * step


def confirmed_fill(trades: list[dict[str, Any]], order_id: int) -> dict[str, Any] | None:
    """把 ``user_trades()`` 回傳的成交明細，篩出屬於指定 ``order_id`` 的那些筆，
    彙總成這張訂單的確認成交資訊：成交量加權平均價、總成交量、總手續費。

    設計成一個獨立的純函式（不打網路），是因為彙總邏輯本身值得單獨測試，
    不用每次都真的發 HTTP 請求；呼叫端典型用法是
    ``confirmed_fill(client.user_trades(symbol, order_id=oid), oid)``。

    找不到屬於這個 ``order_id`` 的任何成交紀錄就回傳 ``None``——呼叫端要
    自己決定退回哪個舊資料當 fallback（例如 ``market_order()`` 當下的
    ``avgPrice``），不要把「查不到」誤判成「成交量是 0」。

    手續費彙總只信任 ``commissionAsset`` 全部是 ``"USDT"`` 的情況——USDⓈ-M
    合約預設用保證金資產（USDT）付手續費，但用 BNB 折扣付手續費時
    ``commissionAsset`` 會是 ``"BNB"``，把不同資產的手續費直接加總會是
    錯的假精確度。混到非 USDT 的手續費時，``commission`` 回傳 ``None``、
    ``commission_asset_mismatch`` 標成 ``True``，讓呼叫端自己決定要換算
    匯率還是退回估計值，這裡不代為決定。
    """
    matched = [t for t in trades if t.get("orderId") == order_id]
    if not matched:
        return None
    total_qty = sum(float(t["qty"]) for t in matched)
    if total_qty <= 0:
        return None
    avg_price = sum(float(t["price"]) * float(t["qty"]) for t in matched) / total_qty
    commission_asset_mismatch = any(t.get("commissionAsset") != "USDT" for t in matched)
    total_commission = None if commission_asset_mismatch else sum(float(t["commission"]) for t in matched)
    return {
        "avg_price": avg_price,
        "quantity": total_qty,
        "commission": total_commission,
        "commission_asset_mismatch": commission_asset_mismatch,
        "trade_count": len(matched),
    }
