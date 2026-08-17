# Proposal — 呼叫方能自己修的失敗，不可以用 5xx 回

> Unreviewed inbox item (dream 2026-08-16). Single-domain evidence → not a PR.
> 來源：`journal/2026-08-16-native-auto-memory-retirement.md` 明確標記為 factory 候選
> （「the one entry here that reads as universal engineering discipline and is not in the
> factory yet — flagged rather than filed, since the rule is that Leo triages what gets
> promoted」）。dream 依規把它放進 inbox，而不是自行開 PR。

## Lesson (de-identified, standalone form)

回應的 status code 是在回答一個問題：**「這次失敗，該由誰採取下一步？」**
5xx 的意思是「我方壞了，你什麼都不用改，重試即可」。所以只要呼叫方**改掉輸入就會成功**，
用 5xx 回就是在說謊，而且是會讓對方做出錯誤行動的謊。

三個具體的判準：

1. **失敗語意優先於失敗機制。** 決定 code 的是「誰能修」，不是「例外在哪一層被丟出來」。
   一個上游拒絕、一個型別轉換爆掉、一個查無資料，都可能是呼叫方送錯東西——它們在程式碼裡
   長得像 internal error，但契約上是 rejection。最常見的退化路徑是「沒有人明確決定過，於是
   最外層的 catch-all 決定了它」。
2. **「推導出來的空集合」是拒絕，不是伺服器錯誤。** 輸入語法合法、但經過解析／查表／
   計價之後推導出空的或不可能的狀態，這是關於**輸入**的事實，要當 4xx 回，並附上
   機器可讀的 reason／error code。
3. **拒絕要用呼叫方的詞彙。** 把內部 id 回給一個從頭到尾只送過外部代碼的呼叫方，等於沒回。
   對方能拿去查、能引用、能據以決定「要不要重送」的東西，才算錯誤訊息。

反向的自檢很便宜：**「收到這個回應的人，能不能判斷該不該重送？」** 不能，就是 code 選錯了。

## Evidence（同一 employer domain，因此不是 PR）

- 合作夥伴送了一張**合法**訂單，收到 `{"statusCode":500}`。真正的原因是購物籃計價後推導成空——
  一個純粹關於輸入的事實。對方拿不到任何可引用的東西，也無法判斷該不該重送。
  應為 422 + `reason` + `errorCodes`。
- 同一表面：`errorCodes:["861"]` 回給一個只送過外部代碼字串的呼叫方——技術上是 4xx，
  實務上仍然無法行動，因為詞彙不對。**選對 code 只做完一半。**
- 一個字串化的 id 傳到期待數字的上游，冒出來的形態是 500。第一版修補只修了看得見的那個
  call site，於是 bug 存活——這同時是「在邊界一次性正規化輸入」的案例。
- 相關但不同：既有 lesson「行為變更沒有重讀過描述它的文件就不算完成」裡，有一份 API 文件
  把 500 列成**正常且可重試**的結果。那是本條的下游傷害——契約說謊之後，文件把謊制度化，
  外部測試者只能回報「就是個 500」。

## Why it is NOT a PR yet

四個案例全部來自同一個 employer domain 的同一組對外 API。CONTRIBUTING 要求 >=2 個不同 domain。

判準本身（誰能修 → 決定 4xx/5xx）讀起來確實是純粹的介面設計紀律，零 domain 名詞，
也不描述特定系統的行為——**standalone 與 discipline 兩關都過，只卡 cross-domain evidence**。
下一個來自別的 domain 的案例（任何有對外契約的介面：CLI exit code、queue 的 retry vs
dead-letter、SDK 丟的例外型別）就足以補齊。在那之前留在 inbox。

## Target

`framework/ENGINEERING-LESSONS.md` §介面／契約紀律（若採用）。
本 instance 的 LIS 具體版本已在 `long-term-memory/leo-working-rules.md` §Error contracts，
不受此 proposal 是否被採納影響。
