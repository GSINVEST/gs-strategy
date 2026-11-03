# 即時策略圖（Live Strategy Graph）規格

> 狀態：Phase A／B 已實作；R5 統計不足邊界見附錄 B，Phase B 驗收見 `docs/acceptance/live-strategy-graph-phase-b.md`
> 相關：[`statistical-decision-tree.md`](statistical-decision-tree.md)、[`auto-research-funnel.md`](auto-research-funnel.md)
> 首個落地對象：`strategies/tsmom_tx_mtx`

---

## 0. 提案

### 為什麼要做

harness 的 trials 搜尋樹（`:9101/trials`）以「一次試驗」為節點，只能事後 replay。
研究者看不到一支策略**此刻**的參數、每一步的中間值、統計判決是怎麼推出來的。
統計決策樹（`decision.py`）本身就是一張 DAG（事實 → 五槽位 → 處方），但埋在程式裡。

本規格把節點單位從「試驗」換成「策略的一個運算步驟」，借用 ComfyUI 的四個機制：

1. **型別化接點**：接錯型別接不上；缺輸入的節點會亮紅燈並說明原因。
2. **增量重算**：以內容 hash 快取，只重算參數被改動之後的下游節點。
3. **workflow 即資料**：圖（接線＋參數）是一份 JSON，嵌進每一份回測產出，拖回來就能重現。
4. **節點是程式碼、接線是資料**：節點實作是 repo 內經 review 的 Python 函式；接線與參數在圖檔裡。

### 最重要的約束：即時介面不得成為 p-hacking 機器

`auto-research-funnel.md` §1 已算過：N 從 33 增加到 382，E[max SR] 會上升 40.6%，而且這個門檻是全域的。
能即時拉滑桿的介面若不記帳，N 會在沒人察覺的情況下暴增。所以本規格把
`stat-ruleset-1.1.yaml` 的 `purpose` N 記帳規則做成 UI 的**機械行為**，不靠研究者自律。

### 範圍

**MVP（本規格）要做：**
- 圖的定義格式、型別系統、執行引擎（含 hash 快取）
- 本機 Web 編輯器：看圖、改參數、看每個節點的即時輸出
- `tsmom_tx_mtx` 改寫成第一張圖，回測結果須與現行 `strategy.py` 等價
- Backtest 執行自動寫入 trial ledger（`log/trials.jsonl`），N 與 deflation 門檻常駐顯示
- 統計決策樹做成可視節點（Facts → Resolver 五槽位 → Prescription）
- 產出（validation sidecar、manifest）嵌入圖 hash 與圖快照

**MVP 不做（第二階段）：**
- production 模式：即時報價 Data 節點、下單節點、drift 監控節點
- diagnostic 模式（反應面掃描），規則見 §5；它會改變 N 記帳，須連同規則集 1.2 一起修訂
- PBO / CPCV 節點（需要多組試驗矩陣）
- 其他三支策略（`vgrsi_tx`、`cubic_momentum_tx`、`xsmom_stkfut_rmt`）的遷移
- 任意新增節點類型的 UI（MVP 只能在已註冊的節點之間接線）

**不改：**
- `runner.py` 的既有 `--strategy/--config` 路徑照常可用，現有策略不受影響
- `decision.py` 與規則集 1.1 的判準與輸出

---

## 1. 名詞

| 名詞 | 定義 |
|---|---|
| 節點類型（node type） | 一個已註冊的 Python 函式，宣告輸入／輸出接點型別與參數 schema |
| 圖（graph） | 節點實例、參數值、接線的集合，存成 `strategies/<id>/graph.json` |
| 圖 hash | 對「節點類型 id ＋節點實作程式碼指紋 ＋參數 ＋接線 ＋資料區間」正規化後取的 hash |
| 節點 hash | 該節點自身（類型、實作指紋、參數）加上所有上游節點 hash 的組合，作為快取鍵 |
| 上游預覽區 | Backtest 節點上游的所有節點（Data / Feature / Signal / Sizing / Cost） |
| 試驗區 | Backtest 節點及其下游（Validation / Facts / Resolver） |

---

## 2. 節點與型別（MVP：tsmom_tx_mtx）

| 節點類型 | 參數 | 輸入 | 輸出 |
|---|---|---|---|
| `data.futures_bars` | bundle, roots, start, end | — | `Bars` |
| `data.continuous` | — | `Bars` | `ContinuousBars` |
| `feature.signed_momentum` | lookback, skip | `ContinuousBars` | `Score` |
| `feature.ewma_vol` | vol_com | `ContinuousBars` | `Sigma` |
| `signal.direction` | allow_short | `Score` | `Direction` |
| `sizing.vol_target` | target_vol | `Direction`, `Sigma` | `RawWeights` |
| `sizing.gross_cap` | max_gross_leverage | `RawWeights` | `Weights` |
| `cost.futures` | per_contract_cost, spread_points | — | `CostModel` |
| `backtest.zipline` | capital_base, calendar, days_before_close | `Weights`, `CostModel` | `Returns`, `Positions` |
| `ledger.selection_n` | — | — | `LedgerN` |
| `validation.report` | — | `Returns`, `LedgerN` | `Report` |
| `stat.facts` | estimand, design, overlap, family, purpose, holding_periods | `Returns` | `Facts` |
| `stat.resolve` | ruleset_version | `Facts`, `LedgerN` | `Prescription` |

參數預設值一律取自現行 `strategies/tsmom_tx_mtx/config.yaml`。
節點怎麼拆、函式放哪裡由實作決定；上表規定的是**使用者在畫面上看到的節點與接點**。

---

## 3. 需求

### R1 型別化接線

- **需求**：系統 MUST 拒絕型別不相容的接線，並在 UI 上即時標示。
  - **情境**：GIVEN 編輯器開著 tsmom 圖 → WHEN 把 `feature.ewma_vol` 的 `Sigma` 輸出拖到 `signal.direction` 的 `Score` 輸入 → THEN 接線不成立，游標／接點顯示不相容提示，圖檔內容不變。
  - **判準**：型別檢查單元測試全綠；載入含非法接線的 `graph.json` 時回報錯誤並指出哪條線，不得靜默丟棄。

- **需求**：型別 `Returns` MUST 只能由 `backtest.*` 節點產生。
  - **理由**：確保上游預覽區不可能算出任何績效數字（N 記帳的前提，見 R5）。
  - **情境**：GIVEN 一個 Feature 節點類型宣告輸出 `Returns` → WHEN 註冊該節點類型 → THEN 註冊失敗並說明原因。
  - **判準**：對應測試全綠。

- **需求**：必要輸入未接的節點 MUST 顯示為「未就緒」並列出缺哪個接點，不得以預設值補上執行。
  - **情境**：GIVEN 把 `sizing.vol_target` 的 `Sigma` 輸入線刪掉 → WHEN 執行 → THEN 該節點與其下游標示未就緒，訊息寫明缺 `Sigma`，其餘上游節點照常顯示結果。

### R2 增量重算與快取

- **需求**：改動某節點參數後，系統 MUST 只重算該節點及其下游；上游節點 MUST 命中快取。
  - **情境**：GIVEN tsmom 圖已完整跑過一次 → WHEN 把 `sizing.vol_target.target_vol` 由 0.15 改為 0.10 並執行 → THEN `data.*`、`feature.*`、`signal.direction` 顯示「快取」，`sizing.*`、`backtest.zipline` 及下游顯示「已重算」。
  - **判準**：執行引擎測試斷言各節點的實際執行次數；UI 上每個節點都有狀態標記（快取／執行中／已重算／錯誤／未就緒）。

- **需求**：節點實作的程式碼改變時，快取 MUST 失效。
  - **情境**：GIVEN `feature.signed_momentum` 已有快取 → WHEN 修改其實作函式本體（不含 docstring／註解）→ THEN 下次執行時該節點與下游重算。
  - **判準**：對應測試全綠。程式碼指紋的正規化方式須與 `scripts/triage_generated.py` 的 AST 指紋一致（去 docstring）。

- **需求**：快取 MUST 存在磁碟上，重開伺服器後仍然有效；快取目錄 MUST 在 `.gitignore` 內。

### R3 即時預覽

- **需求**：上游預覽區的節點在參數改動後 SHOULD 自動重算並更新預覽，不需按「執行」。
  - **情境**：GIVEN 編輯器開著 → WHEN 拖動 `feature.signed_momentum.lookback` 滑桿 → THEN 該節點與下游的上游預覽區節點更新預覽（防抖處理，拖動中不得堆積重算），`backtest.zipline` 不會被觸發。

- **需求**：每個節點 MUST 在節點卡片上顯示其輸出的精簡預覽：
  - 時間序列型別（`Bars`、`Score`、`Sigma`、`Weights` 等）：最近區間的 sparkline 加上最新值
  - `Returns`：權益曲線
  - `Report`：Sharpe、PSR、DSR、MDD、CAGR，以及 `warnings`
  - `Prescription`：五個槽位（base / se / primary / threshold / gates）各自的結果與 `rule_id`
  - 點開節點 SHOULD 看到完整圖表與原始數值表

### R4 明確執行（ComfyUI 的 Queue）

- **需求**：`backtest.zipline` MUST 只在使用者按下「執行」時才跑，參數變動不得自動觸發。
  - **理由**：每次回測都可能讓 N 加 1（R5），自動觸發等於拖一次滑桿就燒掉一堆 N。
  - **情境**：GIVEN 改了 `sizing.gross_cap.max_gross_leverage` → WHEN 尚未按執行 → THEN Backtest 以下的節點標示「過期」，顯示的是上一次的結果並清楚標註它對應的是舊參數。

- **需求**：回測執行中 MUST 顯示進度與可取消；取消的執行 MUST 不寫入 ledger。

### R5 N 記帳（核心約束）

- **需求**：每一個**新的回測組態**（`backtest.zipline` 節點 hash，即 Backtest 及其全部上游的組合；**不含**下游 Validation／Facts／Resolver 的參數與任何 UI 版面欄位）第一次成功跑完 `backtest.zipline`，系統 MUST 自動在 `log/trials.jsonl` 追加一筆 `purpose: selection` 的 trial 記錄，`stat_decision.delta_n = 1`。使用者 MUST NOT 能關閉這個行為。
  - **理由**：只改下游統計宣告不會產生新的績效資訊，不得讓 N 增加（Phase A code review 修訂）。
  - **情境**：GIVEN 已跑過一次 → WHEN 只改 `stat.facts.family` 再執行 → THEN Backtest 命中快取、N 不變。
  - **情境**：GIVEN ledger 上 selection N = 33 → WHEN 以一組從未跑過的參數執行回測 → THEN ledger 多一筆記錄，畫面上的 N 變為 34，deflation 門檻（E[max SR]）同步更新。
  - **判準**：整合測試以暫存 ledger 驗證；`decision.audit_ledger` 對新記錄稽核無問題；`scripts/triage_generated.py` 的 `count_existing_selection_trials` 讀出的 N 與畫面一致。

- **需求**：同一個圖 hash 重跑 MUST 命中快取、MUST NOT 追加記錄（與 funnel L0「同組態重跑不產生新資訊」一致）。
  - **情境**：GIVEN target_vol 從 0.15 改成 0.10 並執行（N+1）→ WHEN 改回 0.15 並執行 → THEN 直接取快取結果，N 不變。

- **需求**：上游預覽區的所有運算 MUST NOT 寫入 ledger（相當於 funnel 的 L0–L2，ΔN = 0）。

- **需求**：畫面 MUST 常駐顯示：目前 selection N、對應的 E[max SR]、本次 session 已新增的 N。
  E[max SR] 的計算 MUST 與 `scripts/triage_generated.py` 的 `expected_max_sharpe` 相同。

- **需求**：ledger 為 append-only，系統 MUST NOT 修改或刪除既有行。

- **需求**：trial 記錄 MUST 含 `config_ref`（指向圖 hash 與圖快照）、`ruleset_version`、`decision_path`、`rule_id`，格式與 `decision.py` 的 `Prescription.to_record()` 相容，且能被 harness 的搜尋前沿以 `config_ref` 比對為「已嘗試」。

### R6 統計決策樹可視化

- **需求**：`stat.facts` MUST 由 `Returns` **自動算出**可重算的事實（Jarque-Bera → `normal`、Ljung-Box → `autocorr`、有效樣本數 → `n_eff`），宣告型事實（estimand、design、overlap、family、purpose、holding_periods）由節點參數提供；`n_trials` MUST 來自 `LedgerN`，不得手填。
  - **情境**：GIVEN tsmom 回測完成 → WHEN 看 `stat.facts` 節點 → THEN 每個自動事實旁邊顯示其來源檢定與 p 值。

- **需求**：`stat.resolve` MUST 呼叫既有的 `decision.resolve`，不得另寫一套判準；`UnderdeterminedError` MUST 讓節點亮紅燈並原文顯示補救訊息（例如「請先跑 Ljung-Box」）。
  - **情境**：GIVEN `stat.facts` 無法判定 `autocorr` → WHEN 解析 → THEN `stat.resolve` 顯示紅燈、訊息，不產生處方；`validation.report` 不受影響照常顯示。

- **需求**：`stat.resolve` 卡片 MUST 將五個槽位分開呈現，每個槽位標示「由哪個事實決定」，`forbids` 清單必須顯示出來（例如成對設計禁用獨立雙樣本 t）。

### R7 圖檔與可重現性

- **需求**：圖 MUST 存成 `strategies/<id>/graph.json`，內容為人類可讀、可 diff 的 JSON（固定鍵序、固定縮排），進 git 版控。UI 的「存檔」寫回此檔；未存檔的修改 MUST 有明顯標示。

- **需求**：每次回測產出的 validation sidecar MUST 內嵌圖 hash 與完整圖快照；`manifest.yaml` 的 validation 區塊 MUST 記錄圖 hash。
  - **情境**：GIVEN 一份三週前的 sidecar → WHEN 在編輯器「從產出載入」→ THEN 還原出當時的圖（參數與接線），且圖 hash 與 sidecar 記錄相同；若當時的節點實作指紋與現行程式碼不同，MUST 明確警告「節點實作已變更，無法保證重現」。

- **需求**：圖 MUST 能在無 UI 的情況下以 CLI 執行（例如 `runner` 新增 `--graph` 參數，或獨立指令），行為與 UI 執行相同，包括 R5 的 ledger 記帳。

### R8 與現行策略等價（遷移正確性）

- **需求**：tsmom 的圖版本在相同資料、相同參數下 MUST 與現行 `strategies/tsmom_tx_mtx/strategy.py` 產出相同的回測結果。
  - **情境**：GIVEN 同一份 `tquant_future` bundle、`config.yaml` 預設參數 → WHEN 分別以舊路徑（`runner --strategy --config`）與圖路徑執行 → THEN 每日 `returns` 序列逐點差異的絕對值 < 1e-10，每日持倉口數完全一致。
  - **判準**：等價測試全綠；若因 bundle 需要 TEJ 金鑰而無法在 CI 執行，MUST 在本機跑過並將比對摘要（僅統計量，不含金鑰與帳務資訊）附在交付回報裡，CI 端以 fixture 資料跑同一條比對。

- **需求**：權重 MUST 只使用當根 K 棒收盤前可得的資料，並在下一根執行（與現行 `handle_data` 的時序相同）。
  - **判準**：一個前視偵測測試：把第 t 天之後的價格竄改掉，第 t 天（含）以前的 `Weights` 必須完全不變。

### R9 執行環境

- **需求**：伺服器 MUST 預設只綁 `127.0.0.1`；MUST 能用 `run.sh` / `run.ps1` 的一個子指令啟動，Windows 與 Linux 皆可運作。
- **需求**：回測以 CPU 執行（zipline 事件迴圈是強序列相依，不適用 GPU）。
- **需求**：任何 log、錯誤訊息、sidecar、UI 顯示 MUST NOT 包含金鑰或個資（例如 TEJ 金鑰）；在回報與程式註解中發現個資時，只寫到類別層級，不寫出種類、樣本、格式、次數、行號。

---

## 4. 任務清單（派工用）

- [x] 0. `/survey-first`：評估現成的 Python DAG／快取框架（如 Apache Hamilton）與前端節點編輯器（如 React Flow），決定採用或自建，結論寫進本檔附錄
- [x] 1. 節點類型註冊、型別系統、參數 schema（R1）
- [x] 2. 執行引擎：hash 快取、程式碼指紋、增量重算、節點狀態（R2）
- [x] 3. tsmom 拆成節點，`graph.json` 與等價測試、前視測試（R8）
- [x] 4. ledger 記帳與 N／E[max SR] 計算（R5；統計不足時的稽核限制見附錄 B）
- [x] 5. `stat.facts` / `stat.resolve` / `validation.report` 節點（R6）
- [x] 6. sidecar／manifest 嵌入圖 hash 與快照，CLI 執行路徑（R7）
- [x] 7. Web 編輯器：畫布、接線、預覽、執行／取消、存檔、從產出載入（R1–R4、R7）
- [x] 8. `run.sh` / `run.ps1` 啟動子指令、`.gitignore` 快取目錄（R9）
- [x] 9. README 補充 CLI／API 用法（Phase A 派工補充）

檔案切分與哪些項目可平行，派工時由 Claude 依實際檔案範圍宣告。

---

## 5. 第二階段預告（本 MVP 不實作，但介面必須留路）

- **production 模式**：只替換 `data.futures_bars` 為即時報價節點、`backtest.zipline` 為下單節點，中間節點不變。
  MVP 的 Data 節點輸出型別 MUST 設計成可被即時來源替換（同一型別）。
- **drift 節點**：比對即時的 `Score` / `Sigma` / `Weights` 與回測期間的分佈，偏離時亮燈。
- **diagnostic 模式**：必須在執行前宣告一組參數網格，結果只以反應面呈現；日後若有 selection trial 採用了該網格內的值，
  N 須追加整個網格的大小（看過 k 個選項再挑一個，等同 best-of-k）。這會改變 N 記帳規則，
  所以 MUST 與 `stat-ruleset-1.2` 一起修訂，不得單獨在程式裡實作。

## 附錄 A：技術選型（Phase A，2026-09-29）

GitHub API 當日快照；stars 不是品質保證，維護狀態以最後 push 及 archived 為依據。

| 候選 | Stars | 最近 push | License | R1 / R2 / R5 | 判定 |
|---|---:|---|---|---|---|
| [Apache Hamilton](https://github.com/apache/hamilton) | 2,601 | 2026-09-29 | Apache-2.0 | Python 型別 DAG / 支援快取 / 需自訂不可繞過記帳 | build |
| [React Flow / xyflow](https://github.com/xyflow/xyflow) | 38,537 | 2026-09-24 | MIT | 可自訂接點驗證 / 狀態顯示需後端 / 記帳需後端 | partial：Phase B 採用畫布 |
| [LiteGraph](https://github.com/jagenjo/litegraph.js) | 8,158 | 2024-08-01 | MIT | 接點與 JSON 完整 / JavaScript 執行非 Python / 記帳需後端 | 不採用 |

三者皆未 archived。Hamilton 適合由 Python 函式宣告資料依賴，但本案需要由固定節點白名單載入使用者 JSON 接線、隔離預覽與明確回測、將取消與 ledger 提交序列化。採用它仍須包覆上述生命週期。MVP 僅十三種節點，選擇 stdlib 小型執行核心，避免雙重 DAG 狀態；代價是自行維護快取與排程測試。實作使用與 triage 相同的 AST 去 docstring 正規化，並把明示 helper 依賴納入指紋。

React Flow 僅採用 Phase B 畫布、互動接點與自訂卡片；所有型別驗證、回測授權邊界與 R5 仍以 Python 為唯一真相。LiteGraph 最後更新較早，且內建 JavaScript 執行器與本案 CPU Zipline 事件迴圈重複，無採用優勢。

### 核心契約

`strategies._common.graph`：`NodeType(id, inputs, outputs, params, function, dependencies=(), cacheable=True)`；函式接受 `(inputs, params, context)` 並回傳依輸出接點命名的 dict。參數 schema 使用 JSON Schema 的 type/default/minimum/maximum/enum 子集。helper 函式或 Python 模組 Path 必須列入 dependencies。

圖格式為 `schema: live-strategy-graph/1`、`nodes: [{id,type,params}]`、`edges: [{from:[node,port],to:[node,port]}]`。固定鍵排序與兩格縮排。接點名稱使用規格型別名稱（例如 `Returns`）。節點 hash 含實作、參數及上游 hash；圖 hash 含正規化圖與所有實作指紋。資料來源必須將資料版本納入參數以避免 bundle 更新後重用舊快取。

`Context` 提供 token、ledger、services、progress。成功 Backtest（含快取重播）必經 `ledger.record_success(graph_hash,snapshot,outputs,context)`，呼叫前由引擎設定 context.backtest_key；ledger 缺少此鍵時拒絕記帳，不退回整張圖 hash 去重。取消與提交共用鎖。ledger 節點為非快取外部來源，完整執行時在回測提交後取樣，讓報告使用最新 N。統計失敗不阻擋已成功回測記帳。HTTP 不接收任意 Python 或 pickle。

## 附錄 B：Phase A 本機 HTTP JSON API

### Phase B 最後一輪修正：預判綁定與文件並行控制

本節取代下列舊表格中未帶前置條件的寫入契約。

- GET `/api/graph` 與所有圖文件回應包含整數 `revision`；尚未載入時亦提供修訂號。每次成功改參數、替換圖、載入、從 sidecar 還原或存檔，修訂號遞增。layout 不改圖修訂號。
- POST `/api/nodes/<id>/params`、`/api/graph`、`/api/graph/load`、`/api/graph/save`、`/api/graph/from-sidecar` 必須帶 `expected_revision`。檢查與整筆修改共用 service 鎖；缺少或不符合目前修訂號均拒絕，無部分修改或檔案寫入。
- GET `/api/run-estimate` 新增 `revision`，與回應的圖快照一致。POST `/api/run` 必須帶使用者畫面上預判的 `expected_backtest_key` 及 `expected_revision`。後端在同一把鎖內核對修訂號及解析後的 Backtest 鍵，並以這份已解析快照啟動工作；不可核對後重新解析另一份資料版本。拒絕時不建立工作、不執行節點、不增加 N。
- 文件衝突回 HTTP 409：`{code:"graph_revision_conflict",error:"圖已被其他分頁修改，請重新載入",revision:<目前修訂號>}`。預判鍵衝突回 HTTP 409：`{code:"run_estimate_conflict",error:"執行預判已變更，請確認最新預判後再次執行",revision:<目前修訂號>}`。缺少前置條件也使用對應衝突格式；既有執行中衝突不變。
- UI 所有改圖操作附上其本地文件修訂號；衝突時清除待提交的舊編輯、重新讀取最新圖，提示「圖已被其他分頁修改，已重新載入」。run 衝突另更新預判，要求使用者重新按執行；不得自動重試或靜默採用新預判。
- CLI 與程序內初始化可使用 GraphService 的無 HTTP 前置條件流程；HTTP 寫入端點不可藉由省略欄位繞過並行控制。

### Phase B 新增契約（既有端點行為維持）

| Method / endpoint | Payload / 回應 |
|---|---|
| GET `/api/session` | `{fixture:boolean, label:string, graph_path?:string}`；UI 模式的 graph_path 為啟動時的工作區相對圖路徑。fixture 時 label 固定為 `FIXTURE 資料・獨立 ledger`，不得回傳暫存絕對路徑 |
| GET `/api/run-estimate` | `{graph_hash,backtest_key,selection_n,already_recorded,delta_n,next_selection_n}`；只釘選資料版本並以參數、實作指紋、上游 hash 計算，不執行節點、不讀取績效、不追加 ledger；缺接點／不可快取上游則 400。already_recorded=true 時 delta_n=0，否則 1；next_selection_n=selection_n+delta_n。graph_hash 為目前文件 hash，backtest_key 為本次解析資料版本的執行鍵；實際執行前資料版本或 ledger 改變時必須重新預判 |
| GET `/api/layout` | `{schema:"live-strategy-layout/1",positions:{node_id:{x:number,y:number}},path}`；對應目前 `strategies/<id>/graph.layout.json`，不存在時 positions 為空，前端依階段排版 |
| POST `/api/layout` | `{positions:{node_id:{x:number,y:number}}}` → 同 GET；有限座標、已知 node id，整筆驗證後固定鍵序與兩格縮排寫入獨立檔。不更改 graph、dirty、節點狀態、graph hash、backtest_key 或 N |

UI 由同一 GraphHTTPServer 提供 `/` 與本地 `/assets/*`，使用既有 Host／Origin 邊界；不提供任意工作區檔案。執行時 JS、CSS、字型皆來自同一 origin。
真實 UI 模式啟動時若圖驗證出現受控 GraphError，仍提供畫布頁面；前端透過既有 graph/load 端點顯示原文錯誤及非法接線索引，而非因無 layout 提前中止。第三方啟動錯誤維持固定訊息遮罩。

`graph-ui --fixture` 使用 Phase A 合成 bundle 與每次啟動獨立的系統暫存工作區（graph、layout、cache、sidecar、ledger 全隔離），禁止接受會覆寫隔離位置的 ledger/cache/root 參數。真實模式維持原 ledger。fixture 頂列常駐標記，不需 TEJ 金鑰。前端使用約 300ms 防抖，只保留最新編輯；取消舊 preview 並確認 worker 結束後才提交新參數與 preview，絕不自動 run。preview 與 run 共用既有 job API。

版面僅經 layout 端點保存；從 sidecar 載入仍用既有端點並顯示 warnings。N 預判為提示，成功執行後以 `/api/ledger` 為準。

啟動：`bash run.sh graph-api --port 9102` 或 `./run.ps1 graph-api --port 9102`。
預設及目前允許的綁定位址為 `127.0.0.1`。Phase A 不提供 HTML 畫布。
所有 POST 需 `Content-Type: application/json`，body 上限 2 MB；拒絕外部 Origin／Host。
路徑均相對啟動工作區；圖只能載入／儲存於 `strategies/<id>/graph.json`，不可越界。

| Method / endpoint | Payload / 回應 |
|---|---|
| GET `/api/node-types` | `{node_types:[{id,inputs,outputs,params,fingerprint}]}` |
| GET `/api/graph` | `{graph,graph_hash,path,dirty}`；尚未載入 graph 為 null |
| POST `/api/graph/load` | `{path:"strategies/tsmom_tx_mtx/graph.json"}` → 圖狀態 |
| POST `/api/graph` | `{graph:{schema,nodes,edges,strategy},path?:"strategies/.../graph.json"}` → 驗證後替換；非法接線整筆拒絕 |
| POST `/api/graph/save` | `{path?:"strategies/.../graph.json"}` → 固定鍵序、2 格縮排儲存，dirty=false；僅當目前圖有成功 Backtest 產出，更新 manifest.validation |
| POST `/api/nodes/<id>/params` | `{params:{target_vol:0.10}}` → 合併參數、標記受影響節點 stale；不自動回測 |
| POST `/api/preview` | `{}` → HTTP 202 `{id,status,preview,progress}`；只執行上游 |
| POST `/api/run` | `{}` → HTTP 202 job；成功 Backtest 強制 ledger 記帳與 sidecar |
| GET `/api/jobs/<id>` | `{id,status,preview,progress,sidecar?,graph_hash?,backtest_key?,message?}`；hash/key 為已提交的執行快照與 Backtest 快取鍵；status=running/complete/error/cancelled |
| POST `/api/jobs/<id>/cancel` | `{}` → HTTP 202 `{accepted:true,reason}`；回測已提交則 HTTP 409、accepted=false |
| GET `/api/nodes` | `{nodes:[{id,status,hash?,graph_hash?,message?,outputs,equity?}]}` |
| GET `/api/nodes/<id>?limit=60&offset=0` | 單節點狀態及預覽；limit 1–1000，offset 從尾端向前分頁 |
| GET `/api/ledger` | `{selection_n,expected_max_sharpe,session_n}` |
| POST `/api/graph/from-sidecar` | `{path:".graph-runs/<hash>.validation.json",graph_path?:"strategies/.../graph.json"}` → 還原圖、recorded_graph_hash、warnings |

時間序列 JSON：`{index:[ISO時間],columns:[欄名],data:[[數值]],total:總列數}`；最新列為 data 最後一列。節點 envelope 有 values 時取其可視資料，內部 as-of 歷史矩陣不傳送。Returns 額外提供累乘 equity；Report 為既有 report 欄位，Prescription 提供 slots/base/se/primary/threshold/gates、各自 facts/rule_id 與 forbids。

節點狀態：`cached` 快取、`running` 執行中、`recomputed` 已重算、`error` 錯誤、`not_ready` 未就緒、`stale` 過期。過期結果保留原 graph_hash；查詢目前圖可比較差異。執行中不接受改圖／改參數／另一個工作（409），Phase B 應在滑桿防抖後觸發 preview，避免排隊。GET 可在執行時讀進度。

取消與成功 Backtest 提交共用鎖。取消先取得鎖則不記帳；成功回測先提交則該次已完成 selection，後續取消回覆不接受，避免把已看過績效從 N 移除。GraphError 與 UnderdeterminedError 的受控訊息傳入 job.message 與 CLI，包含 prepare_graph 失敗；第三方例外不回傳原文。worker 的 console／logging 輸出依執行緒隔離，不改全域 logging disable 門檻，其他執行緒的安全錯誤仍可見。

CLI 與 HTTP 共用 GraphService；`bash run.sh graph-run --graph strategies/tsmom_tx_mtx/graph.json [--preview]`。`--ledger`／`--cache-dir` 只在啟動 CLI/server 指定，HTTP 不可調整或關閉記帳。測試一律指定暫存 ledger。成功回測自動存 `.graph-runs/<hash>.validation.json`，含 graph_hash、graph_snapshot、backtest_key 與 document_graph_hash。平常執行不更新 manifest；明確存檔目前圖且該圖已有成功 Backtest 產出時，才同步 manifest.validation.graph_hash（執行快照 hash）。

資料的 `initial`／`auto` 版本每次執行前於副本鎖定本機 bundle ingestion timestamp，納入執行 snapshot/hash、Backtest 快取鍵與 N 去重鍵。GET graph 的 graph_hash 仍代表使用者文件；執行不改動 graph、不使 dirty 改變，存檔不寫入執行期時間戳。需要固定資料版本時可從 sidecar 還原已釘選快照；舊快照的 ingestion 不存在時拒絕執行，不偷換最新資料。

### 與既有規則的兩個相容邊界

1. trial.config_ref 為 `.graph-runs/<backtest_key>.config.json`，指向首次完成該回測組態的 graph_hash 與 graph_snapshot；trial.graph_ref 亦保留完整快照，config_sha256 為該圖 hash。不同回測組態有不同參照；下游宣告改動不改參照、不增加 N。此欄位仍為 repo 相對路徑字串，沿用 triage_generated.Candidate.config_ref 的精確字串比對契約；generated manifest 候選與 graph 組態各自使用其實際候選路徑，不能混用。
2. 回測成功但零波動／樣本不足／統計未定時，R5 要求 N+1，而規則集 1.1 無法產生可重算的完整處方。此情況追加 selection、delta_n=1、status=pending 並保留未知事實，既有 audit 明確回報不可稽核。正常可判定資料的 audit 必須無問題。未修改 decision.py／規則集，亦未偽造 autocorr／n_eff。此邊界仍需未來規格／規則集明確定義；不可把 pending 宣稱為完整通過 R5 稽核。

MVP 每張圖最多一個 Backtest，避免同一圖塞入多個策略卻只計一次 N；purpose 目前僅接受 selection。未知圖 metadata 拒收，避免把無關內容嵌入 ledger／sidecar。

TSMOM 保留舊策略的有限歷史視窗：EWMA 的有效樣本亦受 lookback + skip + 5 限制。feature.ewma_vol 新增 Score 輸入，依其 window 計算一次 Sigma.values；Sigma 攜帶 source、window、values，sizing.vol_target 直接使用 values，拒絕來源或視窗不一致。改 lookback／skip 會使 Sigma 及 sizing 失效重算；節點卡片預覽即為 sizing 的實際波動值，未改成全歷史 EWMA。

### 單輪審查修正：執行接手與輕量輪詢契約

- GET `/api/session` 新增 `active_job`：無正在執行的工作時為 null；否則為目前 job 的唯讀快照，包含 `id,status,preview,progress,node_states`。不啟動、取消或改動工作。
- GET `/api/jobs/<id>` 新增 `node_states`，為 `{node_id:status}` 的精簡摘要；不含 outputs。前端每 300ms 輪詢 job，只有摘要改變或 job 結束才讀取完整 `/api/nodes`。
- UI 啟動先讀 session 與現有 graph；若 active_job 非 null，接手該 id 的輪詢、進度與取消，不重新 load graph、不提交 preview。接手工作結束後更新節點與 ledger，再啟動一次自己的 preview。
- 即使 session 與啟動 preview 之間有其他工作開始，前端也須重新取得 active_job 並接手，避免卡住或顯示執行衝突橫幅。
- HTTP error、job.message、節點 message 與取消 reason 的使用者可見文字皆提供繁體中文；未知錯誤使用固定安全中文訊息，不洩漏第三方例外原文。此規則取代上述顯示英文受控原文的描述。
- run-estimate 以鎖內取得的圖快照計算並回傳該快照的 graph_hash；資料版本解析在鎖外進行，估算與執行共用唯一節點鍵函式。

## 形式化驗證（FSM）檢視

在畫布頂列新增「形式化驗證」按鈕（開啟浮層），並提供唯讀內嵌頁 `/?view=fsm`（給 genesis-deck 桌面版的面板載入）。內容：該策略的狀態圖（來自 `strategies/<id>/fsm.ir.json`）、不變量清單（來源／確認狀態／程式碼是否實作）、各 TLC 設定的結果、白話反例與其固定三段結尾。

**資料來源與契約**

| 來源 | 內容 | 沒有時 |
|---|---|---|
| `strategy-fsm-atlas/1` 結果檔（genesis-deck 的 `scripts/formal/fsm_atlas.py` 於主機端產生；TLC 只在那裡跑） | 全部欄位：verdict、反例白話文、負控制、模型數字 | 退回 IR |
| `strategies/<id>/fsm.ir.json` | 狀態圖與規則清單；所有規則標「未檢查」，不做守衛白話化 | 顯示「還沒有資料」 |

atlas 路徑：`--fsm-atlas <path>`，其次環境變數 `GS_FSM_ATLAS`，預設 `<root>/data/strategy-fsm/strategy-fsm-atlas.json`。`--fixture` 一律只讀內建的 `fixtures/fsm-atlas.fixture.json`（手寫示範，標明非真實結果），不看任何設定的路徑。

**端點**：`GET /api/fsm`（唯讀；沒有任何寫入端點）。回應是 `strategy-fsm-view/1`：atlas 經**欄位白名單**重組（丟掉未知欄位、cfg 只留檔名、不帶主機路徑／提示詞／金鑰），並附目前載入圖對應的策略 id（`strategies/<id>/graph.json` → `<id>`）。atlas 超過 8 MB、壞檔、schema 不符都視為「沒有 atlas」，不崩潰。

**用詞硬規則**：PASS 只寫「有限模型下不變量未被違反」，不寫已證明；只有「全部是通過類」的設定才是綠色，需人審／無法判定／不認得的狀態＝警示色，負控制未被抓到＝紅色；反例缺固定三段結尾時畫面顯示警示、不假裝完整。面向使用者文字不含後端模型名／供應商／主機／GPU（測試掃整頁）。

**內嵌頁 `/?view=fsm` 的安全邊界**：只載入檢視元件，不載入畫布、不輪詢、只發 `GET /api/fsm`；server 仍只綁 127.0.0.1；靜態頁回應加 `frame-ancestors 'none'` 與 `X-Frame-Options: DENY`（桌面版用子 webview 而非 iframe，不受影響）。fixture 模式不改動 ledger／圖（e2e 驗證 ledger `selection_n` 與圖 hash 前後相同）。

**已知缺口**：atlas 的反例只有白話文字，沒有逐步轉換序列，所以畫布無法在圖上「播放」反例；要做需上游 `fsm_atlas.py` 加 `trace`。桌面版使用者的本機要有 atlas 檔才看得到 TLC 結果，否則只顯示 IR 與「未檢查」。
