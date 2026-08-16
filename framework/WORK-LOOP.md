# Work Loop — 通用 ticket 處理流程

> 框架版。佔位符由各 instance 的 CLAUDE.md 具體化：
> `{ticket_system}`（如 Jira via Atlassian MCP）、`{branch_pattern}`（如 feature/leo/{ticket_id}）、
> `{memory_root}`（instance repo 內的 storage/journal/long-term-memory 路徑）。

當收到 ticket 處理請求時，遵循以下流程。

## Step 1: Retrieve（檢索經驗）
0. **Sync first（最高指導原則，AGENTS.md 核心原則 0）**：`git fetch` 本 instance repo 與所有會碰到的工作 repos，確認 ahead/behind；落後先 pull / rebase 再開始
1. 讀取 STM / LTM `_index.md`（含 staleness check，見 `RETRIEVAL.md`）
2. Grep `{memory_root}/storage/short_term_memory/` 找類似的過去 ticket；有相似 ticket 就讀其 STM，特別關注 Failures 區段
3. Grep `{memory_root}/long-term-memory/` 找相關技術知識
4. **Lesson injection（sparse — 只挑 1~2 條）**：用 ticket 的關鍵字 grep `~/src/project-agent-factory/framework/ENGINEERING-LESSONS.md`，挑**最相關的 1~2 條**（不要更多 — 實測 k=1 檢索勝過 k=4，塞多反而害）把原文抄進 STM 的 Ticket Analysis；Step 4 呈報方案時明說遵守了哪條。CLAUDE.md 的 hot-lessons 常駐區塊涵蓋最高風險紀律，此步補的是 task-specific 的其他條目。
5. **L4 驗證**：從 `{ticket_system}` 取得 ticket 現況（status / comments / links）。STM 說的一律以 ticket 系統為準；發現不同步，同一 turn 修正 STM。

## Step 2: Analyze（分析理解）
1. 從 `{ticket_system}` 取得 ticket 內容
2. 用 Agent 工具派 Explore 子 agent 調查相關程式碼和設定
3. 辨識 ticket 類型、影響範圍、相關 service
4. 草擬 1~3 個解決方案
5. 記錄到 STM: Ticket Analysis + Approaches Considered

## Step 3: Debate（正反辯論 — 按風險分級）
先分級再決定要不要辯論：
- **Routine**（完全照既有 pattern 的 config/設定類 ticket）→ **跳過 debate**，直接進 Step 4，但要在方案裡註明「依循 {過去 ticket} 的既有 pattern」。
- **Code change / 新 pattern / incident / 影響 prod 資料** → 派兩個子 agent 同時辯論：
  - 正方：為推薦方案辯護（優點、可行性）
  - 反方：質疑方案（風險、邊界案例、更簡單的替代方案），且**必問可測試性**：
    這個方案的測試會不會全是 mock？seam（注入點）在哪？寫不出行為級測試的部分是哪一塊？
  - 綜合雙方論點調整方案，記錄到 STM: Approaches Considered

## Step 4: Discuss with User（暫停等使用者確認）
呈現分析結果和方案：
```
## Ticket: {ticket_id} - {summary}
### 分析摘要
### 建議方案（含正反論點）
### Test plan（這個改動怎麼被驗證、seam 在哪、哪些部分只能 mock 及原因）
### 需要確認
```
**必須等使用者確認才能進入 Step 5**；**寫不出 Test plan 的方案不能進 Step 5**——
測不動是設計問題，回頭改方案，不是進實作後再發現。

## Step 5: Execute（執行實作）
1. 建立 Git branch: `git checkout -b {branch_pattern}`
2. **先寫行為級測試骨架，再寫實作**（照 Step 4 的 Test plan；骨架 = arrange/assert
   的空殼即可）。骨架寫不出來 = 設計缺 seam，回 Step 4 修方案，不要硬寫——
   順序本身強迫注入點存在
3. 逐步執行修改，每步驗證
4. 失敗時立即記錄到 STM Failures，分析 root cause
5. 記錄到 STM: Code Changes

## Step 6: Review with User（暫停等使用者 Review）
1. 執行 `git diff --stat` 整理變更摘要
2. 執行測試——新 logic 必附**行為級**測試（mock 掉共用路徑的假綠燈不算，見
   ENGINEERING-LESSONS「Sync sibling encodings」條）；無法附測試時必須在 Review
   報告明寫原因——「寫不出測試」本身就是難測 code 的警報，要浮上來給人裁決，
   不得靜默跳過
3. 呈現 Review 報告
4. **必須等使用者確認才能進入 Step 7**
5. 使用者要求修改 → 回到 Step 5

## Step 7: Complete（收尾）
1. Commit: `git commit -m "[{ticket_id}] {描述}"`
2. Push（使用者同意時）
3. **文件比對** — 這次改的行為，去 fetch 描述它的文件並搜尋，確認仍然成立。
   新的 error code / reason / status 值也算。文件寫過舊行為時要明講「先前版本說 X，
   當時為真，現已不是」，不要默默覆蓋。仍然不寫尚未部署的行為。
   沒有相關文件就明講「無對應文件」，不要跳過這步。
4. 呈報最終結果

## Step 8: Retrospective（回顧反思）
讀取完整 STM 紀錄，進行反思：
1. **結果比較**: 初始分析假設 vs 最終結果
2. **失敗分析**: 每個失敗的 root cause、是否可預防
3. **模式識別**: 是否匹配已知 pattern、新發現
4. **教訓**: 技術面、流程面、工具面；每條做**下推檢查**——能否變成確定性機制
   （hook / grader / constraint / regression 題）而非 prose？參
   `framework/ENFORCEMENT-LADDER.md`，不變量日誌格式見 RETROSPECTIVE.md § 失敗分析
5. **信心度**: 1-5 自評（1=完全依賴指導，5=獨立完成）
6. **Factory 蒸餾檢查（必做，不是選項）**: 問「這次學到的東西有沒有跨 instance 可攜的？」有 → 立刻開 lesson PR 到 project-agent-factory（規格見該 repo CONTRIBUTING.md：一條 lesson 一個 PR、檔案內容去識別化、細節寫在 PR body）。判斷標準：拿掉公司名/系統名之後規則仍然成立、且換一個工作環境仍會踩到 → 可攜。沒有就明說「本次無可蒸餾」。這一步做不做，決定 factory 會不會退化成死文件。
記錄到 STM: Retrospective + Lessons Learned

詳細框架見 `~/src/project-agent-factory/skills/work-loop/RETROSPECTIVE.md`

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
以下情況必須暫停等使用者：
- Step 4（方案確認前）
- Step 6（Review 前）
- 需求不清楚
- 遇到不可逆操作
- 無法自行解決的錯誤
