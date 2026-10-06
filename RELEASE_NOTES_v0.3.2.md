# Binance Trading Toolkit v0.3.2（候選發行說明）

> **狀態：** 僅存在於 `development` 的修正候選，尚未完成獨立 Debug、尚未建立 tag／GitHub Release、尚未更新任何 consumer、未安裝正式 venv，亦未部署。

## 修正內容

- `BinanceFuturesClient._decode()` 現在會將 HTTP 200 回應中攜帶的**負數** Binance `code` 視為錯誤，拋出既有 `BinanceAPIError`；呼叫端不會再把這類業務層拒絕誤判為成功。
- 例外訊息保留 Binance 的非秘密數字錯誤碼，供固定 ID 操作區分唯一允許的 `-2013 NO_SUCH_ORDER` 明確 absence；它不會包含簽名 URL、API key 或 API secret。
- HTTP 200 且 `code=0` 的成功回應維持原本語意。
- `stop_market_close_position()` 在收到 HTTP 200 負錯誤碼時，會回傳 `OrderResult(ok=False)`，使呼叫端依同一 `clientAlgoId` 觀測／收斂，而不是視為成功或分配第二個 ID。

## 新增回歸測試

新增純 fake-response 測試，涵蓋 HTTP 200 負錯誤碼拒絕、HTTP 200 `-2013` 明確 absence、HTTP 200 `code=0` 成功行為，以及 Algo STOP_MARKET 建單的 fail-closed 結果。開發端以 `PYTHONPATH="$PWD" pytest -q` 完整回歸通過。

## 發布前門檻與 non-goals

此候選必須先由獨立 Debug 在乾淨 worktree／testing branch 重新審閱與測試；只有 PASS 後才可向使用者申請建立不可覆蓋的 `v0.3.2` tag／Release。發布後仍需重新執行 Futures Demo Algo 固定 ID 測試；Seykota consumer pin、正式 venv、`/opt`、service／timer 與真倉均不在本候選範圍。
