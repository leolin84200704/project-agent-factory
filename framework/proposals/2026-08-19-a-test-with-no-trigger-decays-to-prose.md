# Proposal: 沒有觸發器的測試會退化成文件

Status: unreviewed inbox item (2026-08-19, factory 自審 — 增補卷寫作 session)
Bar check: **fails cross-domain evidence** — 全部證據來自 factory 自身這一個 domain。
standalone / discipline-not-behavior / 去識別化三項通過。放這裡等 Leo 判斷，未開 PR。
Provenance: **yes**（同 session 曾用 web search 查證外部論文編號）——但本條的全部證據
來自 repo 檔案與本機實跑，未引用任何外部文字，判斷時可據此調整審查強度。

## 候選教訓（standalone 版本，無 domain 名詞）

> 把一條規則寫成測試，只完成了一半。測試本身在強制階梯的第 4 階，但**執行測試**這件事
> 若沒有自動觸發器（CI、pre-push、排程），就停在第 7 階——唯一可以被忽略的那一階。
> 沒有觸發器的測試套件，其「全綠」結論的有效期是「上次有人記得跑」，而**漏跑不留痕跡**：
> 你無法從任何紀錄看出它上次執行是什麼時候。判準：對每一個檢查（測試 / lint / 掃描）
> 都要答得出兩個問題——「誰觸發它」與「沒跑的話誰會知道」；第二個答不出來的，該檢查的
> 實際階數就是 7，不管它寫得多嚴謹。

## 為什麼可能是 discipline 而非 behavior

它規定的是驗收順序（宣稱某規則「已被測試守住」之前，先確認觸發器存在），
不是描述某類系統怎麼運作。可機械化成 review 檢查項：每個測試檔都要能指名一個
非人類的呼叫者。

## 證據（同一 domain：factory 自身，這是它卡住的原因）

1. **89 個測試案例，零個自動觸發器**（2026-08-19 盤點）。四個 suite——
   `framework/hooks/tests/validate-git-push.test.sh`（45 案例）、
   `framework/hooks/tests/remind-engineering-lessons.test.sh`（26）、
   `framework/githooks/tests/githooks.test.sh`（13）、
   `framework/scripts/tests/memory-scoring-frontmatter.test.py`（5）。
   repo 根目錄沒有 `.github/`、沒有 Makefile、沒有任何 CI 設定檔；唯一的「觸發器」是
   原始碼註解裡的一行 `# Run: bash framework/hooks/tests/...` 與 `githooks/README.md`
   的一行指令。當日實跑四個 suite 全綠——**但那個結論只涵蓋當日**。
2. **它們守的是變動最頻繁的那一層。** 這些測試的受測物是 hook（階梯第 5 階的欄杆）。
   2026-08-18 到 08-19 兩天之內 `framework/hooks/` 改過兩輪（偽陽性修法抽出
   `lib/command-text.sh`，以及 scope fix 因 PR #47 未帶上而由 PR #48 補 land）。
   守門人改得最勤的時候，正是回歸最可能發生、測試最需要自動跑的時候。
3. **測試是 fail-open 的唯一偵測器，而 fail-open 完全無聲。** hook 的偽陽性會被使用者
   當場抱怨（所以一定會被修）；放行了不該放行的則沒有任何人會發現。實例：08-18 修掉的
   `grep -o 'push[^;&|]*' | head -1` 只取指令中第一個 push-like 片段，於是
   `echo "about to push" && git push --force origin main` 整段穿過 guard，
   `+refspec` 形式的 force push 同樣穿過。把唯一的偵測器交給「有人記得」，等於沒有偵測器。

## 與既有條目的關係（檢查點 3）

- `framework/proposals/2026-08-01-a-job-that-cannot-run-must-alert.md` 與
  ENGINEERING-LESSONS §流程紀律「告警要掛在『最後一次成功產出』上」講的是**排程工作**的
  靜默失效——job 存在但跑不起來 / 跑了但沒產出。
- 本條講的是**從來沒有被排程的驗證資產**：不是 job 跑不起來，是根本沒有 job。
- 兩者可能是同族第三條，也可能該併進上述條目擴充其適用範圍。**留給 Leo 判斷**；
  若判為同族，建議併入時保留「檢查自己也需要觸發器」這個切面，因為它是唯一會讓
  第 4 階靜默退化成第 7 階的路徑。

## 下推檢查（CONTRIBUTING 檢查點 7）

**可下推到第 6 階。** 具體實作：一個 workflow 跑那四個 suite——秒級、零外部依賴、
零 API 成本、純本機。這是階梯上成本最低的一次升階。

附帶約束（來自 ENGINEERING-LESSONS §流程紀律「絕不讓 linter/pre-commit 警告驅動
code 決策」）：CI 的失敗必須是**擋下**而非通知；只發通知的 CI 是第 7 階換了個位置，
同樣依賴有人記得看。

## 若要升格成正典，還缺什麼

第二個**不同** domain 的案例。形態應該是：某個被廣泛相信在保護系統的測試/檢查/掃描，
從未接上任何自動觸發器，或觸發器在某次遷移後失聯而無人察覺——關鍵特徵是
「上次真的執行是什麼時候」這個問題**在該系統裡查不到答案**。
