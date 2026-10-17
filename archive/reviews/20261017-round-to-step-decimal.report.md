# round_to_step 改用十進位（0.4.1）審閱歸檔

- PR：[columnbb/binance-trading-toolkit#12](https://github.com/columnbb/binance-trading-toolkit/pull/12)
- 合併：`b5858cb`（development，squash）；PR head `07cef2684b56230344f63e5a567f48e875fa20df`；base `07d2c5bd86d81e41187c6b3a6322bd6c1ee1cdd7`
- 審閱：ChatGPT，1 輪 PASS（審閱PR#12結論：PASS）
- CI：pytest SUCCESS（run 39027029524）；209 passed（baseline 193，+16）
- 問題：`round_to_step` 用 `math.floor(q / step) * step`，浮點下 85721.9/0.1=857218.9999999999，已對齊的值被多捨去一檔。實測 BTC 價格（tick 0.1）約 33.1%、數量（step 0.001）約 12.8% 受影響。2026-10-17 A1 加密策略首筆真倉掛停損時發現（要 85721.9、交易所掛 85721.8）。數量少一手對平倉單會留殘倉。
- 修正：改用 `Decimal(repr(float))` 做 ROUND_FLOOR；step 為 None／≤0 原樣；非有限數字丟 ValueError（舊版 NaN 靜默變 NaN）；版本 0.4.1。tests/test_round_to_step.py 整段掃描（120 萬 tick、各 2 萬數量）。
- 審閱提醒：ValueError 不會被 `except BinanceAPIError` 接住，各策略升級釘選時要核對上層例外處理；`_trim` 以八位小數為契約；測試不是三個下單方法的端到端 payload 測試。
- 沒做：`smoke_test.py`、`gate1_validation.py` 同樣的浮點 floor（非交易路徑，另案）；各策略換釘與 A1 部署（各自審閱，部署要問使用者）。
- 隔離聲明：審閱端只讀 GitHub；PASS 不授權換釘或部署。
