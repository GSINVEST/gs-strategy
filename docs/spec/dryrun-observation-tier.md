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
| `d1` | `null` | `{verdict, reasons[], pending[], screened_at, rules_version}`；`reasons` 例：`"DSR 0.94 < 0.95"`，`pending`＝尚未完成（unavailable／provisional）的檢查，不算失敗 |
| `history` | `null` | `null` 或 `{basis:"in_sample_backfill", label:"樣本內回補（非前向）", start, end, equity:{optimistic,base,pessimistic}}` |

`equity` 永遠只是前向；歷史只在 `history`。

## 桌面版／gateway 需要跟著改（本 PR 不做）

見 PR 說明的清單。

## 未涵蓋／風險

- registry 目前沒有 screened 記錄（`log/promotions.jsonl` 在 PR #67，未 merge），所以真實環境要等 #67 進 main 後才能 `observe-start`。
- 回補需要 bundle 涵蓋 `--hist-start`；zipline 重跑一個長區間的耗時未量測。
- 觀察層績效好**不能**當作晉升理由；面板必須把 `label` 顯示出來。
