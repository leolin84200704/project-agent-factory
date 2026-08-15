# Regression Suite — 量測 agent 是否真的在進步

> 回答「怎麼確定有進步」的私有 eval 層。設計依據（2026-08 SOTA review）：
> Anthropic evals 指南（20–50 題起步、code grader 驗 outcome、pass^k 處理非確定性）、
> Stompy 對照實驗（memory 的收益主要在 token/回合數而非成功率 — 所以每題都記錄
> usage）、memory-ablation 是「dream pipeline 有沒有賺回自己成本」的唯一直接證明。

## 檔案佈局（per instance）

```
{instance}/
├── eval-tasks/           ← 任務庫（私有、來自真實失敗，勿用公開 benchmark）
│   └── {task-id}.md
├── scripts/run-regression.py
└── eval-output/{date}-regression-{label}.json
```

## Task 格式

```markdown
---
id: gcp-project-id
source: journal/2026-07-05-wave-2-thesis-ledger.md   # 出處（ticket/STM/journal）
created: 2026-08-15
type: recall            # recall（記憶檢索）| discipline（紀律規則）| behavior（執行類，v2）
expect: "industy-intel"           # regex，agent 輸出必須匹配（case-insensitive）
expect_all: ["fixture", "replay"] # 可選：全部都要匹配
forbid: "industry-intel(?!-)"     # 可選：不得匹配（抓錯誤答案）
---
（給 agent 的 prompt，一題一問。）
```

## 出題規則

1. **只從真實失敗/教訓出題** — 每題 `source:` 指向一個實際踩過的雷（STM Failures、
   journal、LTM gotcha）。答錯過的問題才值得永久回歸。
2. **答案必須可機械判定** — regex 或（v2）bash grader。不用 LLM-judge。
3. **題目要考「記憶才知道的事」** — 模型自身推不出來的 non-inferable 事實/規則
   （ETH Zurich 2026：context 只有這類內容有用）。好題：「gcloud project id 是什麼」
   （typo 只有記憶知道）。壞題：「什麼是 docker」。
4. 目標 20–50 題；達標後新失敗進題、連續 6 個月全過且來源已失效的題退役（記錄在
   task frontmatter `retired: {date}` 而非刪除）。
5. 題庫是私有資料，**不要**放進任何公開 repo 或 prompt 分享。

## 跑法

```bash
python3 scripts/run-regression.py                     # 全部題目，1 次
python3 scripts/run-regression.py --k 3               # pass^3（非確定性）
python3 scripts/run-regression.py --ablation          # 加跑 memory 清空的對照組
python3 scripts/run-regression.py --label monthly-2026-09 --diff monthly-2026-08
```

- Runner 用 `claude -p --output-format json`，cwd = instance repo（載入 CLAUDE.md +
  indexes），工具只開 Read/Glob/Grep（檢索用，不能改東西），模型用 `REGRESSION_MODEL`
  釘住（預設 opus）、`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`。
- 每題記錄：pass/fail、tokens（input+output）、num_turns、耗時。

## Memory-ablation arm（每月一次）

`--ablation` 會用 `git worktree` 建 instance repo 的乾淨副本，把 `storage/`、
`long-term-memory/`、`journal/`、`archive/` 清成空 stub（CLAUDE.md 保留 — 量測的是
**memory 層的貢獻**，不是角色設定的貢獻），在副本裡重跑同一套題。

判讀：
- `pass_delta` > 0 → 記憶系統在提供正確答案（直接價值）
- pass 持平但 ablated 的 tokens/turns 明顯較高 → 記憶在省成本（Stompy 型收益）
- 兩者皆無差 → dream pipeline 沒賺回成本，該檢討題庫或記憶內容

紀律：ablation 跑之前 instance repo 必須 clean（worktree 才能忠實複製）；跑完
worktree 一律清除。

## Lesson eval gate（CONTRIBUTING 的延伸，v1 手動）

Lesson PR merge 前（GEPA 原則：沒有 fitness function 的教訓庫沒有選擇壓力）：

1. 在 factory checkout lesson branch（`git -C ~/project-agent-factory switch lesson/...`）
2. 對受影響的 instance 跑 `run-regression.py --label gate-{slug}`
3. `git switch main` 再跑 `--label gate-baseline --diff gate-{slug}`
4. pass 下降或 tokens 顯著上升 → lesson 有害，PR 附數據駁回；否則照常人工審

（v2：dream 開 lesson PR 時自動附 gate 結果。）

## 排程

- 每月 1 日（或手動）：`run-regression.py --k 1 --ablation --label monthly-{YYYY-MM}`
- weekly-routine 不跑 regression（有 API 成本），只跑免費的 eval / retrieval / metrics
- 成本估算：每題一次 `claude -p` 呼叫；20 題 × 2 arms ≈ 40 短呼叫。用 opus 約數十萬
  token — 若要省，`REGRESSION_MODEL=sonnet`（但跨月比較要固定同一模型才有意義）
