---
name: work-loop
description: 完整的 ticket 處理工作流程（9 步：Retrieve → Analyze → Debate → Discuss → Execute → Review → Complete → Retrospective → Journal）
trigger: "work loop {ticket_id}" or "處理 ticket {ticket_id}" or "執行 {ticket_id}"
---

# Work Loop

> Source of truth 是 factory repo 的 `framework/WORK-LOOP.md` — 收到觸發時**先讀它**
>（本 skill 目錄是 factory `skills/work-loop/` 的 symlink，`realpath` 本檔即得 factory 位置；
> 或依序探測 `~/src/project-agent-factory`、`~/personal_project/project-agent-factory`），
> 依其 9 步執行。本檔只補充 harness 層的執行細節，不重複流程內容。
>
> 歷史註記：本 skill 舊版描述的 `stm_*` / `jira_*` / `task_*` / `stm_distill` 等 MCP 工具
> 屬於已退役的 Python service，勿再使用 — 一切 memory 操作都是直接讀寫 instance repo 的
> markdown 檔。舊版 Step 9「直接蒸餾 LTM」也已廢止：session 只寫 journal，LTM 由 dream
> pipeline 蒸餾（雙寫會造成重複，見 `framework/RETRIEVAL.md` § Write-back）。

## 執行細節（檔案版對照）

- **STM 檔**：`{memory_root}/storage/short_term_memory/{ticket_id}.md`，從
  `framework/templates/stm.md` 建立。「記錄到 STM: {section}」= 直接 Edit 該檔的
  `## {section}` 區段（Ticket Analysis / Approaches Considered / Code Changes /
  Failures / Test Results / User Feedback / Decisions Made / Retrospective /
  Lessons Learned）。
- **檢索**：讀各 tier `_index.md` routing table + Grep STM/LTM 目錄；不預載檔案本體。
- **L4 驗證**：ticket 系統 / GitHub / repo 實況為準，協議見 `framework/RETRIEVAL.md`。
- **子 agent**：Step 2 explore、Step 3 debate pair 的 pattern 見 `framework/SUBAGENTS.md`；
  用 harness 的 Agent 工具（Explore / general-purpose）派發。
- **進度追蹤**：可用 harness 的 TaskCreate/TaskUpdate 追 Step 1~9（可選）。
- **失敗處理**：任何步驟失敗，立即把完整錯誤訊息 + 當時假設寫進 STM `## Failures`，
  分析 root cause；低風險修復後繼續，高風險暫停回報。
- **佔位符**（`{ticket_system}`、`{branch_pattern}`、`{memory_root}`）由 instance 的
  CLAUDE.md 具體化。

## Step 8 Retrospective

讀取完整 STM 檔後，按本目錄 `RETROSPECTIVE.md` 的框架逐項分析，
結果寫回 STM 的 `## Retrospective` 與 `## Lessons Learned` 區段。

## Step 9 Journal（取代舊版 Memory Update）

依 `framework/RETRIEVAL.md` § Session journaling 寫 journal（探索過什麼、排除過什麼、
為何這樣決定、使用者原話）；**不直寫 LTM**。跨 ticket 模式偵測與知識壓縮由 dream
pipeline 的 cross-ticket review 負責，session 中不做。
