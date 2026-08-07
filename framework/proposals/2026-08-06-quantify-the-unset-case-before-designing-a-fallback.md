# Proposal — 設計 fallback 前，先量化「沒設定」實際長什麼樣

> Unreviewed inbox item (2026-08-06). Single-domain evidence so far → not a PR.
> Promote only if a second, different-domain case shows up.

## Lesson (de-identified, standalone form)

要為一個 optional 設定寫 fallback 之前，**先跑一次分佈統計，看真實資料裡「沒設定」有幾種型態。**

直覺會假設只有兩種狀態（有設 / 沒設），實務上至少三種，而且比例常常不是直覺的樣子：
- 完全沒有紀錄
- 有紀錄但值是空的（空字串 / 空陣列 / 佔位值）
- 有紀錄且值明確為 null

「有紀錄就當作有設定」這種寫法，在空值型佔比高的資料上會**大規模判錯**，
而且錯的方向通常是「以為有設定」——也就是**靜默走上錯誤分支**，不會拋錯。

同一條的姊妹規則：**「查不到」和「查詢失敗」必須分開處理。**
把 RPC / 查詢失敗混進「未設定」的分支，等於在上游故障時安靜地套用預設值。

成本對比是這條規則值得寫下來的原因：一個幾分鐘的 group-by 查詢，
換掉一個結構性、影響面等同資料規模的 bug。

## Why it might be universal

- 純**工作紀律**：在寫分支之前先量測輸入分佈，適用任何有 optional 設定的系統。
- 不含領域名詞。把「設定」換成 config、preference、feature flag、表單欄位都成立。
- 「不存在」有多種型態、且要與「取得失敗」區分，是通用的資料建模陷阱。

## Why it is NOT yet a PR

只有一個領域的一個案例。需要第二個**不同領域**的案例才符合 CONTRIBUTING 的 cross-domain bar。

## Evidence (identifying details — inbox only)

VP-17628（lis-backend-emr-v2，2026-08-06）：設計「呼叫者沒指定 provider 時，改用該診所的
default provider」這條 fallback。

在 ClickHouse replica 上掃 **52,615 個 active clinic** 的 `clinic_setting.defaultProvider`：

| 型態 | 比例 |
|---|---|
| 有可用值 | 37% |
| 有 row 但值是空字串 | 37% |
| 完全沒有 row | 26% |

空字串型**和可用值型一樣多**。若照直覺寫「row 存在 → 當作已設定」，約 **19,000 個 clinic 會判錯**，
而且是安靜地拿空字串當 provider id 往下走。
另用 live RPC 探針對單一 clinic 確認過同一形狀（回傳 `value='' active=true`）。

第二點（失敗 vs 未設定不可混為一談）由 debate 的 skeptic 角色提出，同一份設計中一併修掉。
