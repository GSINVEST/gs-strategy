# 策略池切換與回測結果庫（三期計畫・第一期）

> 狀態：三期皆已實作（2026-10-02）
> 相關：[`live-strategy-graph.md`](live-strategy-graph.md)、[`compositional-research-workflow.md`](compositional-research-workflow.md)

## 三期範圍

| 期 | 內容 | 狀態 |
|---|---|---|
| 一 | dashboard 策略下拉選單（策略池＝gs-zipline-tej 註冊表）＋ 每次回測自動存入結果庫 | **本文件** |
| 二 | 蒙地卡羅與多次試驗驗證：每次試驗都存，另存彙總績效分布；接入因子池與 MINT trial ledger | **已實作（見下）** |
| 三 | 量化論文爬蟲排程 → LLM 依論文原文產生策略程式碼 → 通過驗證門檻才放進策略池 | **已實作（見下）** |

## 介面原則

- **單一真相**：策略池就是 gs-zipline-tej 的 `dashboard.strategies.registry`。它掃描所有含 `manifest.yaml` 的 bundle，格式依 `docs/strategy-import-spec.md` v1.2，來源包括：
  - gs-zipline-tej 內建範例；
  - 本 repo 的 `strategies/`；
  - `DASHBOARD_STRATEGY_DIRS` 指定的其他目錄；
  - 經 FORGE／MINT 匯出器產生的 bundle。
- **只當介面，不改程式**：gs-zipline-tej、GSINVEST/gs-FORGE、GSINVEST/gs-MINT 都不修改。本 repo 只呼叫它們的公開函式（`registry.refresh / list_strategies / list_factors`、`runner.run_backtest`），而且一律在 `.venv-bt` 子程序裡執行。圖伺服器本身不 import zipline，外部策略崩潰也不會拖垮它。
- 子程序的輸出可能含金鑰，所以失敗時只回固定的中文訊息，不轉述原文。

## 切換規則

1. 策略自帶 `strategies/<id>/graph.json` 時，直接開那張圖，例如 `tsmom_tx_mtx`、`llm_view_tx`。
2. 其他策略開**粗圖** `strategies/_pool/<id>/graph.json`（自動產生，不進版控）：

```
data.pool_strategy ──StrategyBundle──► feature.strategy_spec ──StrategySpec──► backtest.pool_zipline ──Returns──► report / facts / resolve
        └──────────────────────────────StrategyBundle──────────────────────────┘                                ledger ─┘
```

   - `bundle_version` 是策略原始碼（`.py/.yaml/.md/.json`，不含產生出來的 graph.json）的內容 hash，由 `prepare_graph` 釘選。改一行策略碼就是新的回測組態，N+1。
   - `feature.strategy_spec` 呈現 manifest 宣告的 universe、參數、驗證產物與來源，讓 02／03 欄在拆成細節點之前也有東西可看。
   - `backtest.pool_zipline` 透過 gs-zipline-tej runner 跑真實 Zipline，報酬讀自 runner 輸出的 equity parquet。
3. fixture（測試資料）模式不可切換，因為切換會觸發真實資料回測，違反 fixture 的隔離承諾。

## 結果庫（`data/backtests.sqlite`）

- 每一次**非預覽**的執行都寫一筆 `runs`，包含被 PIT 守門拒絕或回測失敗的執行。欄位有：狀態、是否新增 selection trial、執行後的 N、模型與引擎、Sharpe／PSR／DSR／MDD／CAGR、各節點狀態、參數快照。
- `returns` 以 `backtest_key` 為鍵，每個組態只存一次。快取重播會多一筆 run，但不會重複寫報酬。
- `details` 存 walk-forward fold 表、runner 資訊與驗證報告。
- ledger（`log/trials.jsonl`）仍是 N 的唯一來源；結果庫只負責回答「每次執行產生了什麼」。
- 選用 stdlib SQLite（WAL），不新增依賴。需要分析時，可以用 DuckDB `ATTACH`。
- API：`GET /api/strategies[?refresh=1]`、`POST /api/strategies/select {id, expected_revision}`、`GET /api/results[?strategy=&limit=]`、`GET /api/results/<run_id>`。
- UI：頂列有策略下拉選單（依來源分組，因子池第二期才開放）；「結果」面板列出執行紀錄，點選可看權益曲線、驗證警告與參數快照。

## 實測（2026-10-02）

- 策略池讀到 19 支策略與 4 個因子，沒有匯入錯誤。
- 在 UI 切換到 `buy_and_hold_tw` 後按執行：真實 Zipline 回測 226 天，Sharpe 0.67；自動寫入結果庫並記為新試驗 +1。

## 第二期：蒙地卡羅與多次試驗

- **`validation.monte_carlo` 節點**（屬於驗證區，在回測之後執行）：
  - 對回測報酬做 stationary block bootstrap（Politis–Romano 1994），沿用 `validation.reality_check` 既有的重抽索引。
  - 預設 2000 條路徑、平均區塊長度 10，可以調整。
  - 輸出 Sharpe／MDD／CAGR 的 5–95% 分位、P(Sharpe ≤ 0)、P(期末虧損)，以及權益扇形圖。
  - 它只重抽同一條報酬序列，**不會產生新的績效資訊，所以不增加 N**。新增這個節點不會改變 backtest_key，既有組態會直接快取重播。
  - `llm_view_tx`（由組合式產生）與所有粗圖都已經接上這個節點。
- **結果庫新增 `experiments`／`trials` 兩張表**：
  - 每條蒙地卡羅路徑是一個 trial，存它的 sharpe／mdd／cagr；彙總結果存在 `experiments.summary`。
  - 同一個回測用不同的路徑數、區塊長度或 seed，算不同的實驗。
- **gs-MINT 錦標賽**：`python -m strategies._common.mint_ledger import <trial_ledger.jsonl>`
  - 只依 gs-MINT `docs/contracts/trial-ledger-fields.md` 讀取，不 import 也不修改 gs-MINT。
  - 只把 `event_type == trial` 當成交易 trial；`criteria_change_trial` 計入 DSR 試驗數（`n_for_dsr`）。
  - deep trial 的 id 裡有冒號，讀取時保留原樣。
  - 本機有 `returns_path` 對應的月報酬檔時，重建 month × trial 矩陣並計算 PBO；沒有就明示無法計算。
  - 匯入是冪等的：同一個 run 重新匯入會覆寫。
  - 用 gs-MINT 官方範例 ledger 實測：5 個 trial ＋ 1 次準則變更，n_for_dsr = 6，冠軍為 `…coarse_006:D10:VW`。
- **因子池**：可以從選單選取，開啟的粗圖以 `backtest.pool_factor` 呼叫 gs-zipline-tej 的 `run_factor_backtest`，把因子當選股濾網回測。
  - 濾網參數（mode／direction／n／weighting／rebalance）是節點參數；改參數就是新的試驗。
  - 實測 `forge_mom6_top200_w`：481 天，Sharpe 1.43。
- **粗圖會依最新範本重新產生**，同時保留使用者存過的參數，所以舊策略也會出現新節點。
- **UI**：「結果」面板新增「實驗」分頁（蒙地卡羅分布表＋扇形圖、MINT 彙總＋前 50 名 trial）；蒙地卡羅節點卡片顯示 Sharpe 分布摘要。
- **刻意不做**：重複參數試驗，也就是自動跑參數網格。依 `live-strategy-graph.md` §5，那屬於 diagnostic 模式，會改變 N 的記帳規則，必須連同 `stat-ruleset-1.2` 一起修訂，不能只在程式裡實作。

## 第三期：論文 → LLM 寫策略 → 門檻 → 策略池

`python -m quant_crawler.strategy_gen.llm_codegen --limit 3 [--model qwen3-235b-2507] [--dry-run]`

每天排程跑一次，是 `scripts/daily_refresh.sh` 的 step 5。這一步失敗也不會讓整個 daily_refresh 失敗；可以用 `CODEGEN_LIMIT`、`CODEGEN_MODEL` 調整篇數和模型。

1. **選論文**：從 papers.db 挑出有明確策略線索的論文（`classify_kind` 的 strategy_score 必須大於 0）。已經嘗試過的論文，不論成敗都不再重試，嘗試紀錄存在結果庫的 codegen 實驗裡。
2. **取原文**：用 RAG 取出與交易規則最相關的段落；沒有全文索引時只用摘要。
3. **產生策略**：交給 `models.yaml` 中的模型，產出符合 strategy-import-spec 的 `strategy.py` 與參數，並附上「論文規則對應到台股的方式」與參數理由。提示裡會列出本機 bundle 實際有的標的，透過 gs-zipline-tej 的公開函式 `list_bundle_symbols` 取得。
4. **三道門檻**：
   - **G0 靜態檢查**：
     - 只能 import zipline.api／numpy／pandas／math；
     - 不能使用 open、eval、exec、存取雙底線屬性等；
     - 使用的標的必須存在於本機 bundle；
     - 必須通過 gs-zipline-tej 自己的 `validate_strategy_py`。
   - **G1 煙霧測試**：在 2023-01～06 用 gs-zipline-tej runner 跑真實 Zipline，必須能跑完。失敗時只記錄例外類別名稱，因為 stderr 可能含有金鑰。
   - **G2 交易行為**：至少要有 2 筆交易，而且至少減碼一次，排除買進持有的翻版。這一關只看下單行為、不看報酬，所以屬於初篩，ΔN = 0。
5. **入池**：通過的策略放到 `strategies/_llm_generated/<id>/`（不進版控）。manifest 會帶上 `llm-generated`、`unreviewed` 標籤，以及論文來源、模型、各門檻結果；README 寫明對應方式與參數理由。策略池會自動掃到，dashboard 選單會把它們分到「論文自動生成（未審）」。之後在 dashboard 按「執行」，才會計入一次 selection trial。
6. **紀錄**：每一批都寫成結果庫的 `codegen` 實驗，每篇論文記成一個 trial，內容包括到達哪一道門檻、原因、交易數與減碼數。可以在「結果 → 實驗」查看。

**實測（2026-10-02，qwen3-235b-2507）**：

- 第一輪：3 篇全部卡在 G1，原因是模型選了本機 bundle 沒有的 0050。之後在提示裡列出可用標的，並在 G0 加上標的檢查。
- 第二輪：3 篇中有 2 篇入池（交易 2／106 筆，減碼 1／50 次）；1 篇仍然寫了 0050，在 G0 被擋下。

**注意**：模型產生的策略只是論文規則的**近似實作**。例如有一篇的描述提到 LightGBM，但程式碼裡不可能用到它。所以入池的策略一律標為「未審」，需要人工審閱後才能當作研究結論。
