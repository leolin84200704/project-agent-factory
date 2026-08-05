# Proposal — 修「資料沒到下游」之前，先確認那筆資料真的由你這端送出

> Unreviewed inbox item (dream 2026-08-04). Single-domain evidence so far → not a PR.
> Promote only if a second, different-domain case shows up.

## Lesson (de-identified, standalone form)

修一個「下游收到的欄位是空的」bug 時，第一步不是往上游追資料，而是**打開自己這端送出去的
payload/DTO 型別，確認那個欄位存在**。服務內部有同名變數、內部邏輯正確地算出了值、
甚至內部有一個叫同樣名字的欄位 —— 都不代表它會離開這個服務。

若跳過這一步，會修好一個**不影響輸出的內部值**，而且**沿路每一個訊號都會是綠的**：
單元測試綠、PR 論述有說服力、deploy 乾淨、live 驗證通過。因為那些訊號測的是
「我改的東西有沒有正確運作」，不是「我改的東西是不是問題所在」。

配套：跨服務不能外推「哪個欄位決定 X」。同一個概念在鏈上每個服務可能有不同判準
（實測一條四層的鏈上有三種互不相同的判準），必須逐個服務讀 code。

## Evidence

- **LIS / EMR 訂單 address（2026-08-04, VP-17591）**：ticket 症狀是下游收到的病人地址是空的。
  我逐層驗證了「本服務內部拿不到有效 address」（結論正確），修好了挑選邏輯、加了測試、
  merge、deploy、live 驗證全過 —— 之後才發現送出去的 `PlaceOrderRequest` DTO **根本沒有
  address 欄位**，只送 patient id，地址由下游自行從病人記錄組出。修的東西不影響症狀。
  同一張票還附帶一個外推失誤：我斷言某個 `is_primary_address` 欄位是主因，它在本服務內成立，
  但鏈上另外兩個服務**從來不看那個欄位**。

## Why it might be universal

它規範的是一種工作方式（修下游資料問題前先驗證輸出介面），不描述任何特定系統的行為，
且與「綠色訊號測的是正確運作而非正確方向」這個更一般的觀察相連。

## Why it is NOT a PR yet

只有一個 domain 的一個案例。CONTRIBUTING 要求 >=2 個**不同 domain** 的案例。

## Family note

與已 merge 的三條（`void` 不丟棄 rejection / 搜尋 flag 靜默降級 / repo 部署設定不是 runtime 真相）
是同一形狀：**某環節看起來正常，但沒驗證它真的在做我以為的事**。
若之後要合併，可考慮把四條收斂成一條有名字的 family lesson —— 觀察到的失效模式是：
四條各自獨立記錄時，沒有任何一條救得了下一條。
