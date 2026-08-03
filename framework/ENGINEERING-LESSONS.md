# Engineering Lessons — 通用工程紀律

> 從真實事故蒸餾的跨專案通則。每條都對應至少一次實際踩雷。
> 動 DB / 部署 / prod / migration 前先掃過對應章節。
> 新教訓寫入規則見 `RETRIEVAL.md` § Write-back：確定可泛化才進這裡，job-specific 留在該 instance 的 LTM。

## 驗證紀律

- **Test before push** — 會影響 prod 的 push 前必跑 unit test，且涵蓋新 logic 的每個分支。compile pass ≠ behavior correct。
- **Verified means live, not mock** — 別把 mock unit test 說成「線上驗證」。prod 行為的結論要用真實 DB / 部署中的 service 重現。
- **Verify peer-observed state** — 修 network/lifecycle 問題時，驗證對端可觀察的狀態（對方的 session count、connection 數），不只看自己這邊的 log。
- **Rolling deploy 完成的判準是新 pod 本身，不是 deployment 聚合欄位** — k8s rolling update 期間 `deployment.status.readyReplicas` 計入**舊** pod，所以「spec image 已更新 + ready≥1」會在新 pod 還沒起來時就成立，是假完成訊號；照它開跑驗證會打到舊版 code，測試結果全部作廢。正確判準：存在一個 **image == 目標版本** 且 `containerStatuses[].ready==true` 的 pod（必要時再確認舊 pod 已終止）。同理適用任何滾動替換系統：完成訊號要綁「新實例的身份+就緒」，不能用會混入舊實例的聚合計數。旁註：切換過程中單次 API 讀值可能出現 transient 異常值，關鍵判斷要重讀確認、不要憑單次讀值行動。
- **Batch DB verify 100%** — 批次 INSERT/UPDATE 後驗證全部 rows，絕不抽查。
- **JOIN scope reverse audit** — prod UPDATE-WHERE-JOIN 跑完後，用更廣的 criterion 反向 SELECT 找漏網 row；SQL 的 `NULL = NULL` 是 false，會靜默漏掉。
- **End-to-end equivalence before swap** — 替換 RPC/service 實作前，diff 前後 output 完全相同；proto 相同 ≠ marshalling 相同。
- **Channel liveness is third-party state — round-trip test it** — user-facing 通道（chat bot、webhook 推播、訂閱）的關鍵狀態存在第三方且可被外部行為者改寫；「己方註冊/發送動作執行成功」≠ 通道活著。deploy/驗收收尾必做一次真實 round-trip（實際收發各一則並確認）；長期監控要查第三方的實際狀態（如 getWebhookInfo）而非自己的 log，drift 要自癒並通知用戶。同一通道故障被用戶發現兩次 = 缺的是測試不是修復。
- **資料違反現行設定 ≠ engine bug — 先為設定變更定年** — 當存量資料看起來違反現行設定（預約落在可用時段之外、記錄超出現行 quota、舊值過不了現行 validation），第一個要排除的假設是「設定在資料寫入之後被改過」：先確認寫入當時生效的設定，再開始懷疑驗證引擎。設定 store 常沒有歷史（無 updated_at、無 audit log），但 delete-and-recreate 的儲存模式讓 surrogate key 順序成為可用的定年工具——某物件的整組 row id 若是全表最高，即證明它是最近被整批重寫的，可與資料寫入時間交叉比對出先後。反向教訓：任何 user 可自行編輯、又被引擎當驗證依據的設定表，要有 updated_at 或 audit，否則 triage 只能靠 id 推理，且縮小設定時應偵測並提示落在新設定之外的既有資料。
- **安靜的觀測窗不是故障證據 — 要量輸入端** — 「改完後 N 分鐘沒有預期產出」不能推論成壞了；必須先確認那段時間**輸入是否真的到達**。event-driven 系統的正確量法：topic/queue 的 offsets-by-timestamp（該窗內生產了幾筆）+ consumer group lag；**lag ≈ 0 代表系統把能拿的都吃了 = 健康且輸入為空**，跟「卡住」是完全相反的結論。真實事故（2026-07-31）：cutover 後 61 分鐘零產出，我判定失敗並回滾，實際上上游那小時就是沒有事件，事件在第 65 分鐘到達且新路徑處理正確——回滾反而造成 ~17 筆重複交付與 5 筆中斷的 in-flight 工作。配套兩條：(a) **看證據的時間戳，不只看數量**——「切換後有 64 筆事件」在確認每筆的到達時間之前毫無意義，那 64 筆全部在我量完零之後才到；(b) **檢查這個「故障」有沒有自己已經寫下來的替代解釋**——我先前才記錄過「這個系統有整小時零產出的自然空窗」，卻在自己的改動受嫌疑時忽略該 baseline。嚴謹的統計論證架在錯誤前提上，只會讓錯誤結論看起來更可信。
- **Read the helper before you build on it — stubs log success** — 依賴既有 helper/service method 前先讀它的實作，不能只看名字和回傳型別。真實案例：SFTP `moveFile()` 是空 stub — `ensureConnected()` 後直接 log "File moved successfully" 回 `{success:true}`，從未呼叫 client，任何 caller 都拿到假成功且無錯誤可觀察。判斷法：宣稱有副作用的 method 內若沒有對應的 client/IO 呼叫即是 stub；接手時先補真實作（含 timeout）+ 行為級 unit test，再開始建上層功能。

## 變更範圍紀律

- **Config coupled with code** — 新增任何 `process.env.X`（或等價設定），同一個變更裡就更新所有部署 config（yaml/ConfigMap），並明說改了哪些。
- **Schema migration before deploy** — 對非 ORM-managed 的 prod DB，schema 加欄位必須先手動 ALTER 到 prod 才部署，否則整個 model 的讀取會全數失敗。
- **Audit callers when adding fallback** — 加 fallback 機制時，同一個 PR audit 並遷移所有同類 caller；revert 一個 service 時 audit 同 server 其他同類風險。
- **Sync sibling encodings of an invariant** — 改一個值/邊界的語意時，同 function 內所有衍生點（顯示、驗證、查詢窗、search 邊界）要用 single source 一起改；測試別 mock 掉共用路徑（假綠燈）。
- **Migrate all readers, no mirror** — 替換 source-of-truth 時，遷移所有 reader（含 generic DTO mapper）然後停寫舊 store；「先雙寫留 mirror」只會 diverge。
- **Rollback 是有 blast radius 的變更，不是免費的還原鍵** — 「照 runbook 回滾」聽起來安全，但回滾本身會產生副作用，決策時要跟「維持現狀」一起評估。最常見的形態：**consumer group 切換**。A→B 切換期間 group A 停止消費、offset 停在切換點；切回 A 就從那個 offset 重放，下游若沒有 idempotency gate 就是**重複交付**（重複寫入若是覆寫同一列，row count 還會看起來乾乾淨淨，指標騙人）。硬規則：任一方向切換前，先把**目標 group 的 committed offset 推到 high watermark**（`admin.setOffsets`），或明確接受重放。真實案例：回滾重放 1,940 筆造成 ~17 筆重複；切回去前若不先推 offset，另有 8,501 筆待重放。另一個副作用：**重啟會殺掉 in-flight 的 job**（BullMQ 等），留下 `next_retry_at=null` 的孤兒列，不會自癒，必須事後逐筆補跑並驗證。
- **大改動拆成至少 3 個 commit，切在 revert 邊界上** — 一個把新元件、接線、infra/config 全塞在一起的肥 commit，無法只 revert 有風險的那一片（k8s manifest / prod config），要退就得連安全的部分（新 service + 測試）一起退。寫 code **之前**就規劃邊界：(1) 新能力 + 其單元測試（未接線、inert）→ (2) 接進呼叫端（真正的行為改變）→ (3) infra/config/依賴（最難 revert 的放最後且獨立）。附帶約束：branch 一旦 push，回頭拆需要改寫歷史，而 force-push 在工作 repo 是禁止的——所以要嘛一次切對，要嘛用新 branch 重建。
- **Capability flag enables a pipeline** — backfill 或開 flag 前，確認下游 pipeline 的相依 config 都在，否則 flag 開了功能還是斷的。

## 設計判斷

- **Diagnosis in the agent layer, facts in the app** — 系統有 LLM agent 常駐監看時，失敗「診斷」（根因推理、跨表反查、修法建議）放 agent 層：偵測到失敗後由 agent 查證並把英文結論寫回 DB 的 detail 欄位；app code 只記錄裸事實（錯誤碼、上游回應）並提供可寫欄位。診斷邏輯寫進 app 會讓每個新失敗型態都變成一次 code deploy，而 agent 層改 prompt 就能涵蓋。配套硬規則：短 TTL 的證據（container log 會被下一次 redeploy 洗掉）必須由 agent 在**偵測當下**快照到耐久儲存（repo/檔案），不能等事後查——事後永遠查不到。
- **Prefer stable id over name matching** — 有 unique id 可用時不要用 display name 做 matching key，主動建議改用 id。
- **Owner-scoped, not actor** — 解析 per-owner 屬性（timezone、settings）要用資源擁有者的 id，不是 JWT actor 的；admin 代操作會打破 actor lookup。
- **Cleanup filter scope** — prod DELETE 的 WHERE 必須綁定當次操作範圍（時間 + 明確 ID），絕不為「保險」放寬 filter。
- **No overgeneralize from single case** — 寫通則前 grep 多個 case 確認真正原因，別從單一 case 反推普遍規則。
- **Headless LLM CLI 預設載入整個 harness context** — 用 agent CLI 的 headless/print 模式（如 `claude -p`）當純文字 worker 時，它預設會把整個工具 schema、所有 MCP server、專案 memory 檔、動態 system prompt 都塞進 context——實測連一個字的 prompt 也可能 ~170K token/call，其中工具 schema 是最大宗（非 MCP）。純文字進、JSON 出、不呼叫任何 tool 的任務要明確關掉：`--tools none` + 空 MCP config + 極簡 system prompt + 關動態 section，可降 ~30×。別假設 headless 模式就是精簡的；用 `--output-format json` 看 `usage` 逐項驗證哪個開關真正省 token。
- **Idempotency key 不可被 terminal failure 消耗** — 「先插 unique row 再處理」是正確的併發防護，但 resend 分支必須看既有 row 的**狀態**再決定：處理中/已成功 → 短路回 duplicate；terminal failure（驗證失敗、拒絕、下游錯誤）→ 用 status-gated 原子 UPDATE reclaim 該 row 重跑（N 個併發 resend 恰一個搶到）。否則「修正問題後重送同 key」這個 idempotency 的核心使用情境永遠走不通，且呼叫端拿到的 5xx「可重試」承諾是假的。配套：若 pipeline 是 charge-first / 先產生副作用再完成，副作用完成的**當下**就要把憑證（payment id 等）持久化到該 row，retry 時 replay 而非重做——不然 failed 狀態永遠不敢放開重試。Doc 寫了 retry 語意不代表 code 有實作，兩邊都要驗。
- **Shared-quota bottleneck ≠ the segment you optimized** — 當一個 pipeline 撞共用資源限制（rate limit、訂閱 token 額度、連線池），把某段的單位成本砍 N× 後若整體吞吐只增 ~1.5×，瓶頸就不在那段——是另一個共用同資源的消耗者（常是同時在跑的互動 session 或別的 job）。先量「單位成本 vs 吞吐」的比例再下結論，別繼續優化錯的地方；要清 backlog 就讓該工作獨佔資源（無人值守／錯開時段）跑。


## 流程紀律

- **測試中發現的缺陷必須當場變 ticket，筆記不是追蹤** — E2E/測試/triage 過程中發現「不屬於當前工作範圍」的真實缺陷時，必須在同一個 session 內開正式 ticket（有 owner、有狀態、連結 parent），然後筆記引用 ticket id——絕不能反過來只在 memory/STM 寫一句 "proposed follow-up" 就結案。筆記沒有 owner、沒有 queue、對排程不可見；只存在筆記裡的已知缺陷功能上等同未知缺陷，而重新發現的成本往往由外部使用者/夥伴付。收尾檢查：關 ticket 前掃自己的工作紀錄，任何 "follow-up"/"wrinkle"/"proposed" 字樣而沒有對應 ticket id 的，都是還沒歸檔的缺陷。
- **Probe capability, not just errors** — 只對「有流量的錯誤」告警的監控，永遠抓不到零流量通道的損壞（域名失效、對端主機下線的低頻/休眠 integration）。對所有 enabled 的外送通道要週期性探測「能力」本身（DNS 解析、TCP 可達），否則通道壞掉的發現時間 = 下一筆真實流量失敗的時間，而那筆流量往往就是損失本身。
- **Preserve evidence before restart** — hang 的 pod/process 在 restart/delete 前先 dump logs + describe，否則 root cause 隨 GC 永久消失。
- **Check live state before migration work** — 遷移/cutover 類工作開工前先讀 live config + 環境實況；ticket 系統的狀態 ≠ 環境實際狀態。
- **Worktree for parallel branches** — 多 branch 平行/stacked 工作用 git worktree，不要 in-place checkout（per-worktree 重建 deps）。
- **輸出看起來錯但你的文字沒錯 → 去找顯示層，並用自己的 transcript 舉證** — 用戶反覆指出同一個「你寫錯了」而你的原始文字其實正確時，錯誤在**下游的顯示/轉換層**，那才是要修的地方；用「應該是渲染問題」結案等於把問題留給用戶重複踩。舉證方法：grep 自己的 session transcript（`~/.claude/projects/<slug>/*.jsonl`，解析 JSONL 並把 assistant 的 `text` block 與 `tool_use`/`tool_result` 分開統計），先證明自己到底有沒有輸出過那個字串，再往顯示層追。真實事故：用戶三次（相隔 11 天）指出 Jira 連結域名錯，前兩次我 grep repo/config 都沒找到就寫下「大概是 render-time 替換」；第三次去 grep transcript 才證明 assistant text 從未出現該字串，接著在**舊 session** 裡找到根因——一條 agent 自己寫的終端機 hyperlink 規則（`~/.wezterm-local.lua`，且當初刻意不進 git，所以任何 repo 搜尋必然是空的），把畫面上每個 ticket id 都連到錯誤網域。
- **grep 找不到只界定了搜尋範圍，不證明不存在** — 「我 grep 過，沒有」是無效結論，除非同時說出搜尋範圍。上面那個 case 前兩次搜的是 repo 檔案 + `~/.config` + `~/.zshrc` + `~/.wezterm.lua`，而目標檔案是 `~/.wezterm-local.lua`（刻意未追蹤）。報告時一律寫成「在 X、Y、Z 範圍內沒有」，並主動問「還有哪些地方可能承載這個行為」。
- **絕不從使用者的 email 域名推導組織的 URL/識別碼** — 登入信箱網域 ≠ 組織的服務網域（`hung.l@zymebalanz.com` vs Jira 站 `vibrantamerica.atlassian.net`）。不知道正確值就去查真實來源（既有連結、API、config）或直接問；一次憑空猜測換來三次打擾、一個錯誤的補救方案，以及 22 天畫面上每個 ticket id 都指向不存在的站。
- **repo 裡的部署設定不是 runtime 真相 — 而且兩個方向都會騙你** — 版控裡的 manifest/ConfigMap 常年與實際部署 diverge（真正生效的是 cluster 裡的物件、外部 secret manager、或某台機器上的未追蹤檔案）。兩種相反的誤讀都踩過：(a) **設定裡沒有 → 誤判功能沒在運作**：某個環境變數在 repo 的所有 manifest 都找不到，我因此推論它從未被注入、對應欄位長年為空，準備開 bug ticket 並為了消警告把該欄位的寫入從 code 拿掉；實際查 prod 是 5370/5372 筆都有正確值——那個變數由實際部署注入，repo 的 manifest 目錄根本不是部署來源。(b) **設定裡有 → 誤判功能存在**：新服務留著上一代的設定佔位符（DSN、webhook URL），但整個 SDK/實作都沒遷移過來，所以那些 key 是「曾經有這個能力」的化石，不是「現在有」。硬規則：對「這個東西有沒有在運作」的判斷，一律去 runtime 取一筆真實資料（prod 一個 `COUNT` 就夠）再下結論；**絕不讓 linter/pre-commit 警告驅動 code 決策**——我當時為了讓檢查通過，把該欄位的寫入直接從新路徑移除，那是真的行為退化（該路徑不再記錄是哪個實例處理的），只為消一個誤報。要嘛修工具，要嘛在 commit 說明為何豁免，不要改產品行為去迎合工具。附帶：這類 guard 若只檢查 ConfigMap，就會對「放在 secret 的變數」和「靠 downward API 注入的變數」永久誤報，該修的是 guard。**第三種變體最陰**：repo manifest 宣告了一個注入來源（`envFrom` 同時列 configMapRef 與 secretRef），我據此判斷「secret 才是憑證的正確位置」並照著改 config、還寫進 PR 說明；實際 deployment 的 `envFrom` **只有 ConfigMap，而那個 secret 根本不存在**——所以我的「更正確」改動完全不會生效，而且我在 ticket 裡叫別人去一個不存在的 secret 填值。教訓：**判斷「設定該放哪裡」跟判斷「功能有沒有在跑」一樣，都要問 runtime 而不是問 repo**——一條 `kubectl get deploy -o jsonpath='{...envFrom[*]}'` 就能問。旁證：該服務把其他憑證（PDF API secret、DB 密碼）全放 ConfigMap，本來就是「這裡沒有 secret 機制可用」的線索，我看到了卻解讀成「這個 repo 習慣不好」。

- **Debug 從 header/證據本身下手** — 例：debug CORS 永遠 grep `access-control-allow-origin` 本身；framework 可能回 204 + 其他 header 造成假象。
- **宣稱「更乾淨」前先驗隱藏相依** — 追求更好的設計沒問題，但改之前確認沒有隱藏耦合（env、cache、singleton）。
