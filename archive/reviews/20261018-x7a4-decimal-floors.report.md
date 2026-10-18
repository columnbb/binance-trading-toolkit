# PR #15 審閱歸檔：x7a 步驟 4 smoke_test 與 gate1_validation 改十進位取整（0.4.2）

- PR：columnbb/binance-trading-toolkit #15
- 第 1 輪 head d7468c9edcbdcb21845db4b1c04ae83277f9f61e → BLOCK；第 2 輪 782b2d95db4d57b7b6c8bc78a42b3f23030027da → BLOCK；第 3 輪 dbd248d0b2f73248e0767e798b1c1052eb3f7419 → PASS（審閱PR#15結論：PASS）；base 6951f11c5c84e8307efaaac1dd96248218883475
- squash：ab4c117b2718b839b0ca247fe9b17c175077cc08
- CI：第 3 輪 run 39133371310，原始日誌 238 passed
- BLOCK 重點：①stop_price_below_entry 先把 Decimal 乘積轉 float 才對齊 tick，會把略低於 tick 邊界的精確值抬高（1.0／0.10000000000000002／0.1 應為 0.8）；②對齊後最後一次 float() 仍可能高於精確停損價（怪 tick 0.12345678901234566）。修正：全程 Decimal 到對齊後才轉 float，並以 _not_above 把結果退到 repr 不高於精確值。
- 內容：新增 round_up_to_step；quantity_for_notional、gate1 的價格／數量取整改 round_to_step／round_up_to_step；新增 stop_price_below_entry；版本 0.4.2；沒有策略 repo 使用這些函式，不重釘。
- 審閱者註記（非阻擋）：特殊 tick 退一個浮點單位後未必仍是 tick 整數倍（float 介面限制，另議）；round_to_step 的最後 float() 有同類現象但在交易路徑上、真實 tick 不會發生，本 PR 不動。
- 隔離聲明：審閱者只用 GitHub 唯讀資料、CI 日誌與本地純數值計算，未接觸 /opt /etc /var/lib、systemd、交易所、Google、實機帳本。
