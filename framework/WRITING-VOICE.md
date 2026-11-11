# Writing Voice：agent 的文字和 Leo 的文字差在哪裡

> 這份文件記錄 2026-11-11 做的一次比較，以及由它得出的寫作規則。
> 規則本身常駐在 `AGENTS.md` 的「文字要讓人讀一遍就懂」一節。這裡放的是依據、數字和例句。
> 替 Leo 起草文字、或 Leo 說某段文字「不像人話」時，讀這份文件。

## 1. 起因

Leo 在 2026-11-11 貼了一篇論文的整理，論文是《Why is AI-generated language so hard to understand?》。
Leo 要求所有輸出都要改掉論文講的問題，範圍包含給 Leo 的回覆、Slack、email 和 Jira ticket。

論文的重點有四個。

1. 人讀句子的時候會一邊猜下一個詞。猜對就讀得快，猜錯就要停下來重讀。
2. 人讀句子的時候要先記住前半句，才能看懂後半句。要記的東西太多，就會忘記然後重讀。
3. 模型寫的句子有兩個毛病。第一個是讓沒有生命的東西做人的動作，讀者猜不到這種搭配。第二個是把主詞和動詞隔得很遠，讀者記不住。
4. 這兩個毛病是在後訓練階段養成的。評分只看答案對不對，不看讀者好不好讀，所以模型學會用最少的字塞最多的資訊。

論文裡的例句如下。

| 模型寫的 | 問題 | 好讀的寫法 |
|---|---|---|
| The whole conversation is eating its own tail | 對話不會吃東西 | The conversation keeps repeating the same question |
| Sometimes chaos expects you to remember it independently | 混亂不會期待 | Sometimes you have to remember messy things on your own |
| The guitar sitting in the corner because you are still deciding teaches you zero chords | 主詞 guitar 和動詞 teaches 中間隔了 8 個詞 | The guitar sits in the corner while you decide. It has taught you no chords. |

## 2. 比較的方法

Leo 要求照論文的做法，比較 Leo 自己的話和 agent 的話。

- Leo 的話有三個來源。第一個是 Telegram 上 Leo 發給 agent 的訊息，去掉重複和超過 160 字的轉貼之後有 115 則，共 1,708 個中文字。第二個是 Leo 在 Slack 發的訊息。第三個是 Outlook 的寄件備份。
- agent 的話是 Telegram 上 agent 回 Leo 的訊息，共 295 則，44,389 個中文字。
- Telegram 的紀錄在 `~/.local/share/vibrant-america-auto/state.db` 的 `hitl_log` 表，`direction` 欄位是 `leo` 或 `agent`。
- 計算方式是數每個詞出現幾次，再換算成每一千個中文字出現幾次。

這個比較有三個限制。

1. Leo 在 Telegram 上多半是下指令，agent 多半是寫報告。兩邊的文體本來就不同，所以數字只能看方向，不能當成精確的差距。
2. Leo 的樣本只有 1,708 個字，出現次數少的詞不可靠。
3. Slack 和 Outlook 裡有一部分訊息是 agent 起草、Leo 送出的。長的、條列的訊息多半是這一種。判斷 Leo 的習慣時只看短的、口語的訊息。

## 3. 結果

### 3.1 句子長度差不多，訊息長度差很多

| | Leo | agent |
|---|---|---|
| 每句平均中文字數 | 12.6 | 14.0 |
| 每則訊息平均字元數 | 28 | 825 |

句子長度接近，所以 agent 難讀的原因不在句子長。原因在下面兩項。

### 3.2 agent 用符號塞資訊

每一千個中文字出現的次數如下。

| 符號 | Leo | agent |
|---|---|---|
| 冒號 `：` | 1.2 | 29.1 |
| 左括號 `（` | 4.1 | 18.4 |
| 引號 `「` | 0 | 4.5 |
| 等號 `=` | 0 | 4.0 |
| 箭頭 `→` | 0 | 0.6 |

agent 把補充的事實塞進括號，用「標籤：內容」開頭，用等號和箭頭代替句子。這就是論文說的省字。

### 3.3 Leo 會說但 agent 不說的詞

每一千個中文字出現的次數如下。

| 詞 | Leo | agent |
|---|---|---|
| 直接 | 11.7 | 2.6 |
| 嗎 | 8.8 | 0.3 |
| 這個 | 8.2 | 2.5 |
| 然後 | 6.4 | 0.1 |
| 應該 | 5.9 | 0.3 |
| 怎麼 | 5.9 | 0.5 |
| 請你 | 5.3 | 0.5 |
| 需要 | 5.3 | 1.6 |
| 如果 | 5.3 | 1.3 |
| 這件事 | 2.9 | 0.2 |
| 為什麼 | 2.3 | 0.2 |
| 不是嗎 | 1.8 | 0 |
| 吧 | 1.2 | 0 |

Leo 的說話習慣有四個。

1. Leo 用問句推進事情。例句是「outgoing 裡面有 archive 不是嗎」和「這件事情 Olena 應該已經回覆了？如果是的話就先不用寄信」。
2. Leo 用「然後」「另外」把事情一件一件接下去。例句是「直接 merge 到 staging 然後測試，沒問題的話開 main pr, 然後給我 pr」。
3. Leo 請人幫忙的固定說法是「再麻煩…，謝謝」。例句是「那再麻煩你幫我 create 一個 ticket」和「還有這兩個也麻煩了，謝謝」。
4. Leo 在 Slack 一則只講一件事，句尾常常沒有句號。例句是連續兩則的「他們的 sftp folder 壞了」和「要找他們修」。

### 3.4 agent 會寫但 Leo 不寫的

每一千個中文字出現的次數如下。

| 詞 | Leo | agent |
|---|---|---|
| 目前 | 0.6 | 1.5 |
| 仍 | 0 | 0.5 |
| 尚未 | 0 | 0.3 |
| 沒有 | 5.3 | 9.4 |
| 不會 | 0.6 | 1.7 |

agent 繞口的寫法有四種，例句都取自 agent 實際發出的訊息。

| agent 寫的 | 問題 | Leo 會怎麼說 |
|---|---|---|
| 這是要 AP 或當初買授權的人回答，不是問你。 | 用「A，不是 B」的對比句 | 這封不用回 |
| 我這邊能寫回 Jira 的欄位只有 comment、轉狀態、story points 和 sprint，沒有 due date。 | 先列能做的，最後才講不能做的 | due date 我改不了，要麻煩你手動改 |
| watch 還在跑，每 30 分鐘探一次 port 22。 | 用自己取的代號，主詞做了它不會做的動作 | 我每 30 分鐘會再測一次連線 |
| 可能是後面沒有新的待推結果，也可能是已經不再重試；要等恢復後查 DB 才能確定。 | 一句裡放三件事，用分號接 | 拆成三句，先講還不確定，再講兩種可能 |

## 4. 稱謂

Leo 在同一天指出 agent 直接寫人的全名，沒有照 Leo 自己的叫法。

原因是「Tianhao Wang 一律稱昊哥」這條規則在 2026-11-06 就記下來了，但只放在 lis instance 的長期記憶裡。長期記憶要用到才會載入，所以 agent 常常沒讀到，就照 Slack 和 Jira 顯示的全名寫。

稱謂跟著工作環境走，所以規則放在各 workspace 自己的 `CLAUDE.md`。lis instance 的規則在 vibrant-america-working-agent 的 PR #72。

從這件事得出的通用做法有兩條。

1. 稱謂規則要放在每次都會載入的檔案裡，不能只放在按需載入的記憶裡。
2. 遇到沒有規則的人，先查 Leo 以前在 Slack 和 email 怎麼叫他。查不到就問 Leo。

## 5. 規則放在哪裡

| 規則 | 位置 |
|---|---|
| 文字要讓人讀一遍就懂 | `AGENTS.md`，「溝通偏好」底下 |
| 對外文字用 Leo 的語氣 | `AGENTS.md`，「替 Leo 起草對外文字」 |
| 給同事的訊息 | `AGENTS.md`，「替 Leo 起草給同事的訊息」 |
| 稱謂 | 各 workspace 的 `CLAUDE.md` |

## 6. 還沒做的事

- lis instance 的 daemon 有一個檢查對外訊息的 critic，它不載入 `AGENTS.md`，所以不會用這些規則擋草稿。Leo 在 2026-11-11 決定先不加。
- 英文信要怎麼稱呼昊哥，沒有 Leo 親手寫的例子。2026-10-29 那封的「Hi Wang,」是 agent 起草的。
- 「JJ」這個叫法的依據比較少，Leo 在 Telegram 說過兩次，Slack 只有一則。
- 這次比較沒有量英文。Leo 的英文信很短，開頭是「Hi {名字},」，結尾是「Thanks, Leo」。
