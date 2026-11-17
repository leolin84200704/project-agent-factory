# Gemini 與 Claude Code 模型能力評分、執行時間與自動路由規範

> 單一真相來源與自動執行引擎：`framework/scripts/model_router.py`、`bin/paf`（`paf select-model`）、`framework/scripts/paf_watcher.py`。

這份規範整理 Google Gemini（`agy` CLI）與 Anthropic Claude Code（`claude` CLI）現役模型的能力評分、回應時間與自動選模規則。

兩家模型都支援可調式思考深度（`agy` 模型代碼內建 `low / medium / high / xhigh`；`claude` CLI 透過 `--effort low / medium / high / xhigh / max` 指定）。同一個模型在低思考與高思考下的執行時間相差 5 到 10 倍。過去互動環境常停在 `gemini-4-carbon-high` 或 `~/.claude/settings.json` 的 `opus[1m]` 加 `effortLevel: "xhigh"`，若子程序沒有顯式傳入 `--model` 與 `--effort`，連改幾行文件都會等上數分鐘。因此所有任務分派與檢測審查都必須先經過自我評斷並顯式指定模型參數。

---

## 一、全模型能力評分表（滿分 10 分）

評分維度：
- **規劃推理**：需求拆解、系統架構設計、跨系統風險預判。
- **寫碼實作**：依照規格產出正確程式碼、重構與補測試的準度。
- **除錯定位**：從 log、資料庫狀態與程式碼追出真正根因的能力。
- **審查抓漏**：擔任 PR reviewer 時找出邊界條件、安全漏洞與邏輯瑕疵的能力。
- **工具長鏈**：多步驟呼叫工具、讀多檔且不中途偏離目標的穩定度。
- **回應速度**：延遲越低、輸出越快，分數越高（10 分為秒級回應，1 分為數分鐘級等待）。

### 1. Google Gemini（本機 `agy --model` 實測可用模型）

| 模型名稱 | CLI `--model` 參數 | 規劃推理 | 寫碼實作 | 除錯定位 | 審查抓漏 | 工具長鏈 | 回應速度 | 綜合定位 |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Gemini 4 Carbon (High)** | `gemini-4-carbon-high` | 9.7 | 9.3 | 9.6 | 9.6 | 9.4 | 2.5 | 最強深層推理與高風險把關，等待時間最長 |
| **Gemini 4 Carbon (Medium)** | `gemini-4-carbon-medium` | 9.4 | 9.2 | 9.4 | 9.4 | 9.2 | 4.0 | 深層推理折衷版，延遲比 Carbon High 短 |
| **Gemini 4 Argon (High)** | `gemini-4-argon-high` | 9.1 | 9.0 | 9.1 | 9.2 | 9.0 | 5.0 | 高階工程規劃與跨模組審查，延遲約 Carbon 三分之一 |
| **Gemini 4 Argon (Medium)** | `gemini-4-argon-medium` | 8.8 | 8.8 | 8.8 | 8.9 | 8.8 | 6.2 | 中大型功能規劃與穩健審查 |
| **Gemini 4 Hydrogen (XHigh)** | `gemini-4-hydrogen-xhigh` | 8.8 | 8.9 | 8.7 | 8.8 | 8.8 | 5.5 | 進階思考版主力實作與複雜修改 |
| **Gemini 4 Hydrogen (High)** | `gemini-4-hydrogen-high` | 8.2 | 8.6 | 8.2 | 8.3 | 8.5 | 8.0 | 速度與品質最均衡的日常預設執行與審查主力 |
| **Gemini 4 Hydrogen (Medium)** | `gemini-4-hydrogen-medium` | 7.4 | 8.0 | 7.4 | 7.3 | 7.9 | 9.2 | 小範圍低風險修改、補單元測試與設定調整 |
| **Gemini 4 Hydrogen (Low)** | `gemini-4-hydrogen-low` | 6.8 | 7.6 | 6.8 | 6.7 | 7.4 | 9.5 | `agy` CLI 最快模型，適合前置路由與極簡任務 |
| **Gemini 3.8 Flash (High)** | `gemini-3.8-flash-high` | 7.2 | 7.6 | 7.2 | 7.1 | 7.5 | 6.5 | 舊世代快模型，已被 Hydrogen 系列全面超越 |
| **IDE Flash Lite 子代理** | IDE `Model="flash_lite"` | 4.5 | 5.2 | 4.2 | 4.0 | 5.0 | 10.0 | 僅限 IDE 內建 subagent 純分類與抽取，非 `agy` CLI 參數 |

### 2. Anthropic Claude Code（本機 `claude --model` 與 `--effort` 實測可用模型）

| 模型名稱 | CLI `--model` 與 `--effort` | 規劃推理 | 寫碼實作 | 除錯定位 | 審查抓漏 | 工具長鏈 | 回應速度 | 綜合定位 |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Claude Fable 5 (High)** | `--model fable --effort high` | 9.9 | 9.7 | 9.8 | 9.8 | 9.7 | 3.0 | 最高階跨倉架構規劃與死鎖難題排解（`claude-fable-5`） |
| **Claude Opus 5 (XHigh)** | `--model opus --effort xhigh` | 9.6 | 9.6 | 9.6 | 9.6 | 9.6 | 4.2 | 高風險核心變更實作與嚴格安全把關（`claude-opus-5`） |
| **Claude Opus 5 (High)** | `--model opus --effort high` | 9.4 | 9.5 | 9.4 | 9.4 | 9.5 | 5.5 | 跨模組重構、治理規則與複雜 PR 共識主力 |
| **Claude Sonnet 5 (High)** | `--model sonnet --effort high` | 8.4 | 8.9 | 8.4 | 8.5 | 8.7 | 7.8 | 標準功能實作、一般 bug 修復與第一輪 PR 審查（`claude-sonnet-5`） |
| **Claude Sonnet 5 (Medium)** | `--model sonnet --effort medium` | 8.1 | 8.7 | 8.1 | 8.3 | 8.5 | 8.4 | 低風險小改動與純文件 PR 快速審查 |
| **Claude Sonnet 5 (Low)** | `--model sonnet --effort low` | 7.5 | 8.2 | 7.4 | 7.5 | 8.0 | 9.1 | 文案、設定檔與單純測試微調 |
| **Claude Haiku 4.5 (Low)** | `--model haiku --effort low` | 6.0 | 6.8 | 5.8 | 5.8 | 6.5 | 9.8 | 秒級分類、ticket 前置分派與大量 log 初篩（`claude-haiku-4-5`） |

---

## 二、任務執行時間與速度對照

下表整理實測與實務體感時間範圍。實際耗時會隨讀取檔案數量與測試執行時間變動。

| 模型與思考設定 | 輸出速度參考 | 首字延遲（TTFT） | 單輪對話時間 | 完整任務（讀檔+改碼+跑測試） |
| :--- | :--- | :--- | :--- | :--- |
| **Gemini 4 Carbon (`high`)** | 約 76 tok/s | 120 到 160 秒 | 2 到 6 分鐘 | 6 到 15 分鐘以上 |
| **Gemini 4 Argon (`high`)** | 中等 | 35 到 55 秒 | 45 秒到 2 分鐘 | 3 到 7 分鐘 |
| **Gemini 4 Hydrogen (`xhigh`)** | 中快 | 20 到 45 秒 | 40 秒到 2 分鐘 | 2.5 到 6 分鐘 |
| **Gemini 4 Hydrogen (`high`)** | 約 190 tok/s | 5 到 15 秒 | 15 到 45 秒 | 1 到 3 分鐘 |
| **Gemini 4 Hydrogen (`low / medium`)** | 約 190+ tok/s | 1 到 6 秒 | 3 到 15 秒 | 20 秒到 1.5 分鐘 |
| **Claude Fable 5 (`high`)** | 中慢 | 30 到 90 秒 | 1.5 到 5 分鐘 | 5 到 12 分鐘以上 |
| **Claude Opus 5 (`xhigh`)** | 中慢 | 20 到 45 秒 | 45 秒到 2 分鐘 | 3 到 7 分鐘 |
| **Claude Opus 5 (`high`)** | 中等 | 10 到 30 秒 | 25 秒到 1.5 分鐘 | 2 到 5 分鐘 |
| **Claude Sonnet 5 (`medium / high`)** | 快 | 3 到 12 秒 | 8 到 35 秒 | 45 秒到 2.5 分鐘 |
| **Claude Haiku 4.5 (`low`)** | 極快 | 1 到 3 秒 | 2 到 10 秒 | 15 到 45 秒 |

---

## 三、各模型優劣勢與適用場景

### Google Gemini 系列

1. **Gemini 4 Carbon**
   - 優勢是深層推理與假設驗證極強，能主動抓出並發競爭、權限繞過與資料一致性漏洞。
   - 劣勢是 `high` 模式首字延遲常超過兩分鐘且配額消耗大，拿來跑一般任務會拖慢整體回饋迴圈。
   - 適用資料庫 schema 破壞性異動、金流與認證機制、跨服務疑難事故、以及 `critical` 等級 PR 把關。
2. **Gemini 4 Argon**
   - 優勢是保留接近 Carbon 的多模組推理深度，同時把首字延遲降到三分之一左右。
   - 適用跨模組技術規劃、治理與核心工具鏈變更、以及 `complex` 等級 PR 雙模型審查。
3. **Gemini 4 Hydrogen**
   - 優勢是輸出達 190 tokens/s 且工具呼叫穩定，四段思考深度可從秒級分類一路涵蓋到複雜實作。
   - `low` 專責秒級路由與探索；`medium` 處理文件與設定小改；`high` 負責絕大多數標準業務開發與第一輪 PR 審查；`xhigh` 負責複雜程式修改。

### Anthropic Claude Code 系列

1. **Claude Fable 5 (`fable`)**
   - 優勢是多步長鏈推理與模糊需求拆解最強，防呆考慮最完整。
   - 劣勢是單價與延遲最高，僅保留給 `critical` 等級架構規劃、死鎖除錯、或顯式開啟 `allow_apex_reviewer` 的最高風險審查。
2. **Claude Opus 5 (`opus`)**
   - 優勢是長週期寫碼、跨檔案重構與規範遵循度極穩。
   - 適用 `complex` 與 `critical` 等級的規劃、實作與異質審查（依風險搭配 `--effort high` 或 `--effort xhigh`）。
3. **Claude Sonnet 5 (`sonnet`)**
   - 優勢是延遲低、互動流暢、寫碼與抓漏能力均衡，成本僅 Opus 一半。
   - 適用 `trivial` 與 `standard` 等級的日常開發、作者修正回合與標準 PR 審查。
4. **Claude Haiku 4.5 (`haiku`)**
   - 優勢是 1 到 3 秒內回應且成本極低。
   - 適用新 ticket 進件時的第一步分類、快速檔案掃描與大量 log 過濾，不單獨承擔複雜寫碼或安全把關。

---

## 四、強制自我評斷規則與自動模型路由表

### 1. 兩條硬性規則

1. **新 Ticket／新任務進件必先分派四角色模型**：收到新任務或進入 Work Loop Step 1 時，先執行 `paf select-model` 依標題、描述、預估點數或預計觸碰路徑評斷風險等級，明確決定這張單用哪一個模型做 **規劃（`planner`）**、哪一個模型做 **執行（`executor`）**、哪一個模型做 **檢測審查（`reviewer`）**，並寫入 STM 的 `### Model Plan`。
2. **每次呼叫模型做檢測評斷必先自評風險並鎖定參數**：無論是本地盲審 subagent、`paf ship --consensus`、`paf drive-consensus` 或背景 `paf watch-prs`，在啟動審查模型前都必須先根據實際 git diff、變更檔案路徑與爭議輪次完成複雜度評斷，並把對應的 `--model` 與 `--effort` 顯式傳給 `claude` 或 `agy`，禁止不帶參數直接繼承外部預設環境。

### 2. 四級風險判準與四角色模型路由表

`framework/scripts/model_router.py` 依下列確定性訊號將任務與 PR 分為四級：

| 等級 (`tier`) | 觸發條件（任一成立即歸入該級，由高往低匹配） | 預估時間 | Router（前置分派） | Planner（規劃） | Executor（執行／修正） | Reviewer（異質檢測審查） |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`critical`** | DB migration / `ALTER` / `DROP`、`auth` / `security` / `crypto` / `billing` / `HMAC`、`PERMISSIONS.md`、`pre-tool-guard.py`、線上事故（`--incident`）、檔案數 `> 20`、變更行數 `> 1200`、點數 `>= 8`、或共識進入第 3 輪以上 | 10–25 分鐘 | `haiku (low)` / `hydrogen-low` | `fable (high)` / `carbon-high` | `opus (xhigh)` / `argon-high` | `opus (xhigh)`（或 `fable`） / `carbon-high` |
| **`complex`** | 修改治理或核心引擎檔（`CORE.md`、`AGENTS.md`、`.agent-verify.json`、`bin/paf`、`paf_watcher.py`、hooks、workflows、skills）、跨倉或並發狀態機、檔案數 `9–20`、變更行數 `401–1200`、點數 `5–7`、或共識進入第 2 輪 | 5–12 分鐘 | `haiku (low)` / `hydrogen-low` | `opus (high)` / `argon-high` | `opus (high)` / `hydrogen-xhigh` | `opus (high)` / `argon-high` |
| **`standard`** | 一般業務邏輯新增、單一服務 bug 修復、測試與腳本更新（未命中高風險路徑，檔案數 `<= 8` 且變更行數 `<= 400`） | 2–5 分鐘 | `haiku (low)` / `hydrogen-low` | `sonnet (high)` / `hydrogen-high` | `sonnet (high)` / `hydrogen-high` | `sonnet (high)` / `hydrogen-high` |
| **`trivial`** | 純文件或說明檔（`.md` / `.txt` / `.rst` / `.csv`）且檔案數 `<= 3`、變更行數 `<= 120`；或點數 `<= 1` 的純文案微調 | 0.5–2 分鐘 | `haiku (low)` / `hydrogen-low` | 與 Executor 合併（`sonnet-low` / `hydrogen-low`） | `sonnet (low)` / `hydrogen-medium` | `sonnet (medium)` / `hydrogen-high` |

> 審查者永遠優先選取與作者相反的模型家族（`claude` 作者配 `gemini` 審查者；`gemini` 作者配 `claude` 審查者）。當異質家族發生 API 配額耗盡或逾時而觸發全新空白 session 同家族備援時，路由引擎也會自動套用該家族同等級的審查模型與 effort。

### 3. CLI 與自動化整合用法

1. **新 Ticket 進件分派**：
   ```bash
   paf select-model --title "[VP-19401] Fix empty middle name in mapper" --story-points 2 --author-family claude
   ```
2. **分支或 PR 檢測前依實際 Git Diff 自評**：
   ```bash
   paf select-model --repo . --diff-base main --author-family gemini
   paf select-model --repo . --diff-base main --author-family gemini --role reviewer --json
   ```
3. **`paf ship --consensus`、`paf drive-consensus` 與 `paf watch-prs` 自動執行**：
   - 未指定 `--claude-model` / `--agy-model`（或設為 `auto`）時，`paf_watcher.py` 每一輪都會呼叫 `model_router.select_pr_models()`，讀取 PR 標題、描述、變更檔案清單、實際 diff 行數與當前 `round`，自動為審查端與作者修正端選取模型與 `--effort`，並在 stderr 印出 `[paf model-router]` 稽核紀錄。
   - 若第 1 輪 `standard` 審查提出 `request_changes` 進入第 2 輪，路由器會自動把第 2 輪升階到 `complex`（`opus --effort high` / `gemini-4-argon-high`），第 3 輪升階到 `critical`（`opus --effort xhigh` / `gemini-4-carbon-high`），在同一條持久化 session 上集中火力收斂爭議。
4. **個別倉庫自訂上下限（`.agent-verify.json` 選填設定）**：
   倉庫可在 `.agent-verify.json` 加入 `model_routing` 調整最低／最高等級或啟用 `fable` 最高階審查：
   ```json
   "model_routing": {
     "min_tier": "standard",
     "max_tier": "critical",
     "allow_apex_reviewer": false
   }
   ```
