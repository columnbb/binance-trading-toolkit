# f-5 — Binance 資金費查詢與同步（共用工具程式這一塊）

- PR：[columnbb/binance-trading-toolkit#4](https://github.com/columnbb/binance-trading-toolkit/pull/4)
- squash 合併：`911e214`（main）；base `1882390`
- 審閱：4 輪（Perplexity），首審 BLOCK（F1–F3）→ 複審 BLOCK（F4）→ 第 3 輪 **PASS**（head `ad342f7dc6c10101bea4b5f475bdb0fb644320c3`）
- CI：pytest SUCCESS（run 37418642045）；103 passed（合併前 main 65）
- 對應待辦：機隊工程進度 `f-5`；一致性登記冊 `reconcile.funding_fee_sync`（動能、趨勢仍待辦）
- 部署：不需要（只改共用工具程式；策略端各自 PR 才會釘這個 commit）

## 做了什麼

`income_history()`（`GET /fapi/v1/income`，含 `page`）與 `funding_sync.sync_funding_fees()`／`total_funding_fee()`；寫 `funding_fee` 帳本事件（`source="binance_income"`，去重鍵 `symbol:time:tranId`）。

## 審閱抓到的四個缺陷（都已修）

- F1：以 `startTime = 最後一筆 + 1` 翻頁，會漏掉同一毫秒尾端的列 → 改成固定時間窗加 `page`，續跑起點含頭、靠去重補回。
- F2：逐列驗證逐列寫入，壞列前面已寫、起點已前進 → 整個窗口取回並全部驗證通過才寫，按時間由舊到新寫入。
- F3：NaN／Infinity 金額可入帳 → `math.isfinite` 擋下，時間不可讀也失敗。
- F4：append 被殺會留下沒有換行的半筆 JSON，略過它再 append 會黏行、起點前進而永久漏記 → 帳本任何壞行或尾端沒換行都 `FundingLedgerError`、查交易所與寫入之前就停手，恢復要人工修復帳本，不自動修復。

## 驗證揭露

2026-10-06 在 Demo 與 A1 正式站（唯讀、使用者授權）確認 `FUNDING_FEE` 欄位格式與官方文件一致；趨勢與動能是不同帳戶；Demo 的 `page` 被接受。沒有驗過多頁真實資料的分頁行為。

## 審閱留下的非阻擋事項（併入後續小 PR）

- 壞 UTF-8 會丟 `UnicodeDecodeError`（不是 `FundingLedgerError`）：統一包裝。
- README 與 docstring 仍寫「正式站尚未核對」，要改成上面的驗證揭露。

## 隔離聲明

審閱與開發端都沒有部署、沒有下單；A1 唯讀查詢只讀收益紀錄與餘額，不寫任何東西。完整差異見同名 `.patch`。
