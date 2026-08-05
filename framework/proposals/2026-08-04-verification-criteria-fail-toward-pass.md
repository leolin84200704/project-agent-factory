# Proposal — 驗證判準的錯誤方向系統性偏向「通過」

> Unreviewed inbox item (dream 2026-08-04). Single-domain evidence → not a PR.

## Lesson (de-identified, standalone form)

自己寫的驗證查詢/判準出錯時，錯誤**不是隨機方向**的：它系統性地偏向「通過」。
理由是判準通常長成「找到符合條件的東西 → 算通過」，而多數寫錯的方式（範圍放太寬、
時區算錯、條件打錯、比較型別不符）都會**放進更多東西**，於是更容易找到。

三條可操作的規則：

1. **最便宜的自檢：「如果現在什麼都沒發生，這個判準會不會通過？」**
   會通過的判準沒有鑑別力，不管它跑出什麼結果。
2. **用單調遞增的 id 當基線，不要用時間窗** —— `id > baseline` 沒有時區語意、
   不需要換算、不會因為算錯而擴大範圍。時間窗是這類錯誤最常見的載體。
3. **管線末端的 `| tail` / `| head` 會吞掉前一段的 exit code** —— 於是失敗的命令
   印出成功訊息。需要判斷成敗的命令不要接管線過濾器，或顯式檢查 `PIPESTATUS`/`pipefail`。

## Evidence（皆 2026-08-04，同一個 domain）

- live 驗證的查詢把 UTC 22:30 誤寫成 15:30 → 撈到一整天的**舊成功紀錄**。
  只因為**另一個**不相干的 bug（布林值 vs raw query 的 `1`）才沒誤報成功。那是運氣不是設計。
- 部署完成判準用了 deployment 的 `readyReplicas`，該欄位在 rolling update 期間**計入舊 pod**
  → 舊版本還在跑時就宣告部署完成。（這條已是既有 lesson 第 12 條，寫完仍踩。）
- `git push` 推的是不含目標 commit 的舊 branch，**卻印出成功訊息**，因為輸出接了 `| tail`。

## Why it is NOT a PR yet

三個案例但同一 employer domain。CONTRIBUTING 要求 >=2 個不同 domain。
第 3 點（pipeline 吞 exit code）是最接近純 shell 紀律、最可能獨立成立的一條。
