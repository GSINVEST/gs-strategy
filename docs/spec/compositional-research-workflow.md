# 組合式量化研究 workflow（PoC）規格

> 狀態：PoC 已實作（2026-10-02）；疊在 [`live-strategy-graph.md`](live-strategy-graph.md) 之上
> 首個落地對象：`strategies/llm_view_tx`（QUANTDATA + 研究 RAG → 可熱抽換 LLM → walk-forward）

---

## 0. 目標與決策

**目標**：讓量化研究流程像 ComfyUI workflow 一樣可以組裝，但要更 meta、更 no-code，並帶有 applied category theory
的 compositionality。具體來說要做到四件事：

1. 資料源可以接 QUANTDATA（市場資料庫）與 RAG（研究知識庫）。兩者是獨立的 data source。
2. LLM 換代很快，模型必須可以即時插拔。
3. 要能一眼看清整條鏈：用了哪些資料 → 模型怎麼推論（CoT）→ 給出什麼決策 → 與歷史資料比較的績效，以及 walk-forward replay。
4. 從架構上擋住 LLM 的 look-ahead bias，不靠研究者自律。

**選型決策（更新 2026-10-02 的 `/survey-first` 結論）**：

| 部件 | 原建議 | 實際採用 | 理由 |
|---|---|---|---|
| 畫布 | PoC 用 ComfyUI custom nodes | **沿用 Live Strategy Graph**（React Flow + stdlib 引擎） | kevin-2 上的 `dev/live-strategy-graph-spec` 已完成型別化接點、hash 增量快取、圖即 JSON、N 記帳與 sidecar 重現，比 ComfyUI（GPL、接點型別只比字串）更合本案 |
| 型別／組合 | 自建薄層，參考 DisCoPy | **自建** `compose.types` + `compose.diagram` | 只需參數化型別與 `>>`／`@`；引入 DisCoPy 不划算 |
| 執行 | Hamilton | **沿用既有引擎** | 引擎已是單一真相（快取、取消、記帳鎖），不做雙重 DAG |
| LLM | vLLM + pydantic-ai | **OpenAI 相容 HTTP（stdlib）+ 模型登錄表** | Genesis Deck gateway 已是 vLLM；PoC 不需要另一層 SDK |
| Trace | MLflow | **呼叫快取 + 圖產出內嵌 trace** | 每筆呼叫以請求內容定址、可重播；MLflow 留給後續 |
| Walk-forward | skfolio 切窗 | **自寫 anchored/rolling 切窗 + 樣本內選擇** | 只需要不重疊的測試窗與 embargo，40 行就夠 |
| 回放 | Rerun | **React 內建回放面板** | 和畫布同一個介面，不用再開一個 viewer |

---

## 1. 範疇論的對應

| 範疇論 | 本系統 |
|---|---|
| 物件（object） | 參數化接點型別：`PriceBars`、`MarketView`、`Docs`、`Signals[llm]`、`Returns`… |
| 態射（morphism） | 節點類型：`dom → cod`，例如 `agent.llm_view : MarketView ⊗ Docs → Signals[llm]` |
| 合成 `∘` | `f >> g`：`g` 的自由輸入依**接點名稱**接到 `f` 的輸出，型別必須相容，否則在**組合時**就失敗 |
| 張量 `⊗` | `f @ g`：兩張圖的不交併 |
| 單位元 | `Diagram.id()` |
| 對角（複製） | 輸出可以接到多個輸入（cartesian），所以 `f` 的輸出在 `f >> g` 之後仍可用 |
| 子型別 | `Signals[llm]` 可以接到要求 `Signals` 或 `Signals[*]` 的輸入；反方向不行 |
| 高階運算子 | `backtest.walk_forward`：吃一族策略 `policy(θ)`，對每個 fold 只用樣本內選 θ，輸出一條樣本外報酬 |

定律由測試保證（`tests/test_compose_types_diagram.py`），檢查方式都是比對圖 hash：

- 結合律：`((a>>f)>>g)>>h` 與 `a>>(f>>(g>>h))` 產生相同的圖 hash。
- 單位律：`id>>a>>f` 與 `a>>f>>id` 相同。
- 張量對稱：`a>>(f@side)` 與 `a>>(side@f)` 相同。
- `strategies/llm_view_tx/graph.json` **由組合式產生**（`python -m strategies.llm_view_tx.compose_graph`），測試斷言檔案內容等於下面這個表達式：

```python
research    = data >> view >> docs >> agent
evaluation  = research >> backtest
statistics  = ledger >> (report @ facts) >> resolve
pipeline    = evaluation >> probe >> statistics >> montecarlo
```

只用名稱配對、不做「唯一相容型別」的猜測配對，正是結合律成立的原因。

---

## 2. 節點（`strategies/llm_view_tx`）

| 節點類型 | 輸入 | 輸出 | 重點 |
|---|---|---|---|
| `data.quantdata_futures` | — | `PriceBars` | `qd.futures_continuous`（QUANTDATA REST）。報酬用 `roi`，是同一合約的結算價對結算價，換月不跳空。`data_version` 以資料內容 hash 釘選，內容一變就拒絕執行 |
| `feature.market_view` | `PriceBars` | `MarketView` | 每個決策日（日／週／月）只用 ≤ d 的列計算特徵 |
| `rag.research_context` | `MarketView` | `Docs` | 後端可選 `papers`（papers.db）、`gs_rag`、`fixture`、`none`。每個決策日只取**決策日前 lag 天已公開**的文件 |
| `agent.llm_view` | `MarketView`, `Docs` | `Signals[llm]` | 依模型登錄表呼叫，擷取 CoT，解析 JSON 決策；知識截止前的決策日不呼叫 |
| `backtest.walk_forward` | `Signals`, `PriceBars` | `Returns`, `Positions`, `WalkForward` | 巢狀選擇的 walk-forward，只在乾淨區間運作 |
| `pit.memorization_probe` | `PriceBars` | `ProbeReport` | 記憶探測：要求模型憑記憶回答歷史結算價 |
| `ledger.*`／`validation.*`／`stat.*` | — | — | 沿用 Live Strategy Graph，不另寫判準 |

---

## 3. 防 look-ahead bias（核心約束）

文獻共識（Lopez-Lira et al. 2025，arXiv 2504.14765）：在模型知識截止日之前，
「預測能力」與「記憶」無法區分；在 prompt 裡要求「忽略某日之後的資訊」**沒有效果**。所以這裡全部做成機械行為：

1. **模型 cutoff 登錄**（`compose/models.yaml`）。每個模型都要寫明 `cutoff`，也就是知識截止日的**上界**，並寫 `cutoff_basis` 說明依據。
   不知道真實 cutoff 時，就用發布日或部署日：這樣只會讓乾淨樣本變少，不會讓污染資料混進績效。
2. **乾淨起點**：`clean_start = cutoff + buffer_days + 1` 個營業日。比它早的決策日：
   - 預設**不呼叫模型**；
   - 開啟 `include_contaminated` 可以呼叫來檢視，但訊號一律清成 NaN，**永遠不進 `Returns`**；
   - 回放畫面以斜線標示「已污染・不計績效」。
3. **乾淨樣本不足就失敗**：乾淨區間扣掉第一個樣本內窗後，樣本外天數若小於 `min_clean_oos_days`，回測直接報錯、不記帳。
   例：`qwen3.8-27b` 的 cutoff 上界是 2026-10-01，目前沒有任何乾淨樣本，所以系統拒絕產生績效。
4. **資料時點**：
   - 行情：日期為 d 的列視為在 d 收盤時已知。
   - 文件：已知時間 = `max(published, updated)`，因為存下來的內容是最新修訂版；日期無法解析時一律排除（fail closed）。
   - `gs_rag` 後端只有年份，所以保守地視為次年 1 月 1 日才已知。
5. **匿名化**（預設開啟）：prompt 不放日期與標的名稱，只放標準化特徵，降低模型「認出這段行情」的機會。這是第二道防線，不取代第 2 點。
6. **記憶探測**：在 cutoff 前後各抽 n 個日期，要求模型**必須**憑記憶給出結算價（拒答會把記憶藏起來，所以不接受拒答）。
   若事前誤差明顯小於事後，就標記為疑似記憶。
7. **前視測試**：把 t 之後的價格竄改掉，t 以前的所有 prompt 與訊號必須完全不變（`test_signals_are_point_in_time`）。

中文限制：目前公開的 point-in-time 模型（ChronoGPT、DatedGPT 等）只有英文。台股這條線只能依靠上面的 2、3、5、6 四點。

---

## 4. 模型熱抽換

- 換模型只要改一個參數：`agent.llm_view.model`（UI 下拉選單），圖與接線都不動。新增模型只要在 `models.yaml` 加一筆。
- `prepare_graph` 會把**所選那一筆**登錄內容的 hash 釘進 `model_spec`。
  所以修改某一筆會讓用到它的組態變成新試驗；新增其他模型則不影響既有組態的 N。
- 呼叫走 OpenAI `/chat/completions`。CoT 的來源依序為：
  1. vLLM reasoning parser 回傳的 `reasoning` 或 `reasoning_content`；
  2. 文字裡的 `<think>…</think>`，包含 chat template 已經開頭、回答裡只出現結尾 `</think>` 的情況；
  3. non-thinking 模型則用回答 JSON 裡的 `analysis` 欄位。
  
  trace 會記錄 `reasoning_source` 註明來源。
- 每筆呼叫以「模型、API 模型名、base_url、訊息、溫度、max_tokens」定址，快取在 `.cache/llm-calls/`。
  重跑與回放不會再查詢模型。API key 只從環境變數或金鑰檔讀取，不會寫進快取、log 或錯誤訊息。

---

## 5. Walk-forward 與 N 記帳

- 切窗方式：anchored（樣本內從頭累積）或 rolling。測試窗互不重疊；樣本內與樣本外之間有 `embargo_days`。
- 每個 fold 只用**樣本內** Sharpe 從 `thresholds` 網格選出信心門檻，再套用到下一個樣本外窗。
  竄改樣本外報酬不會改變該 fold 的選擇（有測試）。
- 整個巢狀程序在 ledger 上算**一次** selection trial。網格本身是回測組態的一部分，改網格就是新試驗（N+1）。
- 回測引擎是研究等級的向量模擬器（單一期貨根、收盤決策、次日報酬、手續費＋滑價、口數取整），**不宣稱與 Zipline 等價**。
  要和 Zipline 對帳，屬於後續工作。

---

## 6. 回放

頂列「回放」按鈕（圖中有 `agent.llm_view` 才會出現）會讀取 `GET /api/replay`。

- **時間軸**：價格、策略權益（OOS）對照買進持有、多空部位、污染區間、fold 起點。點圖或拖滑桿可以跳到任一決策日。
- **決策卡**：模型看到的特徵、檢索到的研究（含已知日期）、CoT、完整 prompt、原始回答、這個決策在下一個決策日之前的 OOS 報酬。
- **fold 表**：各門檻的樣本內 Sharpe、選中的門檻、樣本外 Sharpe 與報酬。

---

## 7. 執行

```bash
# 離線示範：合成行情、內建研究摘要、確定性假模型，使用獨立的暫存 ledger
python -m strategies._common.graph ui --fixture --graph strategies/llm_view_tx/graph.json --port 9112

# 真實資料：QUANTDATA + papers.db + Genesis Deck；會寫入 log/trials.jsonl
python -m strategies._common.graph run --graph strategies/llm_view_tx/graph.json
python -m strategies._common.graph ui  --graph strategies/llm_view_tx/graph.json

# 由組合式重新產生圖檔（--check 只檢查是否一致）
python -m strategies.llm_view_tx.compose_graph
```

真實模式需要：
- QUANTDATA REST（`qd.py`，WSL 內會自動改用 IP 加 Host header）；
- `data/papers.db`；
- LLM 金鑰（環境變數 `GS_LLM_API_KEY`，或 `models.yaml` 列出的金鑰檔）。

Genesis Deck 只放行公司出口 IP。

---

## 8. 已知限制與後續

- 回測引擎不是 Zipline，尚未對帳。
- 只支援單一期貨根；權重為「方向 × 曝險」，沒有波動目標化。
- 子圖目前無法摺疊成一個複合節點。範疇上這就是把一張 diagram 收成一個 box，屬於下一步。
- MLflow／OTel trace 匯出尚未實作；目前的 trace 存在呼叫快取與節點產出裡。
- `gs_rag` 後端的時點只到年份，太保守，需要 gs-rag 在 metadata 補上發布日期。
- 記憶探測是啟發式判斷，不是檢定；樣本數小（預設每側 8 個日期）。

---

## 9. 實測結果（2026-10-02，真實資料）

**設定**：

- 資料：QUANTDATA TX 連續月，2024-01-02 ~ 2026-10-01。
- RAG：papers.db，共 585 篇；每個決策日最多取 3 篇。
- 決策：每週一次，提示經過匿名化。
- walk-forward：anchored，樣本內 60 日／樣本外 21 日，門檻網格 {0, 0.6, 0.75}。
- 成本：每口 250 元，加 2 點滑價。

| 項目 | qwen3-235b-2507（cutoff 上界 2025-07-31） | qwen3.8-27b（cutoff 上界 2026-10-01） |
|---|---|---|
| 乾淨起點 | 2025-08-08 | 2026-10-09（尚未到來） |
| 呼叫數 | 60 個決策日，全數解析成功；cutoff 前的 70 個決策日未呼叫 | 0（所有決策日都在 cutoff 之前） |
| 結果 | OOS 2025-11-07 → 2026-10-01，共 218 日 | **回測拒絕執行**：乾淨樣本外天數不足（0 < 60），N 不變 |
| OOS Sharpe | 1.63（買進持有 2.58） | — |
| PSR／DSR | 0.93／0.84（N=2） | — |
| MDD／CAGR | −11.4%／46.2% | — |
| 方向分布 | 做多 46、空手 13、做空 1 | — |
| 記憶探測 | 事前誤差中位數 11.6%，事後 48.4%；未達疑似門檻 | 事前 23.1%；沒有事後樣本 |

**解讀**：

- 乾淨區間內台指期大漲（約 2.3 萬 → 4.9 萬點）。LLM 大多判斷做多，但有 13 次空手、1 次做空，**OOS 沒有贏過買進持有**。落後的原因推測是空手期間錯過漲幅，還沒有逐筆歸因。
- N=2 的 DSR 是 0.84，沒有任何證據顯示這個策略有 alpha。
- 這次 PoC 驗證的是**基礎設施**，不是策略：
  - 資料與模型在 PIT 規則下串起來了；
  - 模型可以一個參數熱抽換；
  - 污染區間不會混進績效；
  - 乾淨樣本不足時系統會拒絕產生數字；
  - 每次試驗都誠實地記進 N。
- 記憶探測：235B 對 2024 年部分日期的估計誤差只有 2–4%（例如 2024-05-15 估 20850，實際 21243）。但它的估值多半集中在 1.6 萬～2.2 萬附近，事後誤差又被大漲行情放大。
  「事前比事後準」可能只是價格漂移造成的，不一定代表記憶。需要更好的基準（例如以 cutoff 時的價格作為 naive guess），列為後續工作。
- 235B 是 non-thinking 模型，CoT 取自回答中的 `analysis` 欄位（`reasoning_source = analysis`）。模型會把提示裡的欄位說明照抄成開頭，屬於提示措辭的小問題。
