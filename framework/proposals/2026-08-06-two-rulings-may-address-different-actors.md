# Proposal — 兩個裁決看似衝突時，先確認他們講的是不是同一種 actor

> Unreviewed inbox item (2026-08-06). Single-domain evidence so far → not a PR.
> Promote only if a second, different-domain case shows up.

## Lesson (de-identified, standalone form)

當兩個有決定權的人先後對「同一個 fallback / 預設行為」下了看似互斥的裁決，
**在回報「兩位意見衝突、請裁示」之前，先檢查他們是不是在講同一類主體。**

很常見的情形是：不衝突。第一個人心裡想的是 A 類呼叫者，第二個人想的是 B 類呼叫者，
而**是我把兩者攤平成一條規則**才製造出衝突。解法不是請他們協調，是把規則的 key
從「一條 chain」換成「以主體類別為列的矩陣」——每個人的裁決各自落在自己的那一列，
兩邊同時成立。

推論的順序：
1. 列出所有會走到這個 fallback 的**主體類別**（呼叫者身分 / 憑證種類 / 角色 / 來源管道）。
2. 把每個裁決標註它實際針對哪一列。
3. 只有當**同一列**收到兩個相反的裁決，才是真衝突，才需要請人協調。

附帶效益：矩陣化通常會順手消掉一些原本要寫額外防呆的漏洞——因為「這一列根本不該走這條路」
比「走了以後再檢查」便宜。

## Why it might be universal

- 它描述的是**工作紀律**（回報衝突前的一個檢查步驟），不是某類系統的行為。
- 不含任何領域名詞：把「fallback」換成「預設值」「錯誤處理」「權限決策」都成立。
- 反模式（把多類主體攤平成單一規則）是通用的抽象失誤，不限軟體。

## Why it is NOT yet a PR

只有一個領域的一個案例。需要第二個**不同領域**的案例才符合 CONTRIBUTING 的 cross-domain bar。

## Evidence (identifying details — inbox only)

VP-17628（lis-backend-emr-v2，2026-08-06）：讓 API 下單路徑的 `orderingProviderId` 變成選填。
- 第 1 輪 我的提案：省略 → 查 clinic 的 default provider → 再退回 token 的 customer（沿用 portal 行為）。Leo 批准。
- 第 2 輪 PM 推翻：default 未設就要 **ERROR**，不准退回 token；明給 provider 一律覆寫。
- 第 3 輪 Leo 反對我「一條 chain 適用所有 token」的摘要：customer-scoped token 本來就該以自己下單，
  **根本不該去查 clinic default**。

最終形狀 = 以 token type 為 key 的矩陣，而非一條 chain：

| token type | 行為 |
|---|---|
| customer-scoped | 以自己下單，不查 default |
| clinic-only | 查 default；未設 → error |
| scope-less | 必須自帶 provider |

PM 的規則只適用中間那列。矩陣化同時消掉了 skeptic 提出的 namespace 漏洞
（另一個 ticket 的第二個 resolution point），不需要額外的 code。
