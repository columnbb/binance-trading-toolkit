# binance-trading-toolkit

共用的 Binance USDⓈ-M 合約（futures）私有 API 客戶端，給
`ed-seykota-systematic-trend-following` 用。

## 驗證狀態——請先讀這段

**2026-08-20 用真實 Binance Futures Demo Trading 測試網帳號完整跑過一輪
「開倉 → 掛原生停損 → 查詢確認 → 取消 → 平倉 → 確認歸零」，全部成功。**
過程中抓到一個文件沒講清楚、會直接讓下單失敗的重大差異：

**Binance 已經把條件單（STOP_MARKET/TAKE_PROFIT_MARKET 等）移到獨立的
Algo Order API，不是原本官方文件字面上寫的 `/fapi/v1/order`。** 第一次
實測時，照官方 New Order 文件寫的 `POST /fapi/v1/order` + `type=STOP_MARKET`
被直接拒絕：

```
HTTP 400：Order type not supported for this endpoint. Please use the Algo Order API endpoints instead.
```

正確端點是 `POST /fapi/v1/algoOrder`（`algoType=CONDITIONAL`），而且**觸發價
的參數名稱也不同**：`triggerPrice`，不是 `stopPrice`。對應的查詢/取消也是
獨立端點（`GET /fapi/v1/openAlgoOrders`、`DELETE /fapi/v1/algoOrder` 用
`algoId` 不是 `orderId`）。`stop_market_close_position()`/`cancel_algo_order()`/
`open_algo_orders()` 已經改用正確端點，用真實測試網驗證過三次（含一次
專門驗證取消流程），回應欄位（`algoId`、`algoStatus`、`triggerPrice`……）
也都對過。**如果沒有實測，這個 client 會在第一次嘗試掛原生停損時直接
失敗**——這正是為什麼「照文件寫」跟「真的驗證過」之間的信心水準要分開講。

其餘部分（簽章、`market_order`、`open_positions`、`account_balance`、
`exchange_info`/`symbol_filters`、`ticker_price`）都在同一次測試網驗證中
確認正常。簽章公式額外跟 `sepa_vcp_screener` 的
`execution/testnet_client.py`（已在 Binance 現貨測試網驗證過）逐位元組
比對過完全相同。

**這組測試網金鑰跟 `sepa_vcp_screener` 用的現貨測試網金鑰是兩個完全獨立
的系統，不能共用**——帳號體系本身就是分開的，必須另外申請。目前官方
主推的申請入口是「Demo Trading」：https://demo.binance.com/en/futures/BTCUSDT
——用既有的真實 Binance 帳號登入後切換到 Demo Trading 模式產生金鑰（會
拿到虛擬資金），對應的 API 網址確認是 `https://demo-fapi.binance.com`
（已實測連得通）。

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
    # 測試網（已實測連得通）：base_url="https://demo-fapi.binance.com"
))

positions = client.open_positions("BTCUSDT")

result = client.market_order("BTCUSDT", "BUY", 0.01)

# 原生停損：多單用 side="SELL" 觸發市價平掉整個部位
client.stop_market_close_position("BTCUSDT", "SELL", stop_price=60000.0, position_side="LONG")
```

## 交易帳本、審計報表、真實下單驗證

跟 `mexc-futures-toolkit` 同樣的三個模組，介面刻意保持一致：

```python
from binance_trading_toolkit import TradeLedger, generate_report, run_smoke_test

ledger = TradeLedger("audit/trading_ledger.jsonl")

# 真實小額（預設 100 USDT 名目）開倉 -> 掛原生停損 -> 查詢確認 -> 取消 -> 平倉。
# 執行前會先檢查帳上沒有既有部位、換算出的金額沒超過上限；任何一步出錯
# 都會嘗試緊急平倉/取消，收尾時再次確認帳上真的歸零。
run_smoke_test(client, ledger, symbol="BTCUSDT", confirm=True)

from binance_trading_toolkit.report import load_events
report_text = generate_report(load_events("audit/trading_ledger.jsonl"))
```

```bash
python -m binance_trading_toolkit.report --ledger audit/trading_ledger.jsonl --print
```

`run_smoke_test()` 內部已經處理好 Binance 特有的兩個坑：原生停損走 Algo
Order API、市價單回應不帶成交明細（改用 `open_positions()` 查真正的進場
價/量）。跟 `confirm` 一樣沒有預設值的還有安全機制本身——開跑前檢查沒有
既有部位、金額上限、任一步出錯就緊急清倉、結束時確認帳上歸零，全部原封
不動保留。**這支會送出真實訂單，執行前務必自己確認情境安全**（測試網
帳號，或帳上沒有其他部位的小額子帳戶）。

## 已知會踩的坑

- **條件單要走 Algo Order API，不是 `/fapi/v1/order`**（見上方驗證狀態）
  ——2026-08-20 真實測試網實測發現，最容易踩、而且官方文件字面上沒講清楚。
- `closePosition=true` 時**不能同時帶 `quantity`**——交易所會自己抓當下的
  完整部位量（已實測驗證）。
- **對沖模式（Hedge Mode）下 `closePosition` 有方向限制**：`LONG` 部位不能
  配 `BUY`、`SHORT` 部位不能配 `SELL`（官方文件記載，測試帳號目前是
  One-way 模式，這條限制本身還沒實際觸發驗證過）。
- 合約規格的最小變動單位要從 `symbol_filters()` 的 `filters` 陣列抓
  （`PRICE_FILTER.tickSize`／`LOT_SIZE.stepSize`／`MIN_NOTIONAL.notional`），
  官方文件明確警告不要直接用 `pricePrecision`/`quantityPrecision`
  ——已實測驗證這樣抓得到正確數值。
- 市價單（`market_order`）的回應在測試網上觀察到 `status="NEW"`、
  `executedQty="0"`、`avgPrice` 缺值——不像 MEXC 或 Binance 現貨那樣在
  下單回應裡就同步帶回成交明細。要拿到實際成交價/量，得另外查詢部位
  （`open_positions()`）或查訂單狀態，`OrderResult.executed_qty`/
  `avg_price` 目前不能保證下單當下就有值。

## 測試

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

測試完全不打網路，用假的 `requests.Session` 驗證簽章公式與請求格式；
簽章公式額外跟 `sepa_vcp_screener` 的已驗證實作逐位元組比對過。

## 現況

2026-08-19 從零開始建立（不像 `mexc-futures-toolkit` 有既有實作可以合併），
根據 Binance 官方文件撰寫；2026-08-20 用真實 Demo Trading 測試網帳號完整
驗證過一輪開倉/掛原生停損/取消/平倉，並修正了條件單端點錯誤（見上方
驗證狀態）。尚未接進 `ed-seykota-systematic-trend-following`——那個專案
目前的下單邏輯（`_send_entry`）本來就對真實下單有硬性擋機制
（"protective stop-order integration must be verified first"），接進來時
會保留這個安全機制，不會因為換了客戶端就解除。
