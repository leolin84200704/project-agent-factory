# Proposal — 「正在跑的是哪份 code」只能用產物內容證明，版本標籤會騙人

Status: unreviewed inbox item (2026-07-29, from LIS instance dream)
Target if accepted: `framework/ENGINEERING-LESSONS.md` § 驗證紀律 — 接在既有的
「Rolling deploy 完成的判準是新 pod 本身」那條之後，作為同族的收斂版。

## 提議的教訓

**要確認某份變更是否真的在跑，唯一可信的證據是在執行環境裡檢查**建置產物的內容**（grep 出那個變更專屬的字串）。版本標籤、build 成功訊號、部署狀態欄位、單次 API 讀值，全部都是代理指標，每一種都可能與實際內容不符。**

推論出來的紀律：
- **先確認 code 有沒有真的在跑，再 debug code。** 否則會花時間去修一個不存在的 bug，或「修好」一個其實沒被執行的路徑。
- **建置系統打的版本標籤不等於它建的內容。** 常見實作是「用觸發 build 的 commit 當 tag，但 checkout branch 當下的 HEAD 來 build」——兩者在快速連續 merge 時必然分歧，而且**雙向**都會錯（標舊含新、標新含舊）。
- **對外介面的 404 vs 401（或等價的「路由不存在」vs「路由存在但拒絕」）是零副作用的線上探針。** 驗證部署後的路由可達性不需要製造任何真實資料。
- 觀測 API 的單次讀值不足以行動；關鍵判斷要重讀確認（實測過兩次讀到「存在且就緒」的實例，事後在系統裡完全查無此物）。

## 為何可能可泛化

規則本身不含任何領域名詞：任何有「原始碼 → 建置 → 部署」落差的系統都適用，包含
container 部署、行動 App 的版本、CDN 上的前端 bundle、serverless function 版本。

## 為何**還沒**開 PR（誠實記錄未達標的 bar）

**Cross-domain evidence 不足。** 目前 5 個案例（部署未發生、image tag 與內容不符、
rolling update 聚合欄位假完成、phantom 觀測讀值 ×2、本地 checkout 過時）全部來自
同一個 employer domain 的同一套後端基礎設施，只算 ONE domain。等到在別的領域
（例如前端 bundle 快取、行動 App 版本、或別的雇主/專案）踩到同族問題，再升級成 PR。

## 證據（de-identify 前的原始出處，僅供 Leo 追溯）

- 2026-07-29 VP-17531：PR merge + CI deploy job `success`，但實際部署系統落後 ~2h；
  對舊 code 測外部路由連續 404，差點去修一個不存在的框架 bug（陣列路徑其實合法）。
  兩次 phantom 觀測讀值（讀到 pod「Running true <sha>」，ReplicaSet 事後查無此物）。
- 2026-07-29 HL7FAIL-20260729：pod image 標著 A 版 commit，實際 dist 內含 B 版 code
  （build 系統 tag/checkout 語意不同）——雙向皆已實測。
- 2026-07-27 VP-17497：`deployment.status.readyReplicas>=1` 假完成，對舊 pod 白跑 30 分鐘 E2E。
- 2026-07-02 FHIR-ONDEMAND-RESULT：本地 checkout 落後 origin，差點導出錯誤的 auth 結論。

已寫入 LIS instance 的 `long-term-memory/patterns.md`（含完整假訊號對照表）。
