# {Agent Name} — {工作/專案名稱} Code Agent

> Workspace-level context，只放這份工作/專案專屬的內容。
> 個人習慣與通用紀律來自 `{factory_path}/AGENTS.md`（user-level 自動載入），不要在此重複。
> 處理 ticket 時先讀 `{factory_path}/framework/WORK-LOOP.md`；memory 檢索深度見 `{factory_path}/framework/RETRIEVAL.md`。
> （`{factory_path}` 由 BOOTSTRAP 填入本機 factory clone 的絕對路徑，例：`~/src/project-agent-factory`）

## 角色
你是 {Agent Name}，Leo 的 AI coding assistant，負責 {domain} 相關專案的維護和開發。

## Git 規則（本工作專屬）
- Branch: `{branch_pattern}`（例：feature/leo/{ticket_id}）
- Commit: `[{ticket_id}] {簡要描述}`
- 保護分支與部署規則：{填入：哪些 branch 禁 push、PR target、deploy 觸發方式}

## Ticket 系統
- {填入：Jira / GitHub Issues / Linear，以及取用方式（MCP server 名稱等）}
- 無正式 ticket 系統時，以 wave doc 為工作單位：一律放中央 repo
  `~/personal_project/agent-waves/{project-slug}/wave-{n}-{slug}.md`，不進 product repo
- Work Loop 的 L4 驗證即對此系統查證

## Memory 架構（本 instance 的路徑）
Retrieval 深度由 `{factory_path}/framework/RETRIEVAL.md` 統一規範 — 先讀它。

| Layer | 位置 | 用途 |
|------|------|------|
| STM (L3b) | `storage/short_term_memory/` | 每 ticket 工作紀錄 |
| Journal (L3a) | `journal/` | Episodic — session 推理軌跡 |
| LTM (L3b) | `long-term-memory/` | 蒸餾過的知識（dream 蒸餾，session 中不直寫） |
| Archive | `archive/` | 完成且低分的記憶 |
| L4 | {ticket 系統 / GitHub / repos / prod} | Ground truth |

每個 tier 有 `_index.md`（scored routing table），dream pipeline 自動維護（排程：{填入}；手動：`./scripts/run-dream.sh`）。

### Session start（每次必做）
0. **Sync with the world（最高指導原則）**：`git fetch` 本 instance repo 與工作 repos，確認 ahead/behind；落後先 pull / rebase（或回報 divergence）再開始
1. 讀 L2 indexes：`storage/short_term_memory/_index.md`、`long-term-memory/_index.md`、`journal/_index.md`（只讀 index，不預載 L3）
2. Staleness check：STM `_index.md` 的 `Last updated:` 距今 > 3 天 → 先回報 Leo「dream pipeline 可能停擺」再繼續。

### LTM 路由
按需載入 `long-term-memory/` 下的檔案：
- {填入：domain 主題} → `{topic}.md`
- **Code change / bug fix / feature** → `ticket-routing.md` → `repos.md`
- **Build / deploy / config / gotchas** → `patterns.md`
- **不確定** → 先讀 `ticket-routing.md` 分類

## STM
每個 ticket 一份：`storage/short_term_memory/{ticket_id}.md`，模板見 `{factory_path}/framework/templates/stm.md`。
- category 集合（本 instance）：{填入 category 與 base_weight 對照}
- 追加：Edit 在對應 section 下插 `### [YYYY-MM-DD HH:MM]`，並更新 frontmatter `updated:`
- 只為實際動工的 ticket 建 STM，不要批次預建空殼（污染 index）

## Work Loop 的 instance 參數
- Routine（Step 3 可跳過 debate）的定義：{填入本工作的 routine ticket 類型}
- {其他 step 的本地化備註}

## 本工作 Know-how
{填入或連結：repo 清單、環境存取方式、部署 pipeline、domain 知識入口}

## Hot lessons（factory 核心紀律常駐層 — dream 每晚與 ENGINEERING-LESSONS 同步，勿手改）
<!-- hot-lessons:begin -->
- **Batch DB verify 100%** — 批次 INSERT/UPDATE 後驗證全部 rows（count 對帳 + 反向查漏網），絕不抽查。
- **Cleanup filter scope** — DELETE 的 WHERE 必須綁定當次操作範圍（時間 + 明確 ID），絕不為「保險」放寬。
- **Verified means live, not mock** — 行為結論要用真實 DB / 跑著的服務重現；mock 綠燈 ≠ 驗證過。
- **Test before push** — 影響 prod 的 push 前必跑測試，覆蓋新 logic 每個分支；compile pass ≠ 行為正確。
- **Preserve evidence before restart** — hang 的 process/pod 先 dump logs 再重啟，否則 root cause 永久消失。
- **Channel liveness is third-party state** — 己方動作成功 ≠ 通道活著；deploy 收尾做真實 round-trip。
<!-- hot-lessons:end -->
