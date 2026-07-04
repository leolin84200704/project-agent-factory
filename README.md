# agent-core

Leo 的可攜 agent 框架：個人工作習慣（Layer 1）+ 可重用的工作方法框架（work loop、memory 分層、retrieval 協議）。任何新工作/新專案都從這裡長出一個熟悉該 domain 的 code agent。

## 分層

| 層 | 內容 | 位置 |
|---|---|---|
| Layer 1 個人層 | 語言/溝通/git 紀律/暫停規則 — 跨工作通用 | `AGENTS.md`（symlink 到 `~/.claude/CLAUDE.md` 全域生效） |
| Framework | work loop、retrieval 協議、通用工程教訓、templates、dream pipeline 樣板、hooks、通用 skills | `framework/`、`skills/` |
| Layer 2 工作層 | 各工作/專案的 instance（know-how、memory 資料） | 各自的 repo（例：`lis-code-agent` = Vibrant America instance） |

## 安裝（新機器）

```bash
git clone git@github.com:leolin84200704/agent-core.git ~/agent-core
ln -sf ~/agent-core/AGENTS.md ~/.claude/CLAUDE.md
# 通用 skills 接到 user-level：
for s in ~/agent-core/skills/*/; do ln -sfn "$s" ~/.claude/skills/$(basename "$s"); done
```

## 初始化新的工作 agent

見 `BOOTSTRAP.md` — 給定 workspace + repos + ticket 系統，長出一個新的 Layer 2 instance。

## 目錄

```
AGENTS.md                    ← Layer 1（保持精簡，每 session 載入）
BOOTSTRAP.md                 ← 新 instance 初始化指令書
framework/
├── WORK-LOOP.md             ← 9 步 ticket 流程（泛化版）
├── RETRIEVAL.md             ← memory 分層 + 檢索深度協議
├── ENGINEERING-LESSONS.md   ← 事故蒸餾的通用工程紀律
├── SUBAGENTS.md             ← 子 agent 使用 pattern
├── templates/               ← CLAUDE.md instance / STM / journal / settings 模板
├── scripts/                 ← dream pipeline 樣板（instance 複製後改參數）
└── hooks/                   ← git 安全 hooks（PreToolUse）
skills/                      ← 通用 skills（symlink 進 ~/.claude/skills）
```

## 維護原則

- `AGENTS.md` 控制在 ~80 行內；細節放 framework 按需載入
- Instance 累積的通用教訓 → 蒸餾進 `framework/ENGINEERING-LESSONS.md`；job-specific 留在 instance
- 改 framework 就 commit push（本 repo 允許直接進 main）
