# dry run 執行器（D3，影子模式）

規格來源：genesis-deck `docs/superpowers/specs/2026-10-30-strategy-live-dryrun-design.md` §6.2、§6.4、§9.3、§10、§14（D3）、§15。
成本模型依據：[`docs/experiments/2026-10-slippage-volatility-volume-share.md`](../experiments/2026-10-slippage-volatility-volume-share.md)（#53、#56）。

**影子模式**：只跑、只寫 DRY 帳本。不接 UI、不下任何單、不碰券商 API。範圍只有 `vgrsi_tx`、`tsmom_tx_mtx`、`cubic_momentum_tx`（日頻、TX／MTX）；`xsmom_stkfut_rmt`（個股期貨）不納入，呼叫會被 `OutOfScope` 拒絕。

## 模組（`strategies/_common/dryrun/`）

| 檔 | 內容 |
|---|---|
| `costs.py` | L1 成本模型，純函式。價差項為主（完整價差預設 6 點、每邊 `spread/2`、基準價＝收盤價）、成交量上限（預設 5%）、NaN／缺值明確處理；衝擊項選配，eta 必須明確給且不得等於 zipline 預設 0.049 |
| `zl_adapter.py` | 在不改 `futures_setup.py` 與策略檔下換上 L1 模型的 zipline 轉接層；`ZiplineSimRunner` |
| `executor.py` | 每日執行、凍結檢查、缺漏標記、重放對帳、暫停／恢復 |
| `ledger.py` | DRY 帳本（獨立 SQLite、`mode='DRY'` CHECK、trigger 擋 UPDATE／DELETE） |
| `guards.py` | §10 第 1–5、8 項：憑證自檢、import 攔截、網路出口、唯讀開啟、命名與 live 設定檔自檢 |
| `types.py` | `SimulatedOrder`（與實單 `Order` 不同型別、無轉換函式）、生命週期（無 live 狀態） |
| `benchmark.py` | D2 介面 stub：D2 前一律回 `None`，帳本寫 `benchmark_status='D2_UNAVAILABLE'` |

## L1 成本與三情境

| 情境 | 完整價差 | 手續費 |
|---|---|---|
| optimistic | 2 點 | ×1 |
| base | 6 點（預設） | ×1 |
| pessimistic | 12 點（基準成本 ×2） | ×2 |

情境分析用的 2／4／6 點檔位另列在 `SENSITIVITY_SPREADS`（不是三條帳本曲線）。

`decide_fill` 的拒絕原因（寫進帳本 `dry_fills.reason`）：`NAN_PRICE`、`NAN_VOLUME`、`ZERO_VOLUME`、`VOLUME_CAP_ZERO`；部分成交 `PARTIAL_VOLUME_CAP`；衝擊項啟用且 σ／ADV 缺值時以 7.5 bp 保守成本成交並標 `NO_DATA_FALLBACK`。zipline 在成交量為 0／NaN、價格 NaN 時會靜默跳過，轉接層改為把拒絕記下來。

## 流程

1. `start`：registry 最新記錄必須是 `promoted`（D1）、目前 `strategy.py`＋`config.yaml` 雜湊必須等於晉升時的雜湊、框架版本必須相符；凍結後寫入 `dry_runs`。
2. `run`（每交易日收盤後一次）：
   - 暫停中 → 拒絕；雜湊或框架不符 → 暫停並拒跑（不寫績效）。
   - 沒跑的交易日、資料快照落後的交易日 → 標 `MISSING`，**不補算**；連續缺 3 日 → 暫停（§9.3 的 N，D3 定為 3）。
   - 每個情境從啟動日重放到當日重建狀態，已記錄日期的權益必須與帳本一致（容差 0.01 NTD），否則暫停且不追加（`ReplayMismatch`）。**帳本只追加當日那一列**；啟動日之前沒有任何 dry run 績效。
   - 悲觀 ≤ 基準 ≤ 樂觀不成立時，當日列加 `SCENARIO_NON_MONOTONIC` 旗標。曾有缺漏的 run，其後各列加 `AFTER_GAP`。
3. `resume`：暫停後須具名的人 resume；雜湊與框架檢查不因 resume 放寬。

## 與規格 §10 的對照

| # | 實作 | 測試 |
|---|---|---|
| 1 | `check_no_credentials`（環境變數名稱、家目錄憑證路徑；錯誤訊息不含值） | `test_dryrun_guards.py` |
| 2 | `scan_forbidden_imports`（AST，含 `__import__`／`import_module` 字面值）＋`ImportGuard`（`sys.meta_path`，已載入則 fail closed） | 同上 |
| 3 | `EgressGuard`（行程內 `getaddrinfo`／`connect` 白名單，預設只 loopback 與 `api.tej.com.tw`） | 同上 |
| 4 | `mode='DRY'` CHECK、獨立檔案、`assert_no_dry_rows`（供實單側呼叫） | 同上、`test_dryrun_executor.py` |
| 5 | `open_readonly`（`mode=ro` ＋ `query_only`）；套件內無任何下單類名稱 | 同上 |
| 6 | `SimulatedOrder` 凍結 dataclass、無 `Order`、無 `to_*`／`as_*` | 同上 |
| 7 | `LIFECYCLE_TRANSITIONS` 無 live | 同上 |
| 8 | `check_run_layout`（名稱含 `dry`、目錄內不得有 live／broker／`.pfx` 設定檔） | 同上 |

**限制**：3 的行程內攔截只是第二道防線，容器網路策略才是真正的出口封鎖，本 PR 沒有提供；4 的「實單側拒絕」只提供函式，實單程式是否採用不在本 repo；§10 第 9 項（桌面版／gateway 無下單端點）屬 genesis-deck。

## 未涵蓋

- D2 benchmark 包（stub 介面而已）；D4 滑價校準；D5 gateway／桌面版。
- 個股期貨（需百分比價差與成交量上限驗證後再加）。
- 容器層網路隔離、排程（cron／systemd）設定。
- 到達價落差、下單延遲、盤後時段（bundle 只含日盤）。
- 衝擊項 eta 校準（預設關閉）。
