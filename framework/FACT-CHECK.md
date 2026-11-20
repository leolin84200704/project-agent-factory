---
title: Fact-check gate (framework)
audience: Any LLM acting as one of Leo's agents, before it answers, sends text, or writes data
purpose: Define what counts as a checked fact, the ten ways facts went wrong, and the receipt that `paf factcheck` and `require-factcheck-receipt.sh` enforce.
---

# 事實查核閘門

> Leo 指令 2026-11-18（VP-19079）：「只要是回答問題，都要做事實查核」，而且要跑「能夠驗證的工具」，
> 用 grep、SELECT 等等「真的能抓到資料」才算合格；改 DB 或發資訊前必須先做。
> 起因是 OptiMantra 的 practice ID 清單。那份清單用串接設定表上的診所名稱判斷 ID 屬於誰，
> 又用「串接開著」推論「這是在用的 ID」，結果 48875 和 56180 都錯，而且已經送出。
> 回頭整理時，同類錯誤在各 instance 的 failures、LTM、incident 裡大約有兩百件（三個 subagent 抽取、重疊後的估計），
> 相關的文字規則超過一百條，幾乎都綁在單一情境上，也幾乎都沒有機制強制。

## 什麼時候要過

1. 任何 DB 寫入：INSERT、UPDATE、DELETE、DDL，staging 和 prod 都算。
2. 任何對外文字：Slack（草稿也算，Leo 會直接按送出）、Email、Teams、Jira comment 和欄位、PR 描述、Confluence 和文件、給廠商或客戶的清單。
3. 給 Leo 的結論或判斷。這一項由 agent 自己守，hook 管不到終端機的回覆。

## 什麼才算查過

每一個事實都寫成三欄：**事實**（statement）、**來源**（source，這個事實出自哪個系統的哪張表或哪個檔案）、**查法**（command）。
每一個事實都要有一張收據：實際跑過的工具和指令（SELECT、grep、kubectl、gh、API 呼叫、parser）、跑的時間、撈到的資料。

不算收據的東西：記憶、STM、handoff、LTM、journal、推理、別人說過的話、沒有實際跑的指令、空的結果、出錯的指令、被 `2>/dev/null` 吞掉錯誤的指令。

## 十類錯誤和各自要加做的檢查

分類跨 ticket、跨資料型別。一個事實可以同時屬於好幾類。

| 類別 | 錯在哪裡 | 代表案例 | 除了收據之外要做的事 |
|---|---|---|---|
| A 不是權威的來源 | 把標籤、衍生欄位、副本、別的系統的表、文件、別人的話當正式紀錄 | 診所名稱標籤（VP-19079）、舊表推 pricing 行為（VP-19145）、同事的舊 comment 說 key 已在 prod（VP-18803）、repo manifest 推 runtime（VP-17544） | 找出這個事實的正式來源（system of record）再查 |
| B 推論當事實 | 推出來的東西寫成查到的 | 「串接開著就是在用」、Cerbo ORC-17 帶 clinic id（VP-19079 11-10，5 張單被 hold）、Cloudflare 100 秒 | 標成推論，寫出從哪些已驗證的事實推出來 |
| C 沒校準的「沒有／壞了」 | 查不到就說不存在 | `rg -r`、zsh `:s`、吞掉 stderr、Prisma `IN` 綁成一個參數、61 分鐘安靜期就回滾 | 同一個工具、同一個範圍，先查一筆已知存在的東西（control） |
| D 身分、歸屬、在用 | 判斷某個 ID、帳號、host、人、訊息屬於誰、還在不在用 | 48875／56180 是停用的個人帳號、JAG 的信當成 BioInsights、CHARM msh06 綁擁有者、host 其實是我們的 | 查主檔，再用獨立的行為（訂單、log、流量、帳號狀態）交叉驗證 |
| E 過時 | 用自己以前的紀錄或舊狀態 | handoff 的 Verified facts、本機落後 origin、10 分鐘前的 git status | 當場重查，不引用紀錄 |
| F 範圍不全或抽樣 | 只看一部分就說全部 | 抽 2 個檔案說 204 個都沒有地址、一個 pod 的 log、一個帳號、BE/FE 的一半 | 對完整的集合核對；用比較寬的條件反查漏網 |
| G 環境搞混 | staging/prod、v1/v2、on-prem/AKS、schema、時區 | staging 的單查 prod DB、.env 其實指向 prod、DB 時區 | 收據裡寫明連到哪台、哪個 schema、哪個 namespace，時間標時區 |
| H 驗證是假的 | 通過的理由不對 | mock 綠燈但方法名錯、測試沒跑、時間條件讓舊資料也符合 | 回答：如果什麼事都沒發生，這個驗證會失敗嗎 |
| I 轉述走樣 | 引用別人的話時改了意思 | 「暫時 done」讀成 Done、把 subagent 結論轉給 Leo | 讀原始訊息，引用原文；subagent 的輸出是假設 |
| J 寫出去沒讀回 | 送出或寫入的結果和以為的不同 | @mention 變純文字、PR comment 寫錯 SHA | 送出或寫入後讀回來 |

## 標示

文字裡的每一個事實都歸成三種之一：
- **已驗證**：有收據。
- **推論**：寫出從哪幾個已驗證的事實推出來。
- **沒有查**：直接說沒查。

推論和沒有查的內容，不能當 DB 寫入的依據，也不能在對外文字裡寫成事實。
STM 和 handoff 的 Verified facts 只放已驗證的內容，推論另列一欄。

## 雙模型驗證與分級（Leo 定版 2026-11-19；三級分級 2026-11-20）

寫的模型不能自己判斷自己的來源是不是 ground truth，也不能自己判斷自己的查法和結果能不能證明這句話。
由另一個家族的模型做驗證，指令一律由腳本執行，驗證模型自己不跑任何東西。驗證有兩種做法。

- **做法 1（審查）**：驗證模型看作者的三欄和執行摘要，逐項回答四件事。
  1. `source_is_ground_truth`：作者的來源是不是正式來源（system of record）。
  2. `command_proves_statement`：這條指令是不是已經把宣稱的條件寫進去，做到宣稱為假時不會通過（不能寫沒帶過濾條件的 `SELECT`）。
  3. `result_matches_statement`：實際執行結果和表述一不一致。非個資模式下直接核對輸出摘要；個資模式下確認指令已編碼條件，因此有資料或空結果本身即能證明一致。
  4. `counterexample_commands`：最多三條唯讀反例指令，宣稱為真時必須沒有輸出，腳本跑了只要撈到資料就推翻。
  前三題全為「是」而且反例都沒撈到資料，才算通過。
- **做法 2（盲測執行後比較）**：驗證模型只拿到「事實」那句話和權威來源表，看不到作者的來源、指令和結果。它自己提出來源和一條帶條件的唯讀指令，由腳本實際執行後，比對兩邊的來源是否一致，以及條件成立與否的真值結果是否一致（不再要求不同欄位順序的輸出雜湊一字不差）。盲測完成後再進做法 1 審查與反例檢查，避免未過濾查詢或雙方共同盲點直接過關。

### 三級分級與模型路由

`paf factcheck` 依 `--tier low|standard|high` 或 `claims.json` 裡的 `tier` 分三級，未指定但帶 `--verify` 時自動判斷（DB 寫入目標或 D／F 類事實自動歸 `high`，其餘歸 `standard`）。模型與 effort 一律由 `framework/scripts/model_router.py` 依反家族自動選取，不再寫死高延遲模型。

| 等級 | 適用情境 | 執行流程 | 驗證模型（`model_router.py`） |
|---|---|---|---|
| `low` | 終端機一般問答、內部低風險查詢 | 本機三欄加收據，不呼叫第二模型 | 無 |
| `standard` | 一般對外文字（Slack、Email、Jira、PR 描述）、一般結論 | 做法 1（審查＋結果一致＋反例） | `standard` 級（`sonnet --effort high` / `gemini-4-hydrogen-high`） |
| `high` | DB 寫入、批次資料清單、D 類歸屬與 F 類全集合陳述、高風險決策 | 做法 2（盲測）再接做法 1（審查＋反例） | DB 寫入走 `critical` 級，其餘走 `complex` 級（`opus --effort high/xhigh` / `gemini-4-argon-high`） |

### 唯讀與隱私
- 驗證模型提出的每一條指令，執行前都要過唯讀檢查：只准登記過的查詢 runner（SQL 只能是一條 SELECT、SHOW、DESCRIBE、EXPLAIN、WITH）、grep、rg、cat、head、tail、wc、ls、jq、find（不含 -exec、-delete）、唯讀的 git、gh、kubectl；不准 pipe、redirect、串接和指令替換。沒過的指令不執行，記在收據裡。
- **PHI**：權威來源表設 `"phi": true` 時（預設），查詢的輸出不會送給驗證模型，只送 exit code、行數和正規化後的雜湊值，結果比對在本機做。指令文字本身會送出去，所以指令裡不放病人姓名這類資料，用 ID 查，並且把宣稱的條件寫進過濾條件裡（例如 `WHERE clinic_id=56180 AND isActive=0`）。

### 權威來源表（每個 instance 一份）
借自 pstack 的 feature map：每一種資料（practice、provider、order……）寫明權威來源是哪張表、怎麼查，以及哪些欄位只是標籤或衍生值。
`paf factcheck` 依序讀 `--sources`、`PAF_FACTCHECK_SOURCES`，都沒有時，找目前所在 git repo 根目錄的 `ground-truth-sources.json` 或 `reference/ground-truth-sources.json`。所以 instance 只要把檔案放在 repo 裡，不用改 settings。來源寫成衍生欄位的事實直接失敗；來源不在表上的會記一筆，交給審查判斷。
驗證模型做盲測時也靠這張表知道該去哪裡查。格式見 `framework/scripts/factcheck.py` 的 docstring。

### 程式碼修改（同樣套用三要素）
- 查法就是測試。`paf tests-fail-on-base --test-cmd "<cmd>"` 會把新增或修改的測試複製到 base commit 的臨時 worktree，並自動掛載本機未追蹤的依賴目錄（`.venv`、`venv`、`node_modules` 或 `--link-dir`），證明在 base 上失敗、在 head 上通過。依改動類型支援 `--change-type auto|bugfix|feature|refactor|docs|config`：純重構要求 base 與 head 兩邊皆通過（行為不變）；純文件或純設定只有在 diff 完全沒有程式碼檔案時才豁免。
- `paf drive-consensus` 與 `paf watch-prs` 的審查 JSON schema（`REVIEW_JSON_SCHEMA`）強制三個必填欄位：`test_source_is_ground_truth`（測試打到真實程式碼或環境邊界，沒有把核心邏輯 mock 掉，環境相關改動具備 staging 或實測證據）、`tests_prove_change`（測試能證明宣稱的效果）、`test_counterexamples`（每個新／改測試的具體反例，任一項 `blocking: true` 或前兩欄任一為 `false` 時，程式機械強制翻成 `request_changes`）。

## 機制

### `paf factcheck <claims.json> [--verify] [--tier low|standard|high]`
claims 檔列出這段文字或這次寫入依賴的每一個事實。腳本自己跑每一條指令，產生收據到 `~/.cache/paf/factcheck/`（`PAF_FACTCHECK_DIR` 可覆寫）。
有 consensus key 時收據會帶 HMAC 簽章（和 PR 共識同一把 key），寫的人沒辦法自己捏造一張雙模型驗證過的收據。
指令在 claims 檔所在的目錄執行。格式和判定規則寫在 `framework/scripts/factcheck.py` 的 docstring：

```json
{
  "target": "slack:C0B8YU8JZG8",
  "claims": [
    {"id": "c1", "text": "56180 is an inactive NPI-type account", "category": "D",
     "source": "lis_core_v7.clinic",
     "command": "python3 q.py \"SELECT clinic_name, clinic_type FROM clinic WHERE clinic_id=56180\"",
     "cross_check_command": "python3 q.py \"SELECT isActive FROM customer WHERE customer_id=12243\""},
    {"id": "c2", "text": "56180 never had an order", "category": "C", "kind": "negative",
     "source": "lis_core_v7.order_info",
     "command": "python3 q.py \"SELECT id FROM order_info WHERE clinic_id=56180 LIMIT 1\"",
     "control_command": "python3 q.py \"SELECT id FROM order_info WHERE clinic_id=20600 LIMIT 1\""},
    {"id": "c3", "text": "Genesis should send 20600", "label": "inferred", "from": ["c1", "c2"]}
  ]
}
```

判定：
- 已驗證的事實：要有 `source` 和 `command`；指令要 exit 0 而且有輸出；可加 `expect_regex`。
- 來源在權威來源表上列為衍生欄位：失敗。
- `kind: negative`（C 類）：主指令必須沒有輸出，`control_command` 必須有輸出。grep、rg 找不到東西時會 exit 1 而且沒有任何輸出，負面陳述接受這種結果；exit 1 但有輸出或錯誤訊息（例如檔案不存在）仍然算失敗。用 `grep -n` 或 `grep -l`，不要用 `grep -c`，因為 `-c` 找不到時會印出 0，算成有輸出。
- D 類：還要 `cross_check_command` 有輸出。
- F 類：還要 `full_set_command` 有輸出。
- 推論：`from` 裡的每一個 id 都要是通過的已驗證事實。
- 沒有查：允許，收據會列出來，提醒文字裡要寫明沒查。

### `framework/hooks/require-factcheck-receipt.sh`（PreToolUse）
對外送出的工具和 DB 寫入，要找到一張通過、120 分鐘內、`target` 字串出現在這次工具呼叫裡的收據。
- `PAF_FACTCHECK_MODE=warn`（預設）：沒有收據時暫停一次，同一個呼叫重跑就放行，記為 override。
- `PAF_FACTCHECK_MODE=block`：沒有收據就擋下。
- `PAF_FACTCHECK_REQUIRE_DUAL=1`：只接受雙模型驗證通過、驗證模型和作者不同家族、HMAC 正確的收據，而且預設對外文字要求至少 `standard` 級、DB 寫入要求 `high` 級（可用 `PAF_FACTCHECK_MIN_TIER_OUTBOUND` 與 `PAF_FACTCHECK_MIN_TIER_DB` 覆寫）。
- 每一次判定都寫進 `~/.cache/paf/factcheck/hook-log.jsonl`，包括這張收據是 dual 還是 single 以及對應的 `tier`。

### 升級條件（照 ENFORCEMENT-LADDER）
warn 模式先跑兩週。之後看 hook-log：pass、paused、override 各幾次，override 裡有幾次事後證明真的沒有需要查的事實。
誤攔少，就把預設改成 block；誤攔多，就先修 hook 的判斷範圍。這個決定和數字一起寫回這一節。

## 和其他規則的關係
- 和 Lauren Tan 的 pstack verification 相同的地方：查真的產物而不是代理指標、判斷的人不是寫的人、`/arena` 用不同廠商的評審、反例要能重現、檢查要先看過它失敗。這份文件多做的三件事：事實陳述也要查（她主要查程式碼）、驗證先盲測（她的 `/interrogate` 會把作者意圖交給審查方）、資料系統裡要明確指出哪個來源才是 ground truth（她的環境裡跑著的 app 就是唯一的來源）。
- AGENTS.md 原則 0（同步世界）和原則 5（記憶不是世界）處理的是 E 類。這份文件把範圍擴大到十類，並且定義收據。
- ENGINEERING-LESSONS 裡各條驗證教訓是這十類的具體案例，新的教訓寫進去時標出它屬於哪一類。
- `validate-outbound-placeholders.sh` 檢查對外文字有沒有 placeholder，這個 hook 檢查對外文字的事實有沒有收據，兩個並存。
