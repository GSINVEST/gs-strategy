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

1. `start`：registry 最新記錄必須是 `promoted`（D1）、目前 `strategy.py`＋`config.yaml` 雜湊必須等於晉升時的雜湊、框架版本必須相符；凍結後寫入 `dry_runs`。框架版本由實際安裝的 zipline 程式碼算出（套件版本＋原始檔雜湊，不接受人工指定）；晉升時 registry 的 `framework_version` 請填 `python -m strategies._common.dryrun framework-version` 的輸出。`run` 每次也會重查 registry，最新記錄不再是此版本的 `promoted` 就暫停。
2. `run`（每交易日收盤後一次）：
   - 暫停中 → 拒絕；雜湊或框架不符 → 暫停並拒跑（不寫績效）。
   - 沒跑的交易日、資料快照落後的交易日 → 標 `MISSING`，**不補算**；連續缺 3 日 → 暫停（§9.3 的 N，D3 定為 3）。
   - 每個情境從啟動日重放到當日重建狀態，已記錄日期的權益必須與帳本一致（容差 0.01 NTD），否則暫停且不追加（`ReplayMismatch`）。**帳本只追加當日那一列**；啟動日之前沒有任何 dry run 績效。
   - 悲觀 ≤ 基準 ≤ 樂觀不成立時，當日列加 `SCENARIO_NON_MONOTONIC` 旗標。曾有缺漏的 run，其後各列加 `AFTER_GAP`。
3. `resume`：暫停後須具名的人 resume，並記下當下的最後已記錄日當浮水位，之後只計新的缺漏；雜湊與框架檢查不因 resume 放寬。

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

**限制**：3 的行程內攔截只是第二道防線，真正的出口封鎖是下面「容器隔離」的 internal 網路加白名單代理；4 的「實單側拒絕」只提供函式，實單程式是否採用不在本 repo；§10 第 9 項（桌面版／gateway 無下單端點）屬 genesis-deck。

## 未涵蓋

- D2 benchmark 包（stub 介面而已）；D4 滑價校準；D5 gateway／桌面版。
- 個股期貨（需百分比價差與成交量上限驗證後再加）。
- 排程「已提供安裝腳本但未啟用」（見下）；告警與備份見「告警與備份」一節。
- 到達價落差、下單延遲、盤後時段（bundle 只含日盤）。
- 衝擊項 eta 校準（預設關閉）。

## 操作方式

```bash
# 查框架版本字串（晉升時 registry 的 framework_version 填這個）
python -m strategies._common.dryrun framework-version
# 啟動一支已晉升的策略（需 registry 最新記錄為 promoted，且內容雜湊相符）
python -m strategies._common.dryrun start --strategy-id vgrsi_tx --start-date 2026-11-03
# 跑最近一個已收盤的交易日（也可 --session YYYY-MM-DD）
python -m strategies._common.dryrun run --strategy-id vgrsi_tx
# 暫停後恢復（具名；之後只計新的缺漏）
python -m strategies._common.dryrun resume --strategy-id vgrsi_tx --by <人> --note <原因>
```

正式運行一律走容器（下節），不要在有券商憑證的使用者環境直接跑。`run` 預設的目標日是「台北時間最近一個已收盤（13:45 後）的交易日」，所以資料落後時會標 `MISSING`，不會悄悄重跑舊日。

## 容器隔離（§10 第 3 項）

檔案在 `dockerfile/dryrun/`：

| 檔 | 作用 |
|---|---|
| `Dockerfile` | python 3.11；依賴鎖在 `requirements.lock`（來自已驗證可跑的環境）；zipline-tej 以具名 build context 傳入固定 commit；非 root；不含任何金鑰 |
| `compose.yml` | `dryrun` 只掛在 `internal: true` 的 `dry_internal` 網路（沒有任何直連外網的路）；唯一出口是同網路上的 `egress-proxy`；唯讀 rootfs、`cap_drop: ALL`、`no-new-privileges`；掛載只有 repo（ro）、bundle（ro）、帳本目錄（rw bind mount，主機路徑由 `DRYRUN_LEDGER_DIR` 指定，預設 `~/.local/share/gs-dryrun`，容器內 `/dryrun-ledger`）；容器以主機使用者的 uid 執行才讀得到權限 700 的 bundle |
| `Dockerfile.proxy`、`squid.conf` | 白名單代理：只放行 CONNECT 443 到 `api.tej.com.tw`，其餘 `deny all`。要加網域必須改這個檔並走 PR |
| `egress_probe.py`、`verify-egress.sh` | 容器內探針：直連 1.1.1.1／8.8.8.8／TEJ／券商網域必須失敗；經代理到 TEJ 通、到 example.com 與券商網域被拒；無券商憑證、無 `/mnt/c`、非 root；`/app`、`/zipline/data` 唯讀、只有 `/dryrun-ledger` 可寫 |

建置與驗證：

```bash
git -C <zipline-tej 倉庫> archive <commit> | tar -x -C /tmp/zl      # 乾淨原始碼（無 .git）
docker build -f dockerfile/dryrun/Dockerfile --build-context zl=/tmp/zl -t gs-dryrun:local .
bash dockerfile/dryrun/verify-egress.sh                              # 實跑封鎖驗證
DRYRUN_DOCKER_TESTS=1 pytest tests/test_dryrun_container.py          # 同上，包成測試
```

`TEJAPI_KEY`／`TEJAPI_BASE` 只放在 `DRYRUN_ENV_FILE`（只含這兩個變數的檔，權限 600），由 compose 的 `env_file` 注入，不進 image、不進 repo。

已實跑驗證（本機 WSL docker，2026-10-30）：`verify-egress.sh` 17/17；容器內以合成的 promoted 記錄（僅測試用、在暫存 repo 副本）走 `start`→`run`×2，三情境皆 `OK`，帳本只有 `mode=DRY`，rootfs 與 `/zipline/data` 唯讀（`/zipline` 為 tmpfs，因 zipline 啟動會在 ZIPLINE_ROOT 建 `extension.py`）。**未驗證**：真實 promoted 策略、排程實際觸發（尚未 apply）、長期運行。

## 告警與備份

由 `scripts/dryrun_run_daily.sh` 在每次跑完後於**主機**執行 `strategies/_common/dryrun/ops.py`（標準函式庫、唯讀開帳本、不連網；不進容器，因為容器沒有出口）：

- `ops backup`：用 sqlite 線上備份 API 複製帳本到 `DRYRUN_BACKUP_DIR`（預設 `~/.local/share/gs-dryrun-backup`），驗 `integrity_check` 與四張表列數一致才算成功，保留最近 14 份（只輪替 `dry_ledger-*.sqlite`，不碰其他檔）。
- `ops health`：每支策略檢查 ①已暫停＝嚴重 ②連續缺漏 1–2 日＝警告、≥3 日＝嚴重 ③最後紀錄距今超過 5 個日曆日＝嚴重（排程沒在跑）；帳本不存在＝嚴重；帳本有但沒任何 run＝警告。結束碼 0／1／2。
- 有警告以上：追加到 `DRYRUN_ALERT_LOG`（預設 `~/.local/state/gs-dryrun/alerts.log`），並把訊息從 stdin 餵給環境變數 `DRYRUN_ALERT_CMD`（例如你的 Slack／mail 小工具；通知指令失敗只印 stderr，不影響檢查）。
- 已實跑（WSL、系統 python3.12）：備份成功並產出檔案、缺漏 1 日回 WARN 並寫入 alert log、帳本不存在回 CRIT；單元測試 11 項。**未驗證**：`DRYRUN_ALERT_CMD` 接真實通知通道、cron 下的環境變數。

**接通知通道**：把 `scripts/dryrun_notify_slack.example.sh` 複製到 `~/.config/gs-dryrun/notify`（權限 700），webhook URL 另存 `~/.config/gs-dryrun/slack_webhook`（權限 600）。`dryrun_run_daily.sh` 偵測到 `notify` 可執行就自動設成 `DRYRUN_ALERT_CMD`；也可自備任何從 stdin 讀訊息的指令。範例腳本只驗過語法，**沒有實際對 Slack 送過**。

## 快照與推送（給桌面版 dry run 面板）

設計見 genesis-deck 的 `docs/superpowers/specs/2026-10-30-dryrun-panel-design.md`。`dryrun_run_daily.sh` 在健康檢查後於**主機**執行：

- `snapshot`（`strategies/_common/dryrun/snapshot.py`）：唯讀開帳本，輸出 `strategy-live-snapshot/1` JSON：每支策略的狀態、最後交易日、連續缺漏、三情境權益序列、累計成本分項，加上告警清單。**不含**成交明細、委託、策略程式碼、帳本路徑。契約範例在 `tests/fixtures/strategy-live-snapshot.example.json`，測試保證它就是產生器現在的輸出（genesis-deck 的 gateway 測試會複製同一份）。
- **機密分類（fail closed）**：策略 `manifest.yaml` 的 `tags` 含 `forge` 或 `mint`（`gs-forge`、`gs-mint` 的策略研究）→ `classification` 為 `["forge"]`／`["mint"]`；沒有則 `["open"]`；manifest 讀不到、沒有 `tags` 欄或 strategy_id 形狀不合 → `["unknown"]`，下游一律當機密，並在告警加一條「請補上 tags」。**這兩個 repo 產出的策略必須帶對應 tag**，否則會被當成一般策略。
- `push`（`push.py`）：以 `PUT` 加 `Authorization: Bearer` 推到 gateway。網址與金鑰存在 `~/.config/gs-dryrun/push_url`、`push_key`（權限 600；金鑰是 gateway 上帶 `strategy-live:push` scope 的專用 key，用 `keyctl.sh` 發，**不要貼到對話或 repo**）。任一檔不存在 → 只印警告、結束碼 0；被拒或網路錯誤 → 結束碼 2。輸出不含金鑰、網址與回應內容。
- 已實跑（WSL 系統 python3.12）：產生快照、推送到本機假伺服器（Bearer 與內容正確）、未設定時略過。**未驗證**：真實 gateway 端點（屬 genesis-deck 的 P2，尚未存在）、`push_key` 的實際發放、cron 下的完整流程。

## 排程

```bash
scripts/dryrun_schedule.sh install --repo <dry run 專用乾淨 checkout> [--env-file F] [--zipline-data DIR]   # 只印出，不生效
scripts/dryrun_schedule.sh install --repo <...> --apply                                                      # 才真的寫入 crontab
scripts/dryrun_schedule.sh remove --apply
```

週二至週六 07:30（WSL 時區須為 Asia/Taipei）。**不是收盤後立刻跑**：bundle 由 Windows 工作排程 `gs-zipline-tej-bundle-daily` 在每天 06:00 更新，當天收盤的資料要到隔天早上才進 bundle，收盤後跑只會把當天標成 `MISSING`（且 `MISSING` 不補算，見流程）。入口是 `scripts/dryrun_run_daily.sh`，在容器內逐策略執行；沒有 promoted 記錄的策略會失敗並記錄，不影響其他策略。

## 已知缺口

- **還沒有任何策略被晉升**（`log/promotions.jsonl` 不存在），所以排程啟用後每支策略都會因 `NotPromoted` 失敗，直到人完成 D1 的 screened → promote。
- 網路層隔離靠 `internal` 網路，不是代理；代理只負責「唯一的出口」只放行 TEJ。Docker `internal` 網路的效力取決於 docker 主機設定，已在本機 WSL docker 實跑一次，其他主機需重跑 `verify-egress.sh`。
- Windows 主機的 `C:/Users/User/.shioaji` 存在券商憑證。容器沒有掛載它，但**不要**把 `DRYRUN_REPO`／`ZIPLINE_DATA` 指到含該路徑的目錄，也不要在容器外（WSL 直接）跑 dry run。
- 告警只有「寫檔＋可選的外部指令」，沒有內建通知通道：沒設 `DRYRUN_ALERT_CMD` 就只會寫 `alerts.log`，沒人看就等於沒有告警。備份只複製到同一台主機的另一個目錄，主機整台壞掉仍會失去帳本與備份；刪帳本檔的風險（trigger 防不了）靠備份降低，不是消除。
- 容器內 `api.tej.com.tw` 是目前已知唯一必要主機；若 zipline-tej 日後新增其他主機，連線會失敗，需更新 `squid.conf`。
