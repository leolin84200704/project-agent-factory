# scripts — dream pipeline + 量測樣板

這些是 **template copies**，不直接執行。初始化新 instance 時（見 `../../BOOTSTRAP.md`）複製到 instance 的 `scripts/` 後修改 `ADAPT PER INSTANCE` 標記的 config 區塊。

| 檔案 | 角色 | instance 要改什麼 |
|---|---|---|
| `dream.md` | 夜間 consolidation prompt | Agent 名、Phase 0 reconcile 目標、LTM 檔名、never-forget category |
| `run-dream.sh` | dream 執行器（重試、通知、post-dream eval） | 通常不用改（model 用 `DREAM_MODEL` 覆蓋，預設 opus；auto memory 固定關） |
| `memory_scoring.py` | 評分 + auto-link + index 重建 | `CATEGORY_KEYWORDS` |
| `reconcile-jira.py` | ground-truth reconcile（Jira 版範例） | 其他 ticket 系統照它的模式重寫 |
| `eval.py` | memory 狀態快照 / diff | 不用改 |
| `test-retrieval.py` | 檢索品質測試（hit@k / MRR） | `TICKET_PREFIX_RE`、`STOPWORDS` |
| `extract-failures.py` | STM Failures → LTM failure index | `THEMES` |
| `extract-rules.py` | STM feedback → LTM rules constitution | `CATEGORIES` |
| `sweep-urgency.py` | STM urgency 補標 | prefix/tag tuples |
| `agent-metrics.py` | 縱向行為指標（interventions / interrupts / streak / auto-approve / line survival） | `WATCH_PATHS`、`DEFAULT_REPOS` |
| `run-regression.py` | 私有 regression suite + memory-ablation（見 `../REGRESSION.md`） | `eval-tasks/` 題庫 per instance |
| `weekly-routine.sh` | 每週量測（eval + retrieval + metrics diff 報告） | 不用改 |
| `install-launchd.sh` | 排程安裝（label 自動取目錄名） | 同機多 instance 用 `DREAM_HOUR`/`DREAM_MINUTE` 錯開 |

工作機的活體（Vibrant America instance）在 `~/src/lis-code-agent/scripts/` — 那份先演化；框架大改時回填這裡。個人機 instance（iic/jac）2026-08-15 起與此版本同步。
