# Work Loop — 通用 ticket 處理流程

> 框架版。佔位符由各 instance 的 CLAUDE.md 具體化：
> `{ticket_system}`（如 Jira via Atlassian MCP）、`{branch_pattern}`（如 feature/leo/{ticket_id}）、
> `{memory_root}`（instance repo 內的 storage/journal/long-term-memory 路徑）。

當收到 ticket 處理請求時，遵循以下流程。

## Step 1: Retrieve（檢索經驗）
0. **Sync first（最高指導原則，AGENTS.md 核心原則 0）**：`git fetch origin {branch}` 本 instance repo 與**這張 ticket 會碰到的**工作 repos（不是全部 repo、不是 `--all`），`git status -sb` 確認 ahead/behind；落後先 pull / rebase 再開始。**每個 repo 一次就夠**——確認沒 diverge 之後，後續讀檔一律走 Read/Grep 讀本地檔，不要用 `git show origin/main:檔案` 當讀檔手段（細節見 AGENTS.md 原則 0）
1. 讀取 STM / LTM `_index.md`（含 staleness check，見 `RETRIEVAL.md`）
2. Grep `{memory_root}/storage/short_term_memory/` 找類似的過去 ticket；有相似 ticket 就讀其 STM，特別關注 Failures 區段
3. Grep `{memory_root}/long-term-memory/` 找相關技術知識
4. **Lesson injection（sparse — 只挑 1~2 條）**：用 ticket 的關鍵字 grep `framework/ENGINEERING-LESSONS.md`（本檔所在 factory repo），挑**最相關的 1~2 條**（不要更多 — 實測 k=1 檢索勝過 k=4，塞多反而害）把原文抄進 STM 的 Ticket Analysis；Step 4 呈報方案時明說遵守了哪條。CLAUDE.md 的 hot-lessons 常駐區塊涵蓋最高風險紀律，此步補的是 task-specific 的其他條目。
5. **L4 驗證（含父單／姊妹單結案核對）**：從 `{ticket_system}` 取得 ticket 現況（status / comments / links）；若為遷移、拆分或系列子單，必先讀父單與已完成姊妹單的結案結論，排除已被證實為死碼或已退役的範圍。STM 說的一律以 ticket 系統為準；發現不同步，同一 turn 修正 STM。

## Step 2: Analyze（分析理解）
1. 從 `{ticket_system}` 取得 ticket 內容
2. 用 Agent 工具派 Explore 子 agent 調查相關程式碼和設定
3. 辨識 ticket 類型、影響範圍、相關 service
4. 草擬 1~3 個解決方案
5. 記錄到 STM: Ticket Analysis + Approaches Considered

## Step 3: Debate（正反辯論 — 按風險分級）
先分級再決定要不要辯論：
- **Routine**（完全照既有 pattern 的 config/設定類 ticket）→ **跳過 debate**，直接進 Step 4，但要在方案裡註明「依循 {過去 ticket} 的既有 pattern」。
- **Code change / 新 pattern / incident / 影響 prod 資料** → 派三個子 agent，**每個給不同的輸入、要自己去拿證據**：
  - 正方：輸入 = ticket + 推薦方案。為方案辯護，每個「可行」的主張要附它自己讀到的 code 位置（`file:line`）或查到的資料
  - 反方：輸入 = 推薦方案 + 相關 code，**不給**主 agent 的推理過程（避免被同一套框架帶著走）。
    質疑風險、邊界案例、更簡單的替代方案，至少做一次**實際的反證嘗試**（跑 query、重現、找反例 row），
    不能只寫推論；且**必問可測試性**：這個方案的測試會不會全是 mock？seam（注入點）在哪？寫不出行為級測試的部分是哪一塊？
  - 裁判：輸入 = 正反兩份報告與其附的證據，**不給**主 agent 的方案草稿推理。逐條把主張標成
    `evidenced`（附了自己拿的證據）或 `inferred`（只有推論）；**`inferred` 的主張不計入結論**，
    只列成「待驗證」帶進 Step 4。輸出：採納 / 修改 / 退回，加上待驗證清單
  - 主 agent 依裁判結果調整方案，記錄到 STM: Approaches Considered
- **為什麼不是加更多 agent 投票**：同一個模型、同一份輸入的 agent，錯誤高度相關，10 票一致 ≈ 同一個判斷講 10 次，
  不會讓信心度變高。信心度來自**輸入不同、證據不同**，以及確定性檢查（測試、staging A/B、prod 唯讀驗證），不是票數。
  agent 口述的「信心度 X%」沒有校準，不能拿來當放行門檻。
- **Verdict ledger**：每次 debate 結束，在 `{memory_root}/storage/debate_ledger.md` 追加一列
  `| 日期 | ticket | 裁判結論 | evidenced/inferred 數 | outcome |`，outcome 先留 `pending`，由 Step 8 回填。
  這份 ledger 是將來談「什麼條件下可以少一道人工 review」的唯一依據：**累積 ≥3 筆已回填 outcome 之前，不提任何自動放行門檻**；
  要提門檻時附上 ledger 的逐筆紀錄走 PR，由使用者決定。

## Step 4: Plan & Gate by Trust Zone（方案與信任區分流）
整理分析結果與 Test Plan（寫入 STM）：
```
## Ticket: {ticket_id} - {summary}
### 分析摘要
### 建議方案（含正反論點）
### Test plan（這個改動怎麼被驗證、seam 在哪、哪些部分只能 mock 及原因）
### 需要確認
```
**寫不出 Test plan 的方案不能進 Step 5**——測不動是設計問題，回頭改方案，不是進實作後再發現。
- **`zone: "autonomous"`（個人倉庫）**：只要需求明確、無 T3 花費與公開授權變更，記錄完 Test plan 即**直接進入 Step 5**，不暫停等人工確認。
- **`zone: "guarded"`（公司／團隊倉庫）**：若涉及跨服務契約、DB schema 變更或需求有歧義，暫停等使用者確認；明確的獨立修復可推進至預演驗證，唯 `main` 合併前必停。

## Step 5: Execute（執行實作）
1. 建立 Git branch（或獨立 worktree）: `git checkout -b {branch_pattern}`
2. **先寫行為級測試骨架，再寫實作**（照 Step 4 的 Test plan；骨架 = arrange/assert
   的空殼即可）。骨架寫不出來 = 設計缺 seam，回 Step 4 修方案，不要硬寫——
   順序本身強迫注入點存在
3. 逐步執行修改，每步驗證（含 `paf lint-code` 確保無 CJK、無未捕獲 `void` promise、無寫死環境變數 fallback 與憑證）
4. 失敗時立即記錄到 STM Failures，分析 root cause
5. 記錄到 STM: Code Changes

## Step 6: Deterministic Verify & Cross-Model PR Consensus（確定性驗證與雙異質模型共識）
1. 執行 `paf verify --repo {repo} --mode pre-push`，產生綁定 `HEAD` SHA 的 Verification Artifact（含 Dune static checks、build、typecheck、行為級 unit test、boot smoke）。新 logic 必附**行為級**測試（mock 掉共用路徑的假綠燈不算）。
2. **雙異質模型 PR 共識審查（預設標準）**：
   - 作者模型執行 `paf ship --repo {repo} --open-for-consensus --author-family <claude|gemini>` 推送分支並開立／更新附帶 Verification Artifact 與作者模型標籤的 PR。
   - 另一家族模型（`claude` <-> `gemini`）僅讀 `git diff`、程式碼與測試輸出進行獨立審查，並透過 `paf post-consensus --pr {num} --role reviewer --family <fam> --head-sha <reviewed_sha> --verdict <approve|request_changes>` 留下讀寫雙向綁定 `headRefOid` 的結構化留言（單一 GitHub 帳號架構下以 `paf-consensus` HTML 標記自聲明 `model_family`，嚴禁代簽非自身模型家族，亦不使用 GitHub 原生 Review Approve 按鈕）。
   - 若為 `request_changes`，作者模型直接在同一 PR 推修正 commit 重審（上限 3 輪；超過 3 輪死鎖或涉及消費者 UX 偏好決策時觸發 `PAUSED_FOR_CONSENSUS_TIEBREAK` 請使用者拍板）。只有異質家族能簽出有效 `approve`（當另一家族配額耗盡且 `.agent-verify.json` 顯式啟用 `consensus.allow_same_family_fallback: true` 時，僅接受帶有與作者不同 `session_id` 的全新空白唯讀 session 所簽出的 `fallback_same_family: true` 備援簽章），而任何家族在當前 SHA 留下的 `request_changes` 或 `needs_human_decision: true` 皆會阻斷合併；未帶獨立 `session_id` 或與作者同 `session_id` 的同家族 `approve` 一律不得撤銷同家族反對意見，且一旦 PR 帶有共識標記，本機 `paf record-review` 收據絕不可覆蓋共識閘門。
   - **跨機排程巡檢與不可偽造外層簽章（`paf watch-prs` / `paf init-consensus-key` / `paf install-watcher`）**：在一台 Mac 執行 `paf init-consensus-key` 產生 `0600` 密鑰（`~/.config/paf/consensus.key`），若雙機協作則透過安全通道（如 1Password / AirDrop / `scp -p`）將同一份 `consensus.key` 同步至另一台 Mac（標記內含 `key_id` 指紋，雙機密鑰不一致時 `paf` 會明確報出 `key_id mismatch`；注意 `--rotate` 會使進行中 PR 的舊簽章失效），並由 `paf install-watcher` 安裝單一 `launchd` `StartInterval` 排程執行 `paf watch-prs --once`（以 GitHub `ETag` `304 Not Modified` 零成本輪詢）。子程序模型（`claude` / `agy`）一律在剔除密鑰環境變數並以權限規則＋`0000` 檔案遮罩隔離密鑰的唯讀沙盒中輸出結構化 JSON，由外層 Python 檢查無密鑰外洩後壓上綁定 `base_branch` 與 `pr_number` 的 `HMAC-SHA256` `sig`；多台電腦共用同一 repo 時，僅 `author_host` 負責該 PR 的修正與合併，審查端則以 `<!-- paf-claim: ... -->` TTL 鎖避免重複審查並在休眠逾時後自動接手。

## Step 7: Auto-Merge on Consensus & Post-Merge Self-Healing（共識後自動合併與善後閉環）
1. Commit（明確指定路徑暫存，禁用 `git add -A`）: `git commit -m "[{ticket_id}] {描述}"`
2. **達成共識後由 Agent 自己合併（通則）**——依 `.agent-verify.json` 的明確 `zone`（缺 `zone` 一律 fail-closed）執行 `paf merge-consensus`（或 `paf post-consensus --head-sha <SHA> --auto-merge` / `paf ship`）：
   - **`zone: "autonomous"`（個人倉庫）**：一般程式碼 PR 一旦雙異質模型對最新 `headRefOid` 達成 `approve` 共識且 GitHub Actions CI 全綠，Agent **立即自己執行** `gh pr merge --squash --match-head-commit <SHA>` 原子合併進 `main`、刪除遠端分支、同步 `main` 並執行可攜式 `post-merge` 驗收（驗收失敗立即由 `paf` 自動開立 revert PR 回滾善後；綁定特定機器的動作如 `launchd/install.sh` 或 VPN 探針不可放進 `post_merge_check`，由宿主端 pull 後執行）。若基底衝突需從新 `main` 重建分支，重建後的新 SHA **必須經異質模型以 `git range-diff` 等方式補簽一次 `approve`** 才能合併；若 PR 修改了 `CORE.md`、`AGENTS.md`、`CLAUDE.md`、`.agent-verify.json`、`bin/paf`、hooks 等權限／治理檔案，則停在 `PAUSED_FOR_GOVERNANCE_APPROVAL` 等使用者確認一次（`--approved-governance`）。
   - **`zone: "guarded"`（公司／團隊倉庫）**：僅 `.agent-verify.json` `staging_branches` 白名單內的預演分支（預設 `staging`、`stage_test`）在達成共識、人類必要審查無阻斷且測試通過後由 Agent 自己合併並驗證；**其餘所有分支（`main`、`master`、`release/*`）一律在完成雙模型共識後停在 `PAUSED_FOR_MAIN_APPROVAL` 呈報使用者**。模型共識不取代人類同事 Code Review（GitHub `reviewDecision` 要求人類審查時 `paf` 自動阻斷），且工作區 `CLAUDE.md` 若有更嚴規則（例如由使用者本人合併 release PR）一律從其規定；使用者核准且符合工作區規則後，以 `--approved-main` 跑完閉環。
3. **文件比對** — 這次改的行為，去 fetch 描述它的文件並搜尋，確認仍然成立。
   新的 error code / reason / status 值也算。文件寫過舊行為時要明講「先前版本說 X，
   當時為真，現已不是」，不要默默覆蓋。仍然不寫尚未部署的行為。
   沒有相關文件就明講「無對應文件」，不要跳過這步。
4. 呈報最終結果 — 結案回報用 AGENTS.md「回報格式」的四段結案格式（發生什麼問題／為什麼／怎麼解／為何有效）

## Step 8: Retrospective（回顧反思）
讀取完整 STM 紀錄，進行反思：
1. **結果比較**: 初始分析假設 vs 最終結果
2. **失敗分析**: 每個失敗的 root cause、是否可預防
3. **模式識別**: 是否匹配已知 pattern、新發現
4. **教訓**: 技術面、流程面、工具面；每條做**下推檢查**——能否變成確定性機制
   （hook / grader / constraint / regression 題）而非 prose？參
   `framework/ENFORCEMENT-LADDER.md`，不變量日誌格式見 RETROSPECTIVE.md § 失敗分析
5. **信心度**: 1-5 自評（1=完全依賴指導，5=獨立完成）
   - **回填 debate ledger**：這張 ticket 若在 Step 3 有 debate，把 ledger 那列的 outcome 改成
     `clean`（上線後無 rework、無 incident）/ `rework`（上線後要再修）/ `incident`（造成事故）/ `abandoned`，
     並寫一句「裁判漏掉或抓到了什麼」。還沒上 prod 就保持 `pending`，等結案那次再填
6. **Factory 蒸餾檢查（必做，不是選項）**: 問「這次學到的東西有沒有跨 instance 可攜的？」有 → 立刻開 lesson PR 到 project-agent-factory（規格見該 repo CONTRIBUTING.md：一條 lesson 一個 PR、檔案內容去識別化、細節寫在 PR body）。判斷標準：拿掉公司名/系統名之後規則仍然成立、且換一個工作環境仍會踩到 → 可攜。沒有就明說「本次無可蒸餾」。這一步做不做，決定 factory 會不會退化成死文件。
記錄到 STM: Retrospective + Lessons Learned

詳細框架見 `skills/work-loop/RETROSPECTIVE.md`（本檔所在 factory repo）

## Step 9: Journal（寫 episodic 紀錄 — 不直寫 LTM）
1. 依 `RETRIEVAL.md` § Session journaling 寫 `{memory_root}/journal/YYYY-MM-DD-{slug}.md`：探索過什麼、排除過什麼、為何這樣決定、使用者的原話
2. **不要直接寫 long-term-memory/** — LTM 蒸餾由 dream pipeline 負責（含去重、cross-ticket pattern 偵測、5-ticket review）。Agent 直寫 LTM 會跟 dream 形成雙寫路徑，造成重複與衝突。
3. Dream pipeline 排程與手動觸發方式見 instance CLAUDE.md

## 失敗處理
任何步驟失敗：
1. 立即記錄到 STM Failures（完整錯誤訊息 + 當時假設）
2. 分析 root cause
3. 風險低 → 修復後繼續；風險高 → 暫停回報使用者

## 暫停規則
依 `.agent-verify.json` 信任區執行：
- **`zone: "autonomous"`（個人倉庫）**：不因常規方案、PR Review 或 merge `main` 暫停；僅在需求自相矛盾、涉及 T3 花費、將私有倉庫改公開／新增開源 `LICENSE`、或自動除錯與自動回滾失敗時暫停。
- **`zone: "guarded"`（公司／團隊倉庫）**：在 `main` 分支合併前（`PAUSED_FOR_MAIN_APPROVAL`）、以人類名義發送外部訊息（Jira/Email/Slack）、DB 破壞性變更或需求不明時暫停；收到 `approve` 後自動完成後續合併、測試與善後閉環。
