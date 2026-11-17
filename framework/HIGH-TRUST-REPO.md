# High-Trust Environment Repo — 結構準則、新建骨架與瘦身改造流程

> 適用時機在建立全新產品倉庫，或受指派診斷並改造既有倉庫使其符合 High-Trust Environment。
> 本檔收錄跨專案通用的架構準則與執行步驟。通用工程事故紀律見 `ENGINEERING-LESSONS.md`，單一端點延遲優化見 `OPTIMIZATION.md`，強制機制階層見 `ENFORCEMENT-LADDER.md`。

## 信任區與所有權前提

動手前先依 `.agent-verify.json` 的 `zone` 確認倉庫所有權邊界。

- **個人倉庫 `zone: "autonomous"`**。建立全新個人倉庫時直接依下方流程 A 建置骨架；使用者指派瘦身或升級既有個人倉庫時，在不影響線上使用者體驗的前提下依流程 B 執行改造、補齊確定性閘門並走完雙異質模型共識。所有變更包含 `.agent-verify.json`、`.github/workflows/ci.yml`、hooks 與 skills 等治理與驗證規則檔案，一律直接交由另一異質模型在固定長對話 session 來回審查確認，在最新 SHA 取得 `approve` 共識與遠端 CI 全綠後由 `paf ship --consensus` 或 `paf merge-consensus` 自動合併至 `main`、關閉雙方 session，並在有設定 `deploy` 時自動部署且於部署後再跑一次測試，不暫停詢問使用者。
- **公司或團隊倉庫 `zone: "guarded"`**。代理程式不可擅自把未經團隊同意的目錄慣例、靜態檢查腳本、CI workflow 或分支保護設定寫進共用倉庫。面對團隊倉庫時只對照下方六項結構準則做唯讀盤點，將缺口與具體提案寫入 instance 的 `long-term-memory/guardrail-gaps.md`，由使用者決定何時以正式 ticket 或 PR 向團隊提出；獲團隊採用後的變更合併至非預演分支前，一律停在 `PAUSED_FOR_MAIN_APPROVAL` 等待使用者核准並服從人類同事審查。

---

## 一、六項 High-Trust Repo 結構準則

一個倉庫要讓代理程式在不靠人工逐行檢查的情況下穩定交付並安全合併，必須同時滿足以下六項結構條件。

### 1. 同功能檔案集中於單一目錄與一對一命名

同一個功能的入口、路由控制器、領域邏輯、資料結構定義與單元測試集中在同一個目錄下。檔案名稱與類別或模組名稱維持一對一對應，讓代理程式不必跨整個倉庫搜尋就能定位影響範圍。可部署服務之間禁止直接匯入彼此的原始碼目錄，跨服務共用程式碼只能放在單一且邊界明確的共用套件中。

### 2. 名稱如實反映底層技術的極薄共用基礎層與標準範本

代理程式在新增功能時會直接複製倉庫裡最短的既有寫法，所以架構設計必須讓最省事的寫法剛好就是正確的寫法。

- **名稱如實對應底層技術**。共用基礎層只放極薄且名稱直接對應底層實作的基礎元件，例如關聯式資料庫連線池、帶過期時間的鍵值與清單儲存、暫時性錯誤重試與外部通道客戶端。禁止把關聯式資料庫資料表包裝成快取引擎或欄式分析資料庫的類別名稱，也禁止保留單純轉發單一 HTTP 呼叫卻無業務邏輯的中介代理服務。
- **提供標準範本**。多服務或多模組倉庫要保留一個結構極簡、通過所有靜態檢查與啟動測試的標準範例目錄，讓後續新增模組時有明確範本可循。
- **連線池與結構初始化安全**。每個資料庫連線池建立時都必須註冊閒置連線 `error` 事件監聽器，避免背景斷線直接終止行程，並依資料庫規格設定明確的 `max` 連線數上限。啟動階段若包含冪等 DDL 初始化，必須以資料庫 advisory lock 序列化執行，防止多複本同時冷啟動時發生型錄競爭錯誤。

### 3. 機器雙向驗證的 `FEATURE_MAP.md`

倉庫根目錄必須維護一份 `FEATURE_MAP.md`，清楚列出所有運作中的服務與共用套件入口、HTTP 路由或排程觸發點、代理程式工具定義，以及正式資料庫資料表清單，讓模糊的使用者回報能直接對應到精確檔案路徑。

只靠人工維護的文件很快就會與程式碼脫節，因此必須搭配確定性的架構檢查腳本如 `scripts/check-architecture.*`，在建置與 CI 階段執行雙向一對一比對。程式碼裝飾器出現的新路由或 SQL DDL 新增的資料表若未寫入 `FEATURE_MAP.md` 就直接報錯；`FEATURE_MAP.md` 或路由清單殘留已刪除的路徑與舊表名也同樣報錯。檢查腳本必須設定明確的最低數量下限，例如至少匹配到的原始碼檔案數、路由數與資料表數，防止因為萬用字元路徑寫錯導致匹配結果為空集合而靜默放行。寫完檢查腳本後必須實際注入一次錯表名或假路由的反例，確認腳本會回傳非零退出碼後再還原。

### 4. 分層硬性靜態與邊界欄杆

純文字規範屬於 `ENFORCEMENT-LADDER.md` 的第 7 階，遵守率會隨對話變長與規則變多而下降。所有能用程式判斷的架構約束都必須下推到第 1 至 6 階的編譯器、靜態分析與 CI 閘門。

- **驗證契約固定鍵名與遠端 CI 對齊**。倉庫根目錄配置 `.agent-verify.json` 並啟用 `dune_lint`，搭配 `.github/workflows/ci.yml` 在遠端直接執行與 `.agent-verify.json` `commands` 完全對齊的架構檢查、型別檢查、單元測試與啟動測試指令；僅倉庫本身內含 `bin/paf` 時才在遠端呼叫 `python3 bin/paf verify --repo . --mode ci`。`paf verify` 讀取 `commands` 底下的固定鍵名 `lint`、`typecheck`、`unit_test`、`boot_smoke`、`replay`、`post_merge_check`、`deploy` 與 `post_deploy_check`，自創未知鍵名會被略過，因此自訂架構檢查腳本 `scripts/check-architecture.*` 必須放進 `commands.lint`。`dune_lint` 負責攔截未處理的浮動 Promise、環境變數寫死預設網址、缺少注入裝飾器的選填建構子參數、程式碼混入中日韓字元與寫死憑證。
- **精確的字元豁免範圍**。若極少數檔案因網域規則必須包含中日韓字元，`dune_lint.cjk_allow_globs` 只能精確列舉該檔案路徑，禁止對整個目錄使用萬用字元豁免。
- **對稱掃描與禁令清單**。自訂架構檢查必須同時掃描 `src/` 與 `test/` 目錄，禁止跨服務直接匯入、禁止殘留已退役的第三方套件、禁止測試指令帶 `--passWithNoTests` 這類無測試時照常通過的旗標，並禁止在程式碼中使用未釘住版本的浮動模型別名如 `*-latest`。

### 5. 閉環執行與完整生命週期啟動測試

代理程式必須能在本機與沙盒獨立跑完建置、型別檢查、行為級單元測試與服務啟動驗證，在推送前自己形成閉環。

- **行為級邊界測試**。共用基礎元件與核心商業邏輯必須具備真實行為測試，涵蓋過期時間在字串與清單等所有目標結構上的過濾效果、`LIKE` 萬用字元如 `%` 與 `_` 的跳脫處理、零值與負數索引邊界，以及暫時性網路或 5xx 錯誤重試與 4xx 錯誤立即失敗的區別。禁止用全 mock 自證的測試充數。
- **完整初始化啟動測試**。每個可部署服務都必須具備放入 `commands.boot_smoke` 或 `commands.unit_test` 的啟動冒煙測試，在測試專用的隔離環境變數下建立依賴注入容器，並實際執行完整初始化生命週期如 `await moduleRef.init()` 與讀取磁碟設定檔或靜態資產路徑。只編譯過或只建立模組卻未執行初始化生命週期的測試，抓不到編譯後相對路徑層級算錯與執行期缺檔問題。NestJS 專案同步由 `framework/githooks/lib/nest-di-smoke.sh` 驗證空環境啟動安全性。

### 6. 雙異質模型對抗審查與線上通道雙向驗收

單一模型同時寫程式碼與審核容易產生盲點，因此交付流程採用盲審加上雙異質模型共識。

1. 在功能分支以明確檔案路徑提交 commit 後，執行 `paf lint-code` 與 `paf verify --repo . --mode pre-push` 產生綁定該 `HEAD` SHA 的驗證產物，再派不帶作者設計理由的唯讀子代理對 diff 與驗證輸出做盲審。若盲審抓出問題，修改後必須重新提交新 commit、重跑 `paf verify --repo . --mode pre-push` 並對新 SHA 重驗盲審，通過後才執行 `paf record-review --repo . --verdict approve --reviewer blind-subagent --summary "<summary>"` 寫入綁定當前 `HEAD` SHA 的盲審收據。
2. 執行 `paf ship --repo . --consensus --author-family <claude|gemini>` 開立或更新帶 `pr_number` HMAC 簽章的 PR，並直接喚起另一模型家族在固定長對話 session 來回審查修正。
3. 盲審與異質模型審查抓到的每一個真實問題，修復時都要同步寫成單元測試或架構檢查規則，轉為永久檢查條件。
4. 當最新 SHA 取得異質模型 `approve` 共識且 GitHub Actions CI 全綠後，`paf ship --consensus` 或 `paf merge-consensus --repo . --pr <num>` 直接執行內部帶 `--match-head-commit` 的原子合併、關閉並清除雙方模型 session，接著執行 `post_merge_check`，若有設定 `deploy` 則自動執行部署並在部署後再測試一次 `post_deploy_check`。治理檔案包含 `.agent-verify.json` 與 `.github/workflows/ci.yml` 一樣直接由另一異質模型確認後自動合併，不暫停詢問使用者。
5. 合併完成後追蹤建置與部署作業，核對線上運行的映像檔 SHA 與啟動日誌，並在真實使用者互動通道完成一次出站推播與進站回覆的雙向驗證，確認線上服務完全正常後才刪除退役的雲端資源。

---

## 二、流程 A — 建立全新個人 High-Trust Repo 的標準步驟

建立全新個人產品倉庫 `zone: "autonomous"` 時，從第一筆提交就依序把六項準則建好。若建立的是公司或團隊倉庫 `zone: "guarded"`，必須先與團隊對齊目錄規範、CI 工具與分支保護政策，取得明確同意後才套用對應步驟，且合併至 `main` 前必須停在 `PAUSED_FOR_MAIN_APPROVAL` 等待使用者與同事審查。

1. **建立單一職責目錄骨架**。單一服務倉庫直接採用根目錄 `src/` 與 `test/`；多服務單一倉庫將可部署應用放在 `apps/` 或 `services/`，共用基礎元件集中在 `packages/shared/`。每個功能模組把路由、邏輯、結構定義與測試放在同一個資料夾內。
2. **實作極薄共用基礎層與標準範本**。在共用套件建立名稱如實對應底層技術的基礎元件，為資料庫連線池預設掛上閒置 `error` 監聽器與 `max` 連線上限，為冷啟動 DDL 加上 advisory lock。建立一個最小可運作的標準功能或服務範本，讓後續開發直接照著寫。
3. **建立 `README.md`、`FEATURE_MAP.md` 與雙向架構檢查**。撰寫精簡的 `README.md` 說明安裝、開發、測試與部署指令。建立 `FEATURE_MAP.md` 登記所有套件入口、路由、工具與資料表。實作 `scripts/check-architecture.*` 雙向核對原始碼、SQL DDL 與 `FEATURE_MAP.md`，設定最低匹配數量下限與跨服務匯入禁令，並實際改錯一個表名或路由測試確認腳本會回傳非零退出碼攔截，驗證後還原。
4. **配置 `.agent-verify.json` 與遠端 CI**。在根目錄建立 `.agent-verify.json`，明確填寫 `"zone": "autonomous"`、`"default_branch": "main"`、`"require_blind_review": true`、`"consensus": { "require_hmac": true }` 與 `"dune_lint": { "enabled": true }`。在 `commands.lint` 放入 `scripts/check-architecture.*`，在 `commands.typecheck`、`commands.unit_test`、`commands.boot_smoke` 與 `commands.post_merge_check` 填入對應指令，確保無任何 `--passWithNoTests`。同步建立 `.github/workflows/ci.yml` 執行相同檢查。
5. **補齊行為級測試與完整初始化啟動測試**。為共用基礎層補齊過期時間、負數與零值邊界、字元跳脫與重試分流的單元測試，並為每個服務補上執行完整 `init()` 生命週期與隔離環境變數的啟動冒煙測試。
6. **首筆 PR 走完驗證與雙模型共識自動合併**。在功能分支提交初始骨架，執行 `paf lint-code` 與 `paf verify --repo . --mode pre-push`，通過唯讀盲審後執行 `paf record-review --repo . --verdict approve --reviewer blind-subagent --summary "<summary>"`，再以 `paf ship --repo . --consensus --author-family <claude|gemini>` 送交異質模型在固定長對話 session 審查確認。即使首筆 PR 包含 `.agent-verify.json` 與 `.github/workflows/ci.yml`，達成異質 `approve` 共識與遠端 CI 綠燈後直接自動合併、關閉雙方 session 並跑完合後驗證，無須人工治理確認。

---

## 三、流程 B — 既有 Repo 診斷與瘦身改造步驟

當使用者指派對既有倉庫進行 High-Trust 評估與瘦身改造時，嚴格依下列七個階段推進，確保在不影響現有使用者體驗的前提下移除閒置程式碼與過期檔案並補齊硬性欄杆。

### 階段 0 — 同步現況與校準流量基準

1. 執行 `git fetch` 與 `git status -sb` 確認本地工作樹與遠端主幹同步。
2. 統計並記錄改造前的總檔案數、程式碼總行數、套件清單與現有測試執行結果作為基準線。
3. 盤點生產環境過去 30 至 90 天的實際請求量、排程執行紀錄與日誌。在判定任何服務或路由為零流量之前，必須先用同一條查詢語法查詢一個確定每天都有流量的活躍服務作為對照組；對照組能查出正確筆數，目標服務查出的零筆才具備證據力。

### 階段 1 — 對照六項準則盤點缺口

逐一對照第一節的六項結構準則檢查現有倉庫並列出具體清單。

- 找出零流量的閒置服務、從未掛載的部署描述檔、無業務邏輯的單純轉發代理層，以及沒有任何呼叫者的死碼方法。
- 找出名稱與底層實作不符的誤導性包裝類別，以及散落在多個服務裡重複實作的資料庫連線、快取、重試與通知程式碼。
- 找出與現況不符或彼此矛盾的架構文件、散落各處的過期說明檔，以及缺失的 `FEATURE_MAP.md`、雙向架構檢查、邊界測試與完整 `init()` 啟動測試。
- 若倉庫為 `zone: "guarded"` 團隊資產，將上述缺口與建議優先順序寫入 `long-term-memory/guardrail-gaps.md` 後回報使用者裁決，停止直接改動共用倉庫。若倉庫為 `zone: "autonomous"` 個人資產且任務明確授權改造，維持所有活躍路由、排程與使用者互動行為不變，建立功能分支進入階段 2。

### 階段 2 — 抽離極薄共用基礎層並統一連線管理

1. 建立或整併單一的極薄共用套件，將各服務重複的基礎設施邏輯收斂為名稱如實反映底層技術的原語元件。
2. 每個服務收斂為單一資料庫連線池實例，補齊閒置 `error` 事件監聽器與符合資料庫規格的 `max` 連線上限，移除繞過共用連線池的獨立連線查詢。
3. 統一實作帶過期機制的鍵值與清單儲存，確認字串與清單結構都具備過期時間欄位與查詢過濾條件，`LIKE` 查詢正確跳脫 `%` 與 `_`，清單截斷與範圍查詢正確處理零值與負數索引，且冷啟動 DDL 初始化以 advisory lock 保護。

### 階段 3 — 刪除零流量閒置資產與過期文件

1. 刪除經階段 0 流量對照組確認無人使用的閒置服務、單跳轉發代理、未使用的協定伺服器、死碼方法與廢棄的部署設定檔，並從套件管理檔移除不再使用的第三方相依套件。
2. 清理倉庫內所有過期、重複或描述舊架構的文件，收斂為根目錄單一真實的 `README.md` 與 `FEATURE_MAP.md`。
3. 逐一核對活躍服務的公開路由路徑、排程觸發端點、環境變數合約與資料庫正式資料表結構，確保線上既有功能零退化。

### 階段 4 — 補齊雙向架構閘門、反例驗證與完整啟動測試

1. 撰寫 `FEATURE_MAP.md` 與 `scripts/check-architecture.*`，雙向鎖定程式碼裝飾器、工具定義、SQL DDL、路由清單與 `FEATURE_MAP.md` 表格的一對一對應，同時掃描 `src/` 與 `test/` 的匯入邊界、退役套件禁令、`--passWithNoTests` 禁令與 `*-latest` 浮動模型別名禁令，並設定檔案數、路由數與資料表數的最低下限。
2. 實際在 `FEATURE_MAP.md` 或程式碼製造一處不一致的反例，執行確認 `check-architecture` 回傳非零失敗後還原。
3. 更新 `.agent-verify.json` 與 `.github/workflows/ci.yml`。把 `scripts/check-architecture.*` 放入 `commands.lint`，對齊 `typecheck`、`unit_test`、`boot_smoke` 與 `post_merge_check` 固定鍵名，並將 `cjk_allow_globs` 收緊至必要的單一檔案路徑。
4. 為每個留存的可部署服務補齊完整呼叫 `init()` 生命週期且隔離外部設定檔路徑的啟動冒煙測試，執行 `paf lint-code` 與 `paf verify --repo . --mode pre-push` 直到全數通過。

### 階段 5 — 提交、盲審與雙異質模型共識收斂

1. 以明確檔案路徑暫存並提交 commit，禁止執行 `git add -A`。在該 `HEAD` SHA 上執行 `paf verify --repo . --mode pre-push`，再派唯讀子代理在無作者推論脈絡下對完整 diff 與測試輸出進行盲審。若盲審發現問題，修改後重新提交 commit、重跑 `paf verify --repo . --mode pre-push` 並重驗盲審，通過後執行 `paf record-review --repo . --verdict approve --reviewer blind-subagent --summary "<summary>"` 紀錄綁定該 SHA 的盲審收據。
2. 執行 `paf ship --repo . --consensus --author-family <claude|gemini>` 開立或更新帶 `pr_number` HMAC 簽章的 PR，交由異質模型家族在固定長對話 session 來回審查。
3. 異質模型若提出 `request_changes`，除了修好程式碼之外，一併將該類問題補進單元測試或靜態檢查規則，提交新 commit 並重跑驗證與異質審查，直到最新 SHA 獲得異質模型 `approve` 且遠端 GitHub Actions CI 全綠；即使是 `.agent-verify.json` 或 `.github/workflows/ci.yml` 等治理檔案，也直接由異質模型確認後自動原子合併、關閉雙方 session，並依序執行合後驗證、部署與部署後測試。

### 階段 6 — 部署核對、真實通道雙向驗收與雲端清理

1. 合併後監看雲端建置與部署流水線完成，查詢線上服務修訂版本與容器映像檔 SHA，並讀取啟動日誌確認無連線或初始化錯誤。
2. 主動觸發排程端點或健康檢查驗證出站路徑，同時檢查外部 webhook 狀態並與使用者在真實互動通道完成一次進站與出站的雙向對話驗收。
3. 確認線上服務運作完全正常後，指定明確服務名稱刪除已退役的舊雲端服務或舊排程項目，執行 `paf garden --repo . --prune-merged-branches` 清理已合併的本地分支，並更新專案記憶與結案紀錄。

---

## 四、High-Trust Repo 驗收檢查表

完成新建或改造後，逐項核對以下條件全數成立才算達標。

- [ ] 同功能程式碼、結構定義與測試集中在同一目錄，可部署服務之間零跨目錄直接匯入
- [ ] 共用基礎層極薄且名稱如實對應底層技術，無誤導性技術別名包裝，無單純轉發的網路中介層，並附有標準範本
- [ ] 資料庫連線池具備閒置 `error` 監聽器與明確 `max` 上限，冷啟動冪等 DDL 受 advisory lock 保護
- [ ] 根目錄具備 `FEATURE_MAP.md`，且放入 `commands.lint` 的 `scripts/check-architecture.*` 雙向驗證路由、工具與 SQL 資料表一對一吻合
- [ ] 架構檢查具備檔案數、路由數與資料表數最低下限，並已實際注入反例確認會失敗攔截
- [ ] 架構檢查同時涵蓋 `src/` 與 `test/`，攔截退役套件、`--passWithNoTests` 與 `*-latest` 浮動模型別名
- [ ] `.agent-verify.json` 使用合法 `commands` 鍵名且與 `.github/workflows/ci.yml` 同步生效，`dune_lint` 開啟且 `cjk_allow_globs` 僅精確到單一必要檔案
- [ ] 單元測試涵蓋所有目標結構的過期條件、字元跳脫、零與負數邊界及重試錯誤分流
- [ ] 每個可部署服務具備執行完整初始化生命週期如 `init()` 且隔離環境變數的啟動冒煙測試
- [ ] 提交後依序通過 `paf verify`、唯讀盲審 `paf record-review`、雙異質模型 SHA 鎖定共識與 `paf merge-consensus`，部署後完成映像檔 SHA 核對與真實通道雙向驗收
