# Proposal: 「由 X 強制」是待驗證的斷言，不是證據

Status: unreviewed inbox item (2026-08-19, factory 自審 — 增補卷寫作 session)
Bar check: **fails cross-domain evidence** — 主證據來自 factory 自身這一個 domain。
standalone / discipline-not-behavior / 去識別化三項通過。放這裡等 Leo 判斷，未開 PR。
另有一個併案可能（見下方「與 2026-08-18 proposal 的關係」），該判斷會直接影響它算幾個 domain。
Provenance: **yes**（同 session 曾用 web search 查證外部論文編號）——但本條的全部證據
來自 repo 檔案 grep，未引用任何外部文字。

## 候選教訓（standalone 版本，無 domain 名詞）

> 文件裡寫著某條規則「由某腳本 / hook / constraint / CI 強制」時，那句話是**斷言**，
> 不是證據。相信它之前先確認三件事：**(a) 被指名的東西存在；(b) 它真的引用了那條規則的
> 關鍵字/欄位；(c) 它真的會被呼叫。** 三項缺任一，該規則實際上停在純文件那一階——
> 而所有讀到那句話的人（與 agent）都會以為它已經被守住。**一個假的機制宣稱，比誠實地
> 承認沒有機制更危險**：後者會讓人保持警覺，前者會讓人停止檢查。

## 為什麼可能是 discipline 而非 behavior

它規定的是相信順序（引用一句「已被 X 強制」之前先驗證 X），不是描述某類系統怎麼運作。
可機械化：機制宣稱若採用固定語法（`enforced-by: {path}`），存在性與內容引用都能被檢查。

## 證據

1. **階梯自己的第 2 階範例沒有實作**（factory，2026-08-19）。
   `framework/ENFORCEMENT-LADDER.md` 的階梯表，第 2 階「儲存層 constraint」那一列，
   「本系統的對應物」欄寫著：**「frontmatter 必填欄位由腳本拒收（`unblock_when:` 缺失
   即報錯）」**。全 repo grep `unblock_when` 的結果只有三個檔案：`framework/RETRIEVAL.md`
   （規則本文）、`framework/ENFORCEMENT-LADDER.md`（該格本身）、
   `framework/templates/stm.md`（模板註解）。以 `--include=*.py --include=*.sh` 搜尋：
   **零命中**。那個「拒收的腳本」不存在，該規則實際停在第 7 階。
   後果不對稱：這一格是階梯用來**教什麼叫第 2 階**的示範，於是它同時教錯了
   「這條規則已經被守住」——連帶讓任何據此判斷「STM 格式有 schema 把關」的後續決策失準。
2. **對照組顯示問題出在哪裡。** 目前唯一一個採用 `enforced-by:` 語法的教訓
   （ENGINEERING-LESSONS §流程紀律，CJK 子規則）指向
   `framework/hooks/validate-repo-language.sh`——該檔案存在，且確實實作該規則。
   所以失效的不是 `enforced-by:` 語法本身，而是**散在文件正文/表格裡、沒有固定形狀的
   機制宣稱**：它們無法被任何檢查掃到，因為它們長得跟一般散文一樣。
   這反過來給出一條修法方向——**機制宣稱必須有語法，否則它不可稽核**。

## 與 2026-08-18 proposal 的關係（檢查點 3，需要 Leo 裁決）

`framework/proposals/2026-08-18-find-the-writer-not-the-reader.md` 說的是：
「要斷言某個 gate/旗標正在控制行為之前，先找到**寫入**它的程式碼」——只有 reader
沒有 writer 的守門恆真。

同根、不同載體：那一條的失效發生在 **runtime 值**（gate 恆真），這一條發生在**文件**
（強制者不存在）。兩種可能：

- **判為同一條教訓的兩個面** → 這份就是那份 proposal 卡住時缺的**第二個 domain**
  （agent 自身框架 vs employer 系統），兩份合併後可直接開 PR。
- **判為兩條** → 理由是修法不同：前者的動作是「去 grep writer」，後者的動作是
  「驗證被指名的路徑存在且引用該 token」，後者可機械化程度高得多。

我沒有自行合併：預設是不推廣，且這個判斷正是 inbox 存在的目的。

## 下推檢查（CONTRIBUTING 檢查點 7）

**可下推到第 6 階，而且這一條特別適合。** 具體實作：一條 CI 檢查，掃 `framework/`
底下所有 `.md`——凡出現 `enforced-by: {path}` 標記，驗證 `{path}` 存在，且該檔案內容
包含被強制規則的關鍵 token（例如宣稱守 `unblock_when:` 的腳本，其原始碼必須出現
`unblock_when`）。配套要求：把散文式的機制宣稱統一改寫成 `enforced-by:` 語法，
否則檢查掃不到它們（證據 2）。

這條教訓的諷刺之處正在這裡：**強制階梯自己的例子，需要一個強制機制來保證它是真的。**

## 若要升格成正典，還缺什麼

若 Leo 判定與 2026-08-18 那份是兩條教訓，則需要第二個**不同** domain 的案例。
形態應該是：一份 runbook / README / ADR / 註解宣稱「這個由 CI 擋」「這個有 DB constraint」
「這個 lint 會抓」，實際查證後發現該檢查不存在、被停用、或掃不到宣稱的範圍——
關鍵特徵是**沒有人查過那句話**，因為它讀起來像已完成的事實。
