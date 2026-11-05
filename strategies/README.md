# Taiwan Futures Strategies (Zipline-TEJ × TQuant-Lab)

從 `data/papers.db` 87 篇論文/報告中篩出 4 支可在台灣期貨市場執行的策略，
直接寫成 TQuant-Lab `tquant_future` bundle 可跑的回測程式碼。

## 策略一覽

| 目錄 | 對應論文 | 標的 | 訊號類型 | 期望年化 (paper原作) |
|---|---|---|---|---|
| `vgrsi_tx/` | arxiv 2605.01300 (Visibility Graphs RSI) | TX (台指期連續月) | 技術指標：可見圖 RSI | Sharpe 2.55-3.6 (DJI/EUR/XAU) |
| `cubic_momentum_tx/` | arxiv 2605.00854 (Periodic Bubbles) | TX | 三次方動量 + Hawkes 過熱 | 模型示意 |
| `tsmom_tx_mtx/` | wiley fut.70093 (Curve Momentum in China) + 業界共識 | TX/MTX | 12-1 月時序動量 + 波動目標 | Moskowitz et al. 經典 |
| `xsmom_stkfut_rmt/` | arxiv 2604.19107 (RMT Complexity Gap) + 個股期動量 | 個股期 universe | 橫斷面動量 + RMT regime overlay | regime-aware 變體 |

## 執行流程

回測環境與爬蟲共用 repo，但用獨立 venv（`.venv-bt`）避免 zipline-tej 與
crawler 的 numpy/pandas pin 衝突。

```bash
# 1. 建立回測 venv (一次性)
./scripts/setup-bt.sh                # 預設用 python3.11，可傳參覆寫

# 2. 設定 TEJ API key (一次性)
cp .env.example .env                  # 然後編輯 .env 填入真的 key
# 注：scripts/ingest_futures.sh 與 scripts/run_strategy.sh 會自動 source .env

# 3. ingest 期貨資料 (一次性 / 定期更新)
./scripts/ingest_futures.sh           # 預設 future="TX MTX"，可用 FUTURES_ROOTS 覆寫

# 4. 跑單一策略
./scripts/run_strategy.sh vgrsi_tx
# 或指定輸出路徑:
./scripts/run_strategy.sh tsmom_tx_mtx /tmp/tsmom.pkl
```

### ⚠️ TEJAPI_KEY 是 import-time 必要條件

`zipline-tej` 依賴的 `exchange_calendars.exchange_calendar_tejxtai` 模組
在 **import 時**就會打 TEJ API 取得最新交易日。沒設 `TEJAPI_KEY`，連
`import zipline` 都會丟 `AuthenticationError`。

也就是說：**任何呼叫到 runner.py / zipline 的指令，shell 內必須先有
`TEJAPI_KEY`**，不只 `zipline ingest` 而已。

### Calendar / Benchmark 預設

- **Calendar** 由 `runner.py` 預設為 `TEJ_morning_future`（`tquant_future`
  bundle 註冊的 calendar）。各策略的 `config.yaml` 不再設定 `calendar:`，
  除非要對接其他 bundle。
- **Benchmark** 預設不設定（zipline 採 zero-returns）。想對標 IR0001 時，
  必須同時 ingest `tquant` equity bundle 並在 config.yaml 加 `benchmark: IR0001`。

每個子目錄都有獨立 README 解釋假設、變數、限制。

## Dashboard import 規格相容 (2026-05-26)

4 個 bundle 同時符合 `~/gs-zipline-tej/docs/strategy-import-spec.md` v1，
可以直接被 gs-zipline-tej dashboard 載入（不需要改任何 strategy.py）。

每個 bundle 內容：

```
strategies/<id>/
├── manifest.yaml     ← spec v1 metadata (id, name, params, source, ...)
├── config.yaml       ← legacy (跑 strategies/_common/runner.py 用)
├── strategy.py       ← initialize(context) + handle_data(context, data)
├── futures_setup.py  ← 共用 helpers (從 _common/ 複製，bundle-local)
└── README.md
```

啟動 dashboard 並指向本 repo：

```bash
DASHBOARD_STRATEGY_DIRS=$HOME/gs-strategy/strategies \
PATH="$HOME/gs-strategy/.venv-bt/bin:$PATH" \
  ~/gs-zipline-tej/.venv-dashboard/bin/python -m dashboard.app
```

### Validator

任何 bundle 是否合規：

```bash
./scripts/validate_dashboard_bundle.py strategies/vgrsi_tx
# OK  vgrsi_tx
```

CI 用：失敗 bundle 數為 exit code。

### 選填欄位 `zkperf_id`（zk-perf 登記）

`manifest.yaml` 可選填 `zkperf_id`（格式 `^[a-z0-9][a-z0-9_-]{2,63}$`），
用來指向 zk-perf 的登記項目；不填就沒有，既有策略皆未設定。

- validator 只檢查格式，格式錯誤會回報明確錯誤（不會崩潰）。
- 策略面板的 `/api/strategies` 會多出 `zkperf_id` 與 `zkperf_registered`，
  有設定時列表顯示中性徽章「已登記 zkperf」，未設定則不顯示任何東西。
- **`zkperf_registered` 只代表「manifest 宣告了 id」，不是 proof 狀態。**
  gs-strategy 不驗證 proof、不連網；驗證與結果由 genesis-deck gateway 負責。

### 把舊式 config.yaml 轉成 manifest.yaml

```bash
./scripts/config_to_manifest.py strategies/new_bundle   # 單一
./scripts/config_to_manifest.py --all                   # 掃整個 strategies/
./scripts/config_to_manifest.py --dry-run --all         # 預覽不寫檔
./scripts/config_to_manifest.py --force --all           # 覆寫人工編輯
```

預設 idempotent：手動編輯過的欄位（如打磨過的 `name` / `description`）會被保留，
只補齊缺少的欄位。`--force` 才會全部重建。

未來爬蟲產生器（gs-scraper / quant_crawler 的 strategy-gen pipeline）應直接
呼叫此 script 並傳 `--generated`，自動填 `source.kind=generated` 與 paper
provenance。

設計細節與決策記錄在 [`docs/progress-strategy-import-spec.md`](../docs/progress-strategy-import-spec.md)。

### 策略分類標籤 (taxonomy)

每個 bundle 的 `manifest.tags` 採用統一的多維度詞彙，dashboard import 後
直接進 `StrategyMeta.tags`，search panel（`main.js` 的 fuzzy haystack）即可
模糊搜尋。維度：

| 維度 | 範例 tag |
|---|---|
| `family` | momentum / trend-following / mean-reversion / pairs-trading / carry / value / factor / volatility / event-driven / arbitrage |
| `signal` | technical / fundamental / cross-sectional / time-series / regime-aware / machine-learning / sentiment / microstructure / graph-based |
| `direction` | long-only / long-short / market-neutral |
| `instrument` | index-future / stock-future / single-stock |

固定執行情境 tag：`taiwan`、`futures`、（自動產生的再加）`paper`、
`auto-generated`、`needs-review`。

自動產生的 bundle 由 `quant_crawler/strategy_gen/taxonomy.py` 從論文標題/摘要
多標籤抽取；手寫 4 支 bundle 用同一詞彙手填。完整詞彙表：

```bash
.venv/bin/python -m quant_crawler.strategy_gen --list-tags
```

搜尋範例（dashboard search panel 輸入）：`market-neutral`、`regime`、
`cross-sectional momentum`（多 token AND）。設計記錄見
[`docs/progress-strategy-taxonomy.md`](../docs/progress-strategy-taxonomy.md)。
