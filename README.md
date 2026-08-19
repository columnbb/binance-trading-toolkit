# binance-trading-toolkit

共用的 Binance USDⓈ-M 合約（futures）私有 API 客戶端，給
`ed-seykota-systematic-trend-following` 用。

## 驗證狀態——請先讀這段

**簽章機制已驗證，端點本身還沒有。** 分開說明：

- **簽章公式**（HMAC-SHA256 對查詢字串、`X-MBX-APIKEY` header）直接沿用
  `Mexc_MarkMinervini`（`sepa_vcp_screener`）專案的
  `execution/testnet_client.py`——那份程式已經在 **Binance 現貨測試網**
  真的送單驗證過。Binance 的簽章機制在現貨／合約 API 之間是一致的，這裡
  用逐位元組比對過兩邊產生的簽章公式完全相同（見 commit）。
- **合約 API 特有的端點路徑、參數名稱、回應格式**是根據 Binance 官方文件
  （2026-08-19 查證）寫的，**沒有用真實或測試網合約帳號實際送過單**。
  跟 `mexc-futures-toolkit` 不一樣——那邊有兩個專案的真實下單驗證撐腰，
  這裡目前只有「照文件寫」的信心水準。

**正式串接 `ed-seykota-systematic-trend-following` 之前，請先在 Binance
Futures 測試網跑過至少一輪「查合約規格 → 開倉 → 掛原生停損 → 平倉」，
確認回應格式跟這裡的解析邏輯（`OrderResult`、`extract_filters` 等）吻合。**
測試網網址官方文件目前列的是 `https://demo-fapi.binance.com`，但坊間
教學普遍還在用較舊的 `https://testnet.binancefuture.com`，兩個都沒實測
過，請自己先確認哪個能通。

## 提供什麼、不提供什麼

**提供：** 簽章、request wrapper、查行情/合約規格、查部位/餘額、下市價單、
掛交易所端原生停損（`STOP_MARKET` + `closePosition=true`，Binance 官方的
Close-All 機制）、取消訂單、調整槓桿/保證金模式。

**不提供：** 策略邏輯、風控參數。這些留在各自專案裡。

## 安裝

```bash
pip install git+https://github.com/columnbb/binance-trading-toolkit
```

## 使用

```python
from binance_trading_toolkit import BinanceFuturesClient, BinanceConfig

client = BinanceFuturesClient(BinanceConfig(
    api_key=os.environ["BINANCE_API_KEY"],
    api_secret=os.environ["BINANCE_API_SECRET"],
    # 先測試網驗證：base_url="https://demo-fapi.binance.com"，
))

positions = client.open_positions("BTCUSDT")

result = client.market_order("BTCUSDT", "BUY", 0.01)

# 原生停損：多單用 side="SELL" 觸發市價平掉整個部位
client.stop_market_close_position("BTCUSDT", "SELL", stop_price=60000.0, position_side="LONG")
```

## 已知會踩的坑（先寫下來，還沒實測驗證過）

- `closePosition=true` 時**不能同時帶 `quantity`**——交易所會自己抓當下的
  完整部位量。官方文件明確說明，`stop_market_close_position()` 已經照這個
  規則寫，但沒有真實驗證過違反時的實際錯誤訊息長怎樣。
- **對沖模式（Hedge Mode）下 `closePosition` 有方向限制**：`LONG` 部位不能
  配 `BUY`、`SHORT` 部位不能配 `SELL`。呼叫端要自己確保 `side` 傳對方向。
- 合約規格的最小變動單位要從 `symbol_filters()` 的 `filters` 陣列抓
  （`PRICE_FILTER.tickSize`／`LOT_SIZE.stepSize`／`MIN_NOTIONAL.notional`），
  官方文件明確警告不要直接用 `pricePrecision`/`quantityPrecision`
  ——這裡的 `extract_filters()` 已經照這個規則寫。

## 測試

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

測試完全不打網路，用假的 `requests.Session` 驗證簽章公式與請求格式；
簽章公式額外跟 `sepa_vcp_screener` 的已驗證實作逐位元組比對過。

## 現況

2026-08-19 從零開始建立（不像 `mexc-futures-toolkit` 有既有實作可以合併），
根據 Binance 官方文件撰寫。尚未接進 `ed-seykota-systematic-trend-following`
——那個專案目前的下單邏輯（`_send_entry`）本來就對真實下單有硬性擋機制
（"protective stop-order integration must be verified first"），接進來時
會保留這個安全機制，不會因為換了客戶端就解除。
