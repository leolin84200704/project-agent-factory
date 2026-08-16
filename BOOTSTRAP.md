# BOOTSTRAP — 初始化一個新的工作/專案 Agent

> 對 Claude 說「依 `~/project-agent-factory/BOOTSTRAP.md` 初始化 {名稱}，workspace 在 {路徑}，repos 是 {...}」即可執行。
> 前提：`~/project-agent-factory` 已 clone、`~/.claude/CLAUDE.md → ~/project-agent-factory/AGENTS.md` symlink 已建立（見 README）。

## 產出

一個 instance repo（例：`{workspace}/my-job-agent/`），結構：

```
{instance}/
├── CLAUDE.md                  ← 從 framework/templates/CLAUDE.instance.md 填入
├── storage/short_term_memory/_index.md
├── journal/_index.md
├── long-term-memory/
│   ├── _index.md
│   ├── ticket-routing.md      ← 初版由 repo 掃描產生
│   ├── repos.md               ← 初版由 repo 掃描產生
│   └── patterns.md            ← 初版由 repo 掃描產生
├── archive/_index.md
├── scripts/                   ← 從 framework/scripts/ 複製後改參數
└── .claude/
    ├── settings.json          ← 從 framework/templates/settings.json 複製
    ├── hooks/                 ← 從 framework/hooks/ 複製
    └── skills/                ← 這份工作專屬的 skills（之後累積）
```

## 步驟

### 1. 收集參數（缺任何一項先問 Leo）
- Agent 名稱、workspace 路徑
- Repo 清單（路徑或 GitHub URL）
- Ticket 系統與取用方式（Jira MCP？GitHub Issues？無？）
- Branch/commit 慣例、保護分支、deploy 觸發方式

### 2. 建骨架
- 依上方結構 mkdir + 複製 templates/scripts/hooks
- `CLAUDE.md`：填 `framework/templates/CLAUDE.instance.md` 的所有 `{佔位符}`；沒有答案的欄位保留 TODO 並列給 Leo
- 各 `_index.md` 初始化為空表 + `Last updated: {today}`

### 3. 餵 repos（長出第一版 LTM）
對每個 repo 派一個 Explore 子 agent（可平行）：
- 掃 README、package.json/build 設定、目錄結構、CI/deploy 設定、env var 用法
- 產出：這個 repo 是什麼、tech stack、怎麼 build/test/run、部署方式、明顯的 gotchas
- **欄杆盤點（enforcement ladder 第 5–6 階）**：這個 repo 現有哪些確定性欄杆
  （strict type config、lint 規則、import boundary、coverage gate、CI 上的
  test/migration 檢查），缺哪些。缺口寫成 `long-term-memory/guardrail-gaps.md`
  ——**只盤點與提案，不擅自加**：工作 repo 是團隊資產，加 lint/CI 規則影響所有人，
  由 Leo 決定何時以正式 ticket/PR 提給團隊。此檔的意義：agent 寫 code 時知道
  「哪些紀律這個 repo 不會自動幫你把關」，對應的驗證要自己補（見 WORK-LOOP Step 5–6）
彙整寫入：
- `long-term-memory/repos.md` — 每 repo 一節
- `long-term-memory/patterns.md` — build/deploy/config 慣例
- `long-term-memory/ticket-routing.md` — 「哪類需求改哪個 repo」的初版路由表（之後由 dream 持續修正）
- `long-term-memory/guardrail-gaps.md` — 欄杆盤點與提案（上）

### 4. 接 memory pipeline
- `scripts/` 內路徑與 ticket 系統參數改成本 instance 的（`ADAPT PER INSTANCE` 標記的 config 區塊）
- 排程：`bash scripts/install-launchd.sh` 直接裝（label 自動取 instance 目錄名；同機多 instance 用 `DREAM_HOUR`/`DREAM_MINUTE` 錯開）。沒有排程也可，但 CLAUDE.md 要註明「手動跑 run-dream.sh」
- Staleness check 從第一天就生效
- Native auto memory 關閉（template settings.json 已含 `autoMemoryEnabled: false`；理由見 `framework/RETRIEVAL.md` § Native harness auto memory）

### 5. Workspace 接線
- 若 Leo 從 workspace 根目錄（非 instance repo 內）啟動 session：workspace 根放一份 `CLAUDE.md`，內容一行 `@{instance}/CLAUDE.md`
- 這份工作專屬的 skills 放 `{instance}/.claude/skills/`，並 symlink 到 `{workspace}/.claude/skills/`（讓從 workspace 啟動的 session 也能發現）
- Workspace 及各工作 repo 放 `.claude/settings.local.json`：`autoMemoryEnabled: false` + **hooks 用絕對路徑指回 factory `framework/hooks/`**（git 安全 ×2 + engineering-lessons 提醒 + verification-asset 保護；單一源頭、不複製檔案進產品 repo）。工作 repo 是 git 的話把 `.claude/` 加進 `.git/info/exclude`（本地 ignore，不產生 commit — 對 push-to-deploy 的 repo 尤其重要）

### 6. 驗收（新開一個 session 測）
- 問「branch 規則是什麼」→ 應同時反映個人層 + instance 層
- 問一個 repo 相關問題 → 應按需讀 `repos.md` 而不是預載全部
- 確認 hooks 生效：試一個 `git push --force`（對 throwaway repo）應被擋

## 原則
- Instance 只放這份工作專屬的內容；任何「其實是通用習慣」的東西上移到 `~/project-agent-factory`（AGENTS.md 或 framework/）
- 反向也成立：instance 累積出的通用教訓，由 dream 或 Leo 確認後蒸餾進 `framework/ENGINEERING-LESSONS.md`
