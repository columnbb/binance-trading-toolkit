# f-5 後續 — 資金費同步「整個帳戶」模式（symbol=None）

- PR：[columnbb/binance-trading-toolkit#6](https://github.com/columnbb/binance-trading-toolkit/pull/6)
- squash 合併：`4e4df6b`（main）；base `e728804`
- 審閱：1 輪 **PASS**（head `81db24df0ffe373de0313e487cff5491736e2f65`）
- CI：pytest SUCCESS（run 37419158837）；108 passed
- 對應待辦：機隊工程進度 `f-5`
- 部署：不需要（共用工具程式）

## 做了什麼

`sync_funding_fees(client, ledger, None)` 整個帳戶一次查（動能是專用帳戶、帳本混有 MEXC 時期幣名，逐 symbol 會被 HTTP 400 Invalid symbol 打斷）；壞 UTF-8 改丟 `FundingLedgerError`；README／docstring 驗證揭露改成 2026-10-06 實際唯讀驗證的內容。

## 審閱留下的邊界（接入策略時要遵守）

- 傳 `None` 要明確寫（參數沒有預設值）。
- 時間 watermark 模型不補「比已記錄最大時間更舊的晚到列」；PASS 不代表無條件完整回補。
- **同一份帳本不要混用 symbol 模式與整個帳戶模式**：只同步過 BTC 到 T 的帳本直接改 None 會跳過 T 之前其他 symbol 的歷史。動能接入 PR 要確認帳本沒有既有的 Binance per-symbol 資金費歷史（只有 MEXC source 的不會推前起點），或另設 backfill。
- 可再補的測試（非阻擋）：整個帳戶跨 symbol 同 time／tranId、整個帳戶 append 中斷專屬回歸。

## 隔離聲明

審閱與開發端都沒有部署、沒有下單；完整差異見同名 `.patch`。
