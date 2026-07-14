# Engineering Lessons — 通用工程紀律

> 從真實事故蒸餾的跨專案通則。每條都對應至少一次實際踩雷。
> 動 DB / 部署 / prod / migration 前先掃過對應章節。
> 新教訓寫入規則見 `RETRIEVAL.md` § Write-back：確定可泛化才進這裡，job-specific 留在該 instance 的 LTM。

## 驗證紀律

- **Test before push** — 會影響 prod 的 push 前必跑 unit test，且涵蓋新 logic 的每個分支。compile pass ≠ behavior correct。
- **Verified means live, not mock** — 別把 mock unit test 說成「線上驗證」。prod 行為的結論要用真實 DB / 部署中的 service 重現。
- **Verify peer-observed state** — 修 network/lifecycle 問題時，驗證對端可觀察的狀態（對方的 session count、connection 數），不只看自己這邊的 log。
- **Batch DB verify 100%** — 批次 INSERT/UPDATE 後驗證全部 rows，絕不抽查。
- **JOIN scope reverse audit** — prod UPDATE-WHERE-JOIN 跑完後，用更廣的 criterion 反向 SELECT 找漏網 row；SQL 的 `NULL = NULL` 是 false，會靜默漏掉。
- **End-to-end equivalence before swap** — 替換 RPC/service 實作前，diff 前後 output 完全相同；proto 相同 ≠ marshalling 相同。
- **Channel liveness is third-party state — round-trip test it** — user-facing 通道（chat bot、webhook 推播、訂閱）的關鍵狀態存在第三方且可被外部行為者改寫；「己方註冊/發送動作執行成功」≠ 通道活著。deploy/驗收收尾必做一次真實 round-trip（實際收發各一則並確認）；長期監控要查第三方的實際狀態（如 getWebhookInfo）而非自己的 log，drift 要自癒並通知用戶。同一通道故障被用戶發現兩次 = 缺的是測試不是修復。

## 變更範圍紀律

- **Config coupled with code** — 新增任何 `process.env.X`（或等價設定），同一個變更裡就更新所有部署 config（yaml/ConfigMap），並明說改了哪些。
- **Schema migration before deploy** — 對非 ORM-managed 的 prod DB，schema 加欄位必須先手動 ALTER 到 prod 才部署，否則整個 model 的讀取會全數失敗。
- **Audit callers when adding fallback** — 加 fallback 機制時，同一個 PR audit 並遷移所有同類 caller；revert 一個 service 時 audit 同 server 其他同類風險。
- **Sync sibling encodings of an invariant** — 改一個值/邊界的語意時，同 function 內所有衍生點（顯示、驗證、查詢窗、search 邊界）要用 single source 一起改；測試別 mock 掉共用路徑（假綠燈）。
- **Migrate all readers, no mirror** — 替換 source-of-truth 時，遷移所有 reader（含 generic DTO mapper）然後停寫舊 store；「先雙寫留 mirror」只會 diverge。
- **Capability flag enables a pipeline** — backfill 或開 flag 前，確認下游 pipeline 的相依 config 都在，否則 flag 開了功能還是斷的。

## 設計判斷

- **Prefer stable id over name matching** — 有 unique id 可用時不要用 display name 做 matching key，主動建議改用 id。
- **Owner-scoped, not actor** — 解析 per-owner 屬性（timezone、settings）要用資源擁有者的 id，不是 JWT actor 的；admin 代操作會打破 actor lookup。
- **Cleanup filter scope** — prod DELETE 的 WHERE 必須綁定當次操作範圍（時間 + 明確 ID），絕不為「保險」放寬 filter。
- **No overgeneralize from single case** — 寫通則前 grep 多個 case 確認真正原因，別從單一 case 反推普遍規則。
- **Headless LLM CLI 預設載入整個 harness context** — 用 agent CLI 的 headless/print 模式（如 `claude -p`）當純文字 worker 時，它預設會把整個工具 schema、所有 MCP server、專案 memory 檔、動態 system prompt 都塞進 context——實測連一個字的 prompt 也可能 ~170K token/call，其中工具 schema 是最大宗（非 MCP）。純文字進、JSON 出、不呼叫任何 tool 的任務要明確關掉：`--tools none` + 空 MCP config + 極簡 system prompt + 關動態 section，可降 ~30×。別假設 headless 模式就是精簡的；用 `--output-format json` 看 `usage` 逐項驗證哪個開關真正省 token。
- **Shared-quota bottleneck ≠ the segment you optimized** — 當一個 pipeline 撞共用資源限制（rate limit、訂閱 token 額度、連線池），把某段的單位成本砍 N× 後若整體吞吐只增 ~1.5×，瓶頸就不在那段——是另一個共用同資源的消耗者（常是同時在跑的互動 session 或別的 job）。先量「單位成本 vs 吞吐」的比例再下結論，別繼續優化錯的地方；要清 backlog 就讓該工作獨佔資源（無人值守／錯開時段）跑。


## 流程紀律

- **Probe capability, not just errors** — 只對「有流量的錯誤」告警的監控，永遠抓不到零流量通道的損壞（域名失效、對端主機下線的低頻/休眠 integration）。對所有 enabled 的外送通道要週期性探測「能力」本身（DNS 解析、TCP 可達），否則通道壞掉的發現時間 = 下一筆真實流量失敗的時間，而那筆流量往往就是損失本身。
- **Preserve evidence before restart** — hang 的 pod/process 在 restart/delete 前先 dump logs + describe，否則 root cause 隨 GC 永久消失。
- **Check live state before migration work** — 遷移/cutover 類工作開工前先讀 live config + 環境實況；ticket 系統的狀態 ≠ 環境實際狀態。
- **Worktree for parallel branches** — 多 branch 平行/stacked 工作用 git worktree，不要 in-place checkout（per-worktree 重建 deps）。
- **Debug 從 header/證據本身下手** — 例：debug CORS 永遠 grep `access-control-allow-origin` 本身；framework 可能回 204 + 其他 header 造成假象。
- **宣稱「更乾淨」前先驗隱藏相依** — 追求更好的設計沒問題，但改之前確認沒有隱藏耦合（env、cache、singleton）。
