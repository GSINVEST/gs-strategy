# Live Strategy Graph — Phase B 驗收

日期：2026-09-29。分支：`dev/live-strategy-graph-spec`。範圍為規格任務 7 與必要的新增 API；未改策略訊號、成交、成本或統計判準，未 push。

## 任務狀態

- [x] M5：先固定附錄 B 的 session／run-estimate／layout 介面契約。
- [x] M6：新增後端端點、同源靜態服務及隔離 fixture，補 pytest。
- [x] M7：React／React Flow／Vite 編輯器、離線字型、型別接點與節點卡片。
- [x] R3／R4：300ms preview 防抖及取代舊工作，明確執行、進度、取消、舊結果過期。
- [x] R5／R6：常駐 N 儀表與預判，Report／Facts／五槽處方及錯誤原文。
- [x] R7：dirty、存檔、獨立 layout、sidecar 還原與指紋警告。
- [x] 啟動器：來源指紋判斷 build 是否過期，Windows／Git Bash 指令、README、忽略產物。
- [x] M8：完整後端回歸、前端單元測試、真 server e2e、雙啟動指令與截圖驗收。

## 實測

所有 fixture 使用真 Phase A 合成期貨 bundle、真 Zipline 訂單／成本／損益事件迴圈。每次 UI 啟動使用不同系統暫存工作區，正式 ledger 不變；測試不 mock API。

| 項目 | 指令 | 結果 |
|---|---|---|
| 完整後端回歸 | `$env:PYTHONUTF8='1'; .venv-bt/Scripts/python.exe -m pytest tests/ -q -rs` | 444 passed、7 skipped；88.83 秒。原 432 項保留，新增 12 項全過 |
| 前端單元測試 | UI 目錄執行 `npm test` | 7 passed |
| 正式 build | UI 目錄執行 `npm run build -- --logLevel warn` | 成功 |
| 真 server e2e | UI 目錄執行 `npm run test:e2e` | 8 passed；約 1.3 分鐘，全部無外部請求 |
| Git Bash fixture 啟動 | `bash run.sh graph-ui --fixture --port 19104` | 過期 build 指紋觸發 npm ci＋build；印出 `http://127.0.0.1:19104` |
| PowerShell fixture 啟動 | `./run.ps1 graph-ui --fixture --port 19105` | 跳過最新 build；印出 `http://127.0.0.1:19105` |
| 首頁 | `curl.exe -s -o NUL -w 'HTTP %{http_code}' http://127.0.0.1:19104/` | HTTP 200；19105 同樣 HTTP 200。測試服務已停止 |

e2e 覆蓋：

1. 真拖曳 Sigma → Score，顯示型別錯誤，接線與 graph hash 不變。
2. 真拖動 lookback 滑桿，上游更新、沒有 POST run、N 不變。
3. 新組態 N+1，Report／Facts／Prescription 有實際內容；第二次回放 N 不變。
4. 真回測執行中取消，狀態過期、N 不變。
5. 真拖動節點，layout 有改變，dirty／hash／預判均不變。
6. 改參數但不執行，Backtest 及下游保留舊結果並標過期。
7. sidecar 還原精確參數與快照 hash；抽屜圖表超過單頁資料、有 tooltip、原始表 offset 分頁。
8. 事實不足時處方卡顯示原文補救訊息，不顯示舊處方；Report 不受影響、N 不增加。

所有情境逐一檢查瀏覽器請求只指向 `http://127.0.0.1:19102`，並檢查 CSP／未捕捉錯誤。完成狀態另斷言 13 張卡片皆完整位於 1280×800 視窗、無重疊，N 邊界標籤可見。

驗收截圖：系統暫存目錄 `live-strategy-graph-fixture-1280x800.png`，不入版控。

## 風險與複核

Pre-mortem 的三項風險及證據：

- N 預判與實際資料版本不一致：新增 pytest 比對預判鍵與真回測鍵，測下游參數不影響鍵、缺輸入與不可快取上游拒絕預判。
- preview／載入／執行競態：單元測試覆蓋防抖、取消順序與取代；UI 同步提交鎖防止載入／執行期間參數混入；preview 尚未回 job ID 時等待 ID 再取消。
- fixture 污染正式資料：graph、layout、bundle、cache、sidecar、ledger 全放不同暫存 root，禁止 root／ledger／cache 覆寫，並測取消不記帳與啟動失敗清理。

獨立唯讀複核與主 agent 的共識：Backtest 鍵公式和既有引擎一致，layout 與圖身份分離。複核指出的啟動失敗遮罩／清理、執行與 sidecar 提交窗口、舊取消覆寫新 job、preview 啟動窗口均已修正；沒有未解決的審查分歧。

初始圖非法接線另以真 HTTP 測試確認：UI 仍啟動，graph/load 回覆 400 與原文接線索引。第三方例外仍遮罩。機密變更掃描僅輸出布林判準，結果 PASS；正式 ledger 沒有版控差異。

量化檢查界線：本次是既有策略的工程介面，不是新 alpha／filter，不宣稱 fixture 的績效有交易意義。訊號時序、PnL、成本及資料對齊沿用 Phase A，以原有前視、真 Zipline 等價、overnight／intraday 損益一致性測試防回歸。

## 檔案清單

- 規格與紀錄：`docs/spec/live-strategy-graph.md`、本檔。
- 後端：`strategies/_common/graph/{__main__.py,api.py,ledger.py,service.py,fixture.py}`。
- 前端：`strategies/_common/graph/ui/{package.json,package-lock.json,index.html,vite.config.js,src/main.jsx,src/model.js,src/model.test.js,src/style.css}`。
- 版面：`strategies/tsmom_tx_mtx/graph.layout.json`；初始空座標啟用動態排版，拖曳後存入具體座標。
- 測試：`tests/test_live_graph_phase_b.py`、`tests/e2e/{playwright.config.cjs,graph.spec.cjs}`。
- 整合：`scripts/build_graph_ui.py`、`run.sh`、`run.ps1`、`.gitignore`、`README.md`。

## 限制

既有依賴外部套件／授權 bundle 的測試依原條件跳過，沒有刪減或弱化測試。Phase A 對「成功回測但統計不足」的 pending 稽核邊界不變，詳見附錄 B。

Phase B 範圍內沒有未完成項目。本機是 Windows，已實測 Git Bash 與 PowerShell；沒有原生 Linux 執行環境可供實測。

首次 npm 安裝與瀏覽器安裝需要套件來源；完成 build 後的 UI 執行不需外網。schema 未給 maximum 的數字參數，其滑桿視窗由 minimum／default 推導並可擴張，數字輸入不另加人為上限。完整來源事實、warnings 與 forbids 可在卡片捲動區／檢視抽屜閱讀。

## 對抗式審查與 Phase B 單輪修正（2026-09-30）

起點為 `5c9a995`，分支 `dev/live-strategy-graph-spec`。M9 先固定附錄 B 契約；後端與前端在不重疊檔案集合並行，最後由主 agent 串行整合 e2e 與驗收。未修改 decision.py、規則集、strategy.py；未 push。

### 修正與失敗測試證據

後端新增測試均位於 `tests/test_live_graph_review.py`；前端單元測試位於 `strategies/_common/graph/ui/src/model.test.js`。

| 項目 | 修正 | 測試與實測證據 |
|---|---|---|
| A1 | 私有 import namespace 直接編譯原始碼，不沿用 sys.modules 或 pyc；原 strategy 唯一本地 import futures_setup 同樣新鮮載入。執行前後檢查指紋，提交前再檢查快照全部指紋 | `test_a1_fresh_cost_helper_and_fingerprint`：先失敗 `[6,6]`，修後 `[6,12]`；`test_a1_implementation_change_during_execution_cannot_commit`：改檔後拒絕提交，N=0且不寫該快取 |
| A2 | 配對實際 ingestion 後使用其 isoformat，文件保留原參數、執行副本正規化 | `test_a2_equivalent_ingestions_count_once`：先有3鍵，修後三字串只有1鍵、N=1 |
| A3 | 最大lag為 min(max(21,floor(10·log10 n)),floor(n/5))；檢查短階、21階及最大階，任一顯著即yes；ACF用同最大階 | `test_a3_monthly_dependence_uses_non_iid_and_full_provenance`：lag-21資料先no，修後yes且非iid；保存 tested_lags、p_values、decision_p_value。保留舊短階p_value並明示p_value_lag |
| A4 | invalidate保留舊state欄位，僅改過期標記與result_hash | `test_a4_stale_returns_keep_original_graph_hash`：原先缺graph_hash，修後與舊Returns來源一致 |
| B1 | session.active_job供重新整理接手；完成後才啟動自己的preview，409競態重新讀session；HTTP／job／node／CLI錯誤安全繁中 | `test_b1_b4_active_job_snapshot_and_safe_chinese`、`test_b1_http_and_node_messages_are_safe_chinese`；前端3個B1單測涵蓋接手與409競態；e2e `refresh during active preview adopts the job and recovers automatically` 原版60秒無取消鈕，修後自動恢復且無錯誤橫幅／API錯誤、N=0 |
| B2 | core.node_cache_key是Engine與estimate唯一鍵公式 | `test_b2_estimate_and_ledger_share_key_function`：監測兩路都呼叫共用函式，估算鍵等於實際ledger的backtest_key |
| B3 | 初次量測後只自動fit一次，明確「全圖」按鈕才再次fit | `B3 fits once after measurement and ignores later selection or dimensions`；既有sidecar e2e新增點選卡片開抽屜前後viewport不變斷言 |
| B4 | job附精簡node_states，300ms輪詢；狀態摘要改變或完成才抓nodes；同job共用輪詢Promise | `B4 polls at 300ms and refreshes nodes only for changed states or completion`：3次job、2次nodes、兩次300ms間隔；後端B1/B4測試驗摘要 |
| B5 | 鎖內深複製圖與取得文件hash，prepare_graph及後續解析移到鎖外 | `test_b5_estimate_releases_lock_and_keeps_snapshot`：先因編輯阻塞失敗，修後可同時編輯且估算仍對應原快照 |
| B6 | edge id由來源／目標節點與接點組成 | `B6 preserves remaining edge identities after deletion`：刪前邊不改後邊id |
| B7 | 成本使用既有scalar樣式顯示中文單位，固定TX、MTX優先及其餘symbol順序 | `B7 displays cost units without raw JSON`、`B7 displays TX then MTX regardless of API key order, then sorts other symbols`；reload e2e另斷言實際卡片文字 |

後端首批7個反例先失敗後通過，另補繁中與執行中改檔反例，合計9個新增pytest。前端首批6個反例先失敗，另補409完成競態與反向成本欄位順序，合計8個新增單測。整合首輪原8個e2e均過；新情境接手成功，但成本欄位順序斷言失敗，保留斷言並修正呈現順序後重跑。

### 複核與界線

Pre-mortem：舊程式污染新鍵、鎖外解析混用不同圖、接手工作與preview競態。獨立複核先從私有載入／正規化／圖快照形成判斷，再核對實作。反例檢查追加了執行中改檔拒絕提交、409工作已結束、反向JSON鍵順序。沒有未解決的審查分歧。

既有測試僅依新增session欄位及繁中錯誤契約更新精確期望；統計原LB10數值斷言仍保留。全圖包含性檢查改成先按「全圖」再驗13張卡無重疊，符合B3；另外保留點選卡片不得改viewport的新斷言。未刪除或弱化既有測試，沒有回滾項目。

本次不是新alpha或filter；不改訊號、成交與成本公式。合成fixture驗證工程與R8等價性，不據此宣稱實盤績效。既有外部資料測試跳過條件及pending統計稽核邊界維持。

### 本輪最終驗收

| 實際執行指令 | 最終結果 |
|---|---|
| `$env:PYTHONUTF8='1'; .venv-bt/Scripts/python.exe -m pytest tests/ -q -rs` | **453 passed、7 skipped、147 warnings；92.94秒**。含R8真Zipline等價；原444項保留 |
| UI目錄 `npm test` | **15 passed**；原7項保留，新增8項 |
| UI目錄 `npm run build -- --logLevel warn` | 成功 |
| UI目錄 `npm run test:e2e` | **9 passed；1.4分鐘**；原8項保留，新增執行中reload情境，該項24.3秒 |
| `git diff --check` | 通過 |
| 原策略／規則／正式ledger差異檢查 | 無變更 |
| 機密模式掃描 | PASS；僅輸出布林判準 |

pytest警告包含依賴套件棄用、fixture bundle重註冊與零波動統計運算；未隱藏警告，外部依賴及授權資料的7項skip條件未變。完整結果無失敗，沒有回滾項目。所有e2e仍檢查無外部請求及未捕捉瀏覽器錯誤。

里程碑：M9固定契約、M10後端修正、M11前端修正、M12整合e2e與驗收紀錄。各commit皆使用繁體中文與指定Codex trailer；不push。

## Phase B 最後一輪對抗式修正（2026-09-30）

起點 `d8b60fa`。本輪僅處理 C1–C4；審查意見不納入。先於附錄 B 固定 `expected_revision`、`expected_backtest_key` 及具名繁中 409 契約，再以不重疊檔案集合並行後端與前端，最後串行整合兩個 e2e 情境。

### 重現、修正與驗證

後端三項重現先實跑 **3 failed**；C4 兩個渲染情境先實跑 **2 failed、15 passed**，確認原版問題後才修正。

| 項目 | 修正及測試名稱 | 實測結果 |
|---|---|---|
| C1 | `/run` 在 service 鎖內比對畫面預判的修訂號與 Backtest 鍵，以核對後的同一份已解析快照執行；UI 不再丟棄新預判並直接執行。`test_seen_estimate_conflict_does_not_add_selection`、`test_run_key_conflict_creates_no_job`、`test_run_executes_once_prepared_checked_snapshot` | 舊預判回 409，N 維持 1、不新增 job；缺鍵與錯鍵同樣拒絕，正常提交只解析一次 |
| C2 | `import_zipline_offline` 在程序內注入輔助日曆，完全移除共用套件 CSV 的寫入／還原。`test_two_fixture_processes_never_write_shared_calendar` | 兩個程序依閘門交錯進出；兩者等待中及結束後，共用快取均與原內容一致 |
| C3 | 五個改圖端點皆核對修訂號；文件讀取及提交共用鎖。UI 保留待提交編輯的原始修訂基線，衝突清除佇列、重新載入並顯示指定提示。`test_stale_edge_delete_preserves_new_parameters`、`test_every_graph_mutation_rejects_invalid_revision_atomically`、`C3 clears queued stale edits while recovering a conflict` | 舊刪線回 409、value=2 與原接線保留；缺少／過期／非整數修訂號均不改文件或存檔 |
| C4 | 名稱、狀態、座標及接點查詢使用自有屬性；待提交編輯、缺輸入傳播與自動座標使用無原型字典。`C4 renders node and drawer for reserved id %s`、`C4 propagates missing inputs through reserved node ids without inherited state` | `__proto__`、`constructor` 均可渲染卡片及抽屜；缺輸入訊息正常傳播 |

新增後端測試位於 `tests/test_live_graph_conflicts.py`，共 **27 passed**。前端新增測試位於 `src/model.test.js` 與 `src/render.test.js`；`C1/C3 never retries %s` 另確認兩種具名衝突都不自動重試。

新增真實雙分頁 e2e（不 mock API）：

- `another tab changes parameters then stale run returns 409 and re-estimates without N`：畫面先顯示「N 不變」，另一分頁改參數後，舊分頁只送一次原預判；回 409、載入新參數及新預判，N 不增加、無活動工作。
- `stale tab deletes an edge and reloads after 409 while preserving new parameters`：另一分頁先改參數，舊分頁以鍵盤實際選取並刪線；送出舊修訂號、回 409，最新參數與接線保留。

### 範圍及複核

主端複核特別檢查預判核對後不得再次解析、409 不得進自動重試、背景 refresh 不得替待提交舊編輯洗掉修訂基線，以及所有寫入端點不能以省略欄位繞過。原 HTTP 測試只補新契約的前置條件，既有斷言保留。

整合首輪 e2e 的舊預判情境通過；刪線情境指出受控畫布未保存接線選取狀態，導致尚未送出刪線請求。補上 `onEdgesChange` 的選取狀態保存，衝突時清除選取；保留實際鍵盤選取、刪除及 409 的全部斷言後重跑。

本輪未修改 `decision.py`、規則集或 `strategy.py` 的行為，亦未修改正式 ledger。fixture 仍使用真 Zipline 訂單、成本與損益事件迴圈；合成資料不代表交易績效。未 push。

### 最終驗收結果

| 實跑指令／檢查 | 結果 |
|---|---|
| `$env:PYTHONUTF8='1'; .venv-bt/Scripts/python.exe -m pytest tests/ -q -rs` | **480 passed、7 skipped、147 warnings；119.43 秒**。原 453 項保留，新增 27 項 |
| UI 目錄 `npm test` | **21 passed**。原 15 項保留，新增 6 項；整合修改後重跑通過 |
| UI 目錄 `npm run build -- --logLevel warn` | 通過 |
| UI 目錄 `npm run test:e2e` | **11 passed；1.8 分鐘**。原 9 項保留，新增 2 項 |
| 共用套件日曆驗收前後雜湊比較 | 相同；不顯示內容 |
| 策略／規則集／正式 ledger 版控差異 | 無變更 |
| `git diff --check` | 通過 |
| 變更檔案的已知環境機密值及私鑰模式掃描 | PASS；僅輸出布林判準 |

既有 7 項外部依賴／授權資料 skip 條件未改，依賴棄用及統計邊界警告未隱藏。全部必要驗收通過，**無回滾項目**。M13 固定契約、M14 修正後端與日曆隔離、M15 修正前端衝突及渲染、M16 補齊雙分頁 e2e 與本紀錄；提交皆使用繁體中文與指定 Codex trailer，不 push。

### 附：形式化驗證（FSM）檢視驗收

| 檢查 | 結果 |
|---|---|
| `python -m pytest tests/test_live_graph_fsm.py -q` | 9 passed（白名單、主機路徑、壞檔／缺檔、IR 退路、唯讀、Host 檢查、fixture 隔離、路徑逃逸） |
| UI `npm test` | 35 passed（原 21 項保留，新增 14 項） |
| 真 server `--fixture`＋Chromium `graph-fsm.spec.cjs` | 5 passed（F1–F5：端點、浮層內容與用詞、視窗內不溢出、內嵌頁唯讀、fixture 不動 ledger／圖） |
| 既有 `graph.spec.cjs` | 11 passed（無回歸） |
