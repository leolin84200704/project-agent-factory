# Proposal: 被中斷的終端輸出不是被回滾的交易——重試或修補之前先重讀狀態

Status: unreviewed inbox item (2026-09-03, lis dream distillation)
Bar check: **fails cross-domain evidence** — 目前只有一個 employer domain 的一次事件。
standalone、discipline-not-behavior 兩條通過。

## 候選教訓（standalone 版本，無 domain 名詞）

> 一個會寫入持久狀態的指令在你眼前被中斷（Ctrl-C、終端斷線、tool timeout、輸出被截斷），
> 「沒看到成功訊息」不能推論成「沒有寫入」。寫入方常在輸出被印出之前就已 commit。
> 決定下一步之前，先用一條獨立的讀取（另一個 connection / 另一個 client）確認目前狀態，
> 再從三選一：什麼都不做、重跑、或針對已存在的結果做修補。
> 盲目重跑會撞唯一性守衛或造成重複；盲目修補在目標不存在時是靜默 no-op——兩種錯都不會報錯。

## 為什麼是 discipline 而非 behavior

規定的是「中斷之後的第一個動作必須是讀，不是寫」這個工作順序，不描述任何資料庫或工具
如何處理中斷。可檢查化：中斷事件之後的下一條指令若是寫入且沒有前置的讀取，即違規。

## 證據（單一 domain，這是它卡住的原因）

一次 prod 資料新增（兩張表各一行，單一 transaction）：apply script 在輸出到一半時被中斷，
操作者當下不確定是否已寫入。實際上 transaction 已 commit（row 的 created_at 早於中斷時刻）。
若照直覺重跑 INSERT，會撞 script 自帶的 duplicate guard 而中止（結果正確但流程繞路）；
若直接對「以為存在」的 row 下 UPDATE 而 row 其實不存在，affected=0 會靜默通過。
本次是先 SELECT 才發現已存在，隨後以一條綁 id + 舊值的 UPDATE 完成修正（affected=1 changed=1）。

## 若要升格成正典，還缺什麼

第二個**不同** domain 的案例：例如一次雲資源建立、一次訊息發送、一次檔案上傳在輸出被截斷後
被重做而產生重複，或被跳過而造成缺漏。
