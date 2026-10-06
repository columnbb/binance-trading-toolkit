# f-5 補救 — 把資金費同步搬到策略實際釘的 development 線

- PR：[columnbb/binance-trading-toolkit#8](https://github.com/columnbb/binance-trading-toolkit/pull/8)
- 合併：`17f4e4a`（development，merge commit）；PR head `ee13f08bbac434661481f88af8456cf54052e46e`；base `b88c4ee`
- 審閱：1 輪 **PASS**
- CI：pytest SUCCESS（run 37426106236）；192 passed
- 對應待辦：機隊工程進度 `f-5`
- 部署：不需要

## 為什麼有這個 PR（疏失紀錄）

f-5 的 toolkit PR #4／#6 是從 `main` 開的、合併進 `main`。策略釘的 `b88c4ee`（0.3.3）在 `development` 線上，**不在 `main`**：`main` 是較舊的線，缺 `order_by_id`／`order_by_client_id`、`is_order_not_found`、穩定 client id 參數、拒絕 HTTP 200 帶錯誤碼、保留明確「訂單不存在」錯誤。趨勢 #99 因此把釘子改成 `main` 上的 `4e4df6b`（會失去這些能力；尚未部署）；動能改釘時被自己的能力檢查擋下才發現。開發端開 PR 前沒確認「釘的 commit 是否在基底分支上」；審閱端也只看 diff（審閱者已承認依賴升版審查時沒核對新釘是否保留舊釘能力）。

## 做了什麼

把 `origin/main` 合進 `origin/development`（唯一衝突：`tests/test_client.py` 結尾兩邊都加測試，兩邊保留）。帶進 f-5 的 `funding_sync.py`、`income_history()`、測試、README、f-6 的 CI、三份歸檔；`funding_sync.py`／`tests/test_funding_sync.py`／`README.md` 與審閱過的 `4e4df6b` blob 相同；`client.py` 與 `tests/test_client.py` 相對 development 只新增（+25／+29 行）。合併後 `b88c4ee` 與 `main` 都是 development 的祖先。

## 後續（本 PR 不含）

- 趨勢 #101、動能：toolkit 釘子改到 `17f4e4a`；趨勢新增 `tests/test_toolkit_capabilities.py`（對錯的釘子實測 7 個失敗）。
- `main` 是否追上 `development`（走 PR）待使用者決定；之前 development 與 main 的分歧已造成這次釘錯。

## 隔離聲明

審閱與開發端都沒有部署、沒有下單。
