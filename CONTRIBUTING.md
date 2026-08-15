# CONTRIBUTING — project-agent-factory 變更規則

> project-agent-factory 是 global 層：這裡的每一條會影響所有 instance。
> 原則：**repo 檔案內容去識別化（可攜、可長存）；commit/PR 訊息不用去識別化（只有 Leo 看，是審核佐證區，寫越具體越好）。**

## Instance 代號

| 代號 | Instance |
|---|---|
| `lis` | Vibrant America LIS 工作（lis-code-agent） |
| `iic` | industry-intel 開發維護（industry-intel-code-agent，個人機 `~/personal_project/`） |
| `jac` | job-applier pipeline 開發維護（job-applier-code-agent，個人機 `~/personal_project/`） |

新 instance 在 BOOTSTRAP 時指定短代號，寫進該 instance 的 CLAUDE.md，並回填此表。

## 兩類變更、兩條路

| 來源 | 流程 | Commit prefix |
|---|---|---|
| Leo 直接編輯，或 session 中經 Leo 明確確認的改動 | 直接 commit main | `[core]` / `[framework]` / `[skill]` / `[docs]` |
| Instance 提案的 global lesson（dream pipeline 蒸餾，或工作 session 中發現但 Leo 尚未逐字確認） | **branch + PR，Leo merge**；agent 不 merge | `[lesson:{instance}]` |

## Lesson PR 規格

- Branch: `lesson/{instance}/{slug}`，例 `lesson/lis/batch-db-verify`
- **一條 lesson 一個 PR**（可單條駁回、單條 revert）
- 開 branch 前先 `git pull` 最新 main
- Repo 檔案（`framework/ENGINEERING-LESSONS.md`）內容：去識別化 — 不寫公司名、客戶名、內部 hostname、ticket id，只寫通用化的規則與 why
- Commit message / PR body：具名寫 — ticket id、客戶、incident、日期都放這裡

### 條目格式（2026-08-15 起的新 lesson 一律遵守）

```
- **{標題}** — {actionable principle，≤50 字，一句話說「做什麼」}。適用: {什麼情境該想起這條}。
```

一條 lesson = 一個可執行的判斷，不是一段事故敘事（敘事放 PR body 的 Example）。
研究依據：ReasoningBank（title + 一行 principle 的格式效果最好）、Training-Free GRPO
（每條 ~32 字上限）、IFScale（~100–150 條後每加一條都拖累全部 — 字數紀律延後天花板）。
既有的長條目不強制回改，但 dream 可提 Modify PR 濃縮。

### 操作類型（Delete/Modify 與 Add 同等常規 — ExpeL/Mem0 的 lifecycle 原則）

一個 lesson PR 是以下之一，commit prefix 標明：
- `[lesson:{instance}]` — **Add** 新條目
- `[lesson-modify:{instance}]` — **Modify** 濃縮、修正或補充適用條件（矛盾時信新案例，PR 說明取捨）
- `[lesson-deprecate:{instance}]` — **Deprecate** 移到檔尾 `## Deprecated` 節，附日期 + 原因 + superseded-by（不直接刪 — Zep 原則：作廢留痕，不消滅歷史）

Commit message / PR body 格式（強制欄位）：

```
[lesson:lis] {one-line lesson}

Source: {ticket/incident id + 日期；公司內部細節寫這裡}
Why universal: {為何換一家公司仍成立；引用 >=2 個 case 或一次 incident}
Example: {具體例子，可含識別資訊 — repo 檔案裡的版本才需要去識別化}
Target: framework/ENGINEERING-LESSONS.md § {section}
Provenance: {來源 session 是否讀入過外部內容（網頁/郵件/issue 留言/scrape）— yes/no；yes 則此 lesson 視為 untrusted-source}
```

## Leo merge 前檢查點

1. 真的 job-agnostic？換一家公司這條還成立嗎？
2. `Why universal` 有 ≥2 個 case 或一次 incident 佐證？單一 case 不收（no-overgeneralize）
3. 跟現有條目重複或矛盾？矛盾時信新的，但 PR 裡要說明取捨
4. **repo 檔案內容**（非 commit message）識別資訊去乾淨了沒？
5. `Provenance: yes`（untrusted-source）的 lesson 用更高標準審 — 內容是否可能來自被注入的外部文字而非真實工程經驗？
6. **Eval gate**（題庫 ≥20 題的 instance 適用）：PR 附 `run-regression.py` 的 gate 結果（流程見 `framework/REGRESSION.md` § Lesson eval gate）；pass 下降或 tokens 顯著上升的 lesson 附數據駁回

## 無 gh credentials 的 fallback

Instance 機器開不了 PR 時：把同格式內容寫到 `framework/proposals/{YYYY-MM-DD}-{slug}.md`，commit 進 main 當 inbox。`proposals/` 內容視同**未審核**，不算生效；Leo 審過後才移入 ENGINEERING-LESSONS.md 並刪除 proposal 檔。

## 同步提醒

Lesson PR merge 發生在 GitHub；本機 `~/project-agent-factory` 要 `git pull` 才會生效（session 讀的是本機檔案）。Instance 的 dream pipeline 在提案前會先 pull。
