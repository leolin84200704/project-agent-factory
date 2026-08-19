# Proposal: 斷言一個 gate / flag / 設定的行為之前，先找到寫入它的東西

Status: unreviewed inbox item (2026-08-18, lis dream)
Bar check: **fails cross-domain evidence** — 兩個案例都在同一個 employer domain。
其餘兩條（standalone、discipline-not-behavior）通過。放這裡等 Leo 判斷，未開 PR。

## 候選教訓（standalone 版本，無 domain 名詞）

> 要斷言某個 gate、旗標或設定值正在控制行為之前，先找到**寫入**它的程式碼，
> 不能只找到讀取它的那一行。只有 reader 而沒有 writer 的守門，讀起來像「保護存在」，
> 實際上恆真。同理，設定值的權威來源是**部署後生效的那份**，不是 repo 裡同名的檔案。

## 為什麼可能是 discipline 而非 behavior

它規定的是工作順序（在 X 之前先驗證 Y），不是描述某類系統怎麼運作。
可機械化成 review 檢查項：看到 `if (checkX(...))` 形態，grep 誰寫那個 key。

## 證據（同一 employer domain，這是它卡住的原因）

1. 某訊息消費者的「每筆只處理一次」守門：該函式**只讀不寫**，唯二的 writer 呼叫點都被註解掉，
   gate 恆真。旁邊另有一個真正生效的 dedup（永久性 history row），但語意不同
   （per-event 而非 per-item），因此實際行為與註解宣稱的不一致。
2. 同一次工作中，repo 的 `.env` 寫 `envm="stprod"`，部署後的 ConfigMap 是 `envm="prod"`，
   而載入器不覆寫已存在的環境變數 → 走的是另一個分支，該分支硬寫死逾時值並丟棄呼叫端傳入的參數。
   staging 看起來安全、production 會觸發 rebalance。
3. 另一張 ticket：一個收件人欄位由建立流程「抄一次」後永不同步，
   所以只讀 dispatch 端的程式碼永遠看不出值從哪來、為何過時（母體約 22% 已偏移）。

## 若要升格成正典，還缺什麼

第二個**不同** domain 的案例。形態應該是：某個被廣泛相信在生效的開關 / 權限 / 快取失效條件，
實際上沒有任何 writer，或 writer 只在初始化時跑一次。
