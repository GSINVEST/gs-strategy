# dry run 觀察層（observation tier）

目的：D1 閘門三支策略全 blocked（DSR 0.91～0.94 < 0.95），promoted 路徑的執行器一律 `NotPromoted`，桌面版「策略」分頁是空的。觀察層讓這些策略仍有**前向紙上模擬**可看，**不碰閘門、不碰門檻、不碰 registry**。

依據：[`dryrun-executor.md`](dryrun-executor.md)、genesis-deck `2026-10-30-strategy-live-dryrun-design.md`（§4.1 生命週期、§10 隔離）。

## 不變量（測試逐條鎖住：`tests/test_dryrun_observe.py`）

1. **觀察不是晉升**：`observe.py` 只 import 唯讀的 `registry.latest`，沒有 `promote`／`record_screening`，全程不寫 registry（測試比對 registry 檔案位元組不變）。`promote()` 對 blocked 照舊拒絕，`rules.py` 門檻、`is_promoted` 語意零改動。
2. **資格**：registry 該策略**最新事件**必須是 `screened` 且 verdict ∈ {`blocked`, `conditional`}。最新是 `promoted`（該走 promoted 路徑）、`eligible`（該去晉升）、或沒有記錄，一律 `NotObservable`。範圍同 promoted（只收三支日頻 TX／MTX，個股期貨 `OutOfScope`）。
3. **分離的生命週期**：`types.OBSERVATION_STATES`／`OBSERVATION_TRANSITIONS`（observing ⇄ paused → retired），沒有任何通往 promoted／dryrun_active／live 的邊。
4. **帳本分離**：同一份 DRY 帳本，`dry_runs.tier ∈ {promoted, observing}`（CHECK）。`DryLedger.find_run` **預設只回 promoted**，所以 `DryRunExecutor` 看不到 observing 的 run；`ObservationExecutor` 反之。舊帳本啟動時 `ALTER TABLE` 補 `tier`（既有列＝promoted）。
5. **持續資格**：每日重查。最新事件不再是**同內容**的 blocked／conditional screened（被晉升、變 eligible、策略改版）→ 暫停 `NO_LONGER_OBSERVING`。**同內容 blocked 重篩不影響**繼續跑，D1 失敗原因隨最新一次篩選更新。
6. **同一模擬器、同一 L1 成本、三情境、缺漏不補算、重放對帳、凍結雜湊**：直接繼承 `DryRunExecutor`（只覆寫資格檢查與 tier）。框架版本以啟動當下凍結（不要求等於篩選時的字串），之後每日比對。
7. **隔離照舊**：`selfcheck`（憑證、import、出口、唯讀、命名）每個入口都跑，沒有任何下單／券商通路。

## 歷史回補（樣本內，非前向）

觀察起算日之前沒有前向資料。`observe-backfill --hist-start D` 以同一個 L1 三情境模擬器重跑 `[D, 起算日前一交易日]`，寫進**獨立的** `dry_history` 表（`basis='IN_SAMPLE_BACKFILL'`，append-only trigger，一個 run 只能回補一次）。

- 這段資料就是 D1 篩選與參數選擇看過的樣本，**不是前向績效**。資金從 `capital_base` 重新起算，**不與前向曲線相連**，快照裡也是獨立欄位。
- 未寫入 `dry_daily`，所以不影響缺漏、對帳、暫停等任何前向邏輯。

## CLI

```bash
python -m strategies._common.dryrun observe-start    --strategy-id tsmom_tx_mtx --start-date 2026-11-03
python -m strategies._common.dryrun observe-backfill --strategy-id tsmom_tx_mtx --hist-start 2020-01-02
python -m strategies._common.dryrun observe-run      --strategy-id tsmom_tx_mtx
python -m strategies._common.dryrun observe-resume   --strategy-id tsmom_tx_mtx --by <人> --note <原因>
```

`scripts/dryrun_run_daily.sh`：設 `DRYRUN_OBSERVE=1` 才會多跑 `observe-run` 並以 `--with-observing` 產快照；**預設關閉，這個 PR 不排程、不改 `GS_PROMOTERS`**。

## 快照 schema（向後相容）

genesis-deck gateway 的 `strategylive.validate` 對欄位**逐一嚴格比對**，多一個欄位就 400，且只收 `schema == "strategy-live-snapshot/1"`。所以：

- **預設輸出 `/1`，與觀察層出現前逐欄位相同**，observing 的 run 一律略過 → 現行 gateway 不受影響（測試：欄位集合不變）。
- `--with-observing` 輸出 **`/2`** = `/1` 全部欄位 + 每個 run 多四個欄位。契約範例 `tests/fixtures/strategy-live-snapshot-v2.example.json`（測試保證等於產生器輸出）。

| 新欄位（僅 /2） | promoted | observing |
|---|---|---|
| `tier` | `"promoted"` | `"observing"` |
| `label` | `null` | `"未通過 D1／僅供觀察"` |
| `d1` | `null` | `{verdict, reasons[], pending[], screened_at, rules_version}`；`reasons` 例：`"DSR 0.941 < 0.95"（向零截斷到三位，不四捨五入）`，`pending`＝尚未完成（unavailable／provisional）的檢查，不算失敗 |
| `history` | `null` | `null` 或 `{basis:"in_sample_backfill", label:"樣本內回補（非前向）", start, end, equity:{optimistic,base,pessimistic}}` |

`equity` 永遠只是前向；歷史只在 `history`。

## 桌面版／gateway 需要跟著改（本 PR 不做）

見 PR 說明的清單。

## 未涵蓋／風險

- registry 目前沒有 screened 記錄（`log/promotions.jsonl` 在 PR #67，未 merge），所以真實環境要等 #67 進 main 後才能 `observe-start`。
- 回補需要 bundle 涵蓋 `--hist-start`；zipline 重跑一個長區間的耗時未量測。
- 觀察層績效好**不能**當作晉升理由；面板必須把 `label` 顯示出來。

## 外部觀察層 run（多檔／舊單檔相容）

研究端提供、不在本帳本內的觀察層 run，在每日快照附加。建議將轉換完成的 GDR 與其他研究端來源檔案放到 git 工作樹外的 `extra.d/`：

```bash
export DRYRUN_EXTRA_RUNS_DIR="$HOME/.local/share/gs-dryrun/extra.d"
python -m strategies._common.dryrun.snapshot --ledger "$DRYRUN_LEDGER_DIR/dry_ledger.sqlite" \
  --strategies-dir strategies --extra-runs-dir "$DRYRUN_EXTRA_RUNS_DIR" --out snapshot.json
```

- `--extra-runs-dir` 依檔名排序讀取 `*.json`，每檔同時驗證 `runs` 與 `alerts`；一檔失敗只跳過該檔，其他檔與帳本 run 照常輸出。全部失敗仍產生帳本快照。
- `strategy_id` 不得與帳本、檔內或已接受檔案重複；排序較後的衝突檔整檔略過，該檔其他 id 也不會被占用。
- 僅收完整 observing run，classification 只能含 `forge`、`mint`、`unknown`。巢狀 `equity`、`cost_cum`、`d1`、`history` 與可選 regime 切片依 gateway 欄位及大小限制驗證。快照隱含觀察層，使用 regime 切片時升為 `/3`。
- `alerts` 可省略（舊檔相容），有提供時必須是陣列，單筆鍵恰為 `strategy_id`、`level`、`message`；id 必須屬同檔 run，等級只收 `WARN`／`CRIT`，訊息最多 300 字。無效 alert 使整檔略過。
- 帳本告警優先保留，外部告警依檔名與陣列順序附加；總數超過 200 時先截斷外部告警並 WARN；帳本本身已超限時也截尾至 200 並 WARN（限外部附加路徑）。累計 run 超過 100 或序列化大小超過 2 MiB 時，略過造成超限的整檔並 WARN。
- 舊 `--extra-runs <檔>` 與每日腳本 `DRYRUN_EXTRA_RUNS=<檔>` 繼續支援。單檔驗證失敗時 CLI 回傳 2，由每日腳本退回不帶外部 run 的快照。舊單檔的 alerts 現在也會附加。
- 同時設定資料夾與單檔時資料夾優先，忽略單檔並輸出一行 WARN。資料夾不存在或無法讀取也只告警，保留帳本快照。
- 為避免把未核定內容寫進 log，逐檔 WARN 以排序序號代稱檔案，只給固定驗證失敗原因，不回顯檔名、路徑、資料內容或例外原文。可於受控環境依當次檔名排序定位。
- 真實檔案不得放進 git 工作樹；測試全部採合成資料。GDR 產生方法見 genesis-deck `scripts/gdr_observe/README.md`。
