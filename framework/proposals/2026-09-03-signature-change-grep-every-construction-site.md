# Proposal: 改一個型別的建構簽名時，grep 每一個建構點——包括測試檔裡的局部 helper；只做 build 的 pre-push 不會替你檢查

Status: unreviewed inbox item (2026-09-03, lis dream distillation)
Bar check: **fails cross-domain evidence** — 目前只有一個 employer domain 的一次事件。
standalone、discipline-not-behavior 兩條通過。

## 候選教訓（standalone 版本，無 domain 名詞）

> 給一個類別/函式新增必填參數時，建構點不只有主要的 setup（`beforeEach`、factory、DI 容器），
> 測試檔內部常有自己 new 物件的局部 helper。改完後用「型別名 + `new`」或建構函式名 grep 整個
> repo（含測試），逐一對照參數數量。
> 同時確認本地 gate 實際跑了什麼：一個只跑 build/compile 的 pre-push hook 通常排除測試檔，
> 所以測試裡的型別錯誤會靜默通過本地、在 CI 才炸，並讓整個測試 suite 載入失敗、deploy 停在舊版。
> 推送前用與 CI 相同的指令與環境變數重現一次 test stage，而不是跑一組挑選過的 suite。

## 為什麼是 discipline 而非 behavior

規定的是變更簽名後的搜尋範圍與推送前的驗證方式，不描述任何測試框架或 hook 的行為。
可檢查化：簽名變更的 diff 若沒有觸及 grep 命中的每個建構點，即違規。

## 證據（單一 domain，這是它卡住的原因）

一次 service 新增第 7 個 constructor 依賴。作者更新了 spec 頂層的 `beforeEach`，漏掉兩個 describe
區塊內各自的 `buildService()` helper。本地：pre-push hook 只跑 `nest build`（spec 不在 build 的
tsconfig 裡）；針對性的 jest 跑法使用的 ts-jest diagnostics 比 CI 寬鬆——兩者都綠。CI：TS2554
「Expected 7 arguments, got 6」→ suite 無法載入 → test stage 紅 → deploy stage 未執行 → 兩個環境
停在合併前的 image，直到第二個 PR 補上兩行。用 `NODE_ENV=test … npx jest --ci` 在本地可完整重現。

## 若要升格成正典，還缺什麼

第二個**不同** domain 的案例：任何語言/框架下，介面變更漏掉測試內的建構點、且本地 gate
不涵蓋測試檔而在 CI 才失敗。
