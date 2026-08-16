# Enforcement Ladder — 規則的強制機制階梯

> 一條規則的價值取決於它被強制的方式，不是它被寫下的地方。寫成 prose 的規則
> （CLAUDE.md、ENGINEERING-LESSONS.md）是**第 7 階：唯一可以被忽略的一階**——
> 本系統已實測（canary 2026-08-15：prose 教訓 0/3 被遵守；改成 hook + 常駐 +
> 注入後 3/3）。因此：每條規則入庫時都要問「能下推到第幾階」，能推多深推多深。
> 每一條停在第 7 階的技術規則，都是一次「本來可以編碼但沒有編碼」的自白。

## 階梯（由強到弱）

| 階 | 機制 | 產品 code 的例子 | 本系統的對應物 |
|---|---|---|---|
| 1 | 表達上不可能 | branded type、discriminated union、private constructor | 檔案格式讓錯誤寫法無法表達（如 `expect_all` 一律 block list） |
| 2 | 儲存層 constraint | UNIQUE、CHECK、FK、NOT NULL | frontmatter 必填欄位由腳本拒收（`unblock_when:` 缺失即報錯） |
| 3 | 啟動/開工時失敗 | boot 時驗 config，缺 env 不啟動 | staleness check、`run-regression.py --check`（grader 入庫前自驗） |
| 4 | 測試失敗 | property-based test、invariant test | regression suite、canary 題、ablation arm |
| 5 | 攔截層 | lint rule、import boundary | PreToolUse hooks（git 安全、lessons 提醒、驗證資產保護） |
| 6 | CI / gate 失敗 | migration linter、breaking-change diff | lesson eval gate（REGRESSION.md § Lesson eval gate） |
| 7 | 寫成文件 | ADR、wiki | ENGINEERING-LESSONS.md、hot-lessons、CLAUDE.md |

判準：**下層的機制不依賴任何人（或任何模型）「記得」**。第 7 階的遵守率隨
session 長度衰減（McMillan 2026）、隨條目數增加而全體變差（IFScale）；
第 1–6 階不會。

## 使用點（誰在什麼時候問這個問題）

1. **Retrospective（work loop Step 8）**：每個失敗填不變量日誌三格——
   被違反的不變量 / 最低可強制階 / 實作（見 `skills/work-loop/RETROSPECTIVE.md`）。
2. **Lesson PR 審核（CONTRIBUTING.md 檢查點）**：能下推的教訓，PR 應附帶機制
   或開 follow-up；prose 條目加 `enforced-by:` 標記指向機制。
3. **Dream hot-lessons sync**：已有 `enforced-by:` 的教訓是常駐區塊的降級候選——
   ≤8 條的常駐預算應留給還沒有機制能接住的規則。
4. **BOOTSTRAP 欄杆盤點**：初始化新 instance 時，對每個工作 repo 盤點現有的
   第 5–6 階欄杆（lint / strict types / coverage gate / CI 檢查）與缺口，寫成
   `guardrail-gaps.md` 提案給 Leo——agent 不擅自往團隊 repo 加規則，但要知道
   哪些紀律沒有機制把關、需要自己在 work loop 補驗證。

## 邊界（什麼東西只能留在第 7 階）

業務脈絡、決策理由（為何選 A 不選 B）、遷移進行到哪個階段、需要人類裁量的
判斷準則（「定性權在 owner」這類）。這些無法編碼，prose 是正確的家。
反之，任何能寫成 regex、path 檢查、schema 驗證、round-trip 測試的規則，
留在第 7 階都是技術債。

## 為什麼驗證資產本身要受保護（reward signal 完整性）

測試、grader、eval 題、hook 是 agent 的 reward signal。同一個 agent 寫的
code 和測試互相驗證，證據力是弱的；agent 修改「用來評判自己的東西」時
尤其如此——改一個 expect regex 就能讓 fail 變 pass，而且沒有任何機制會注意到。
政策：驗證資產**新增自由、修改既有斷言需人批准**
（由 `framework/hooks/protect-verification-assets.sh` 在使用點攔截）。

來源：2026-08-15 外部架構討論的蒸餾（enforcement ladder、invariant log、
protected verification assets），與本系統同日的 canary 實證互相印證。
