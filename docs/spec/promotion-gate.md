# 晉升閘門與 registry（D1）

> 實作：`strategies/_common/promotion/`　CLI：`scripts/promotion_gate.py`
> 規格來源：genesis-deck `docs/superpowers/specs/2026-10-30-strategy-live-dryrun-design.md` §4.1、§5、§17（Q7、Q8、Q9）

**本閘門不證明策略有效，只決定這支策略是否值得進入 dry run。晉升不等於授權實單。**

## 1. 檢查狀態與判定

每項檢查回傳 `CheckResult(name, status, value, threshold, detail)`，`status` 四選一：

| status | 意義 |
|---|---|
| `ok` | 通過 |
| `provisional` | 證據不足但無反證（樣本 50–249 筆、`n_trials=1`、外部檢查 WARN） |
| `blocked` | 未過門檻 |
| `unavailable` | 沒跑或算不出來。**絕不視為 ok**，報告中會明列 |

整份報告的 `verdict`：有任何 `blocked` → `blocked`；否則有 `provisional` 或 `unavailable` → `conditional`；全 `ok` → `eligible`。

## 2. 門檻（`rules.py`，只准向上收緊）

| 項目 | 門檻 |
|---|---|
| 有效觀測數 | ≥ 250 `ok`；≥ 50 `provisional`；其餘 `blocked` |
| DSR | > 0.95（`n_trials=1` 時只給 `provisional`，因為沒有 deflate） |
| PBO | < 0.5（至少 2 個候選設定，T×N） |
| 成本乘二後總報酬 | > 0 |
| SPA | p < 0.05（p 值由外部提供，D1 不計算） |
| 外部檢查 | 見 §4 |

改動任何門檻必須同步改 `tests/test_promotion_gate.py::test_thresholds_are_pinned`，並在 PR 說明理由。

## 3. n_trials

`n_trials` **一律讀 trial ledger**（`log/trials.jsonl`，`scripts.triage_generated.count_existing_selection_trials`），`GateInputs` 沒有可傳入的欄位。讀不到 ledger 時 N=0，報告照實顯示。

## 4. quant-backt-tools 結束碼對照

閘門不 import `quant_backt`，只吃其 CLI 結束碼（語意依其 SKILL.md，**未於本 repo 實跑驗證**）：

| 結束碼 | 閘門狀態 |
|---|---|
| 0（PASS） | `ok` |
| 1（WARN／INCONCLUSIVE） | `provisional` |
| 2（FAIL） | `blocked` |
| 3（輸入錯誤）、未提供、未知碼 | `unavailable` |

檢查名：`param_robustness`、`tail_scenarios`、`repro_audit`、`engine_parity`。

## 5. registry（`log/promotions.jsonl`）

append-only JSONL，一行一筆事件，只追加不改不刪。共同欄位：`event`（`screened`／`promoted`）、`ts`、`strategy_id`、`lineage_id`、`branch`、`strategy_sha256`（`strategy.py`＋`config.yaml`）、`framework_version`、`data_snapshot`、`cost_model_version`、`tags`。

- `screened` 另有 `report`（`GateReport.to_dict()`）。
- `promoted` 另有 `by`、`note`、`verdict`。

晉升規則：

- 簽署者必須在環境變數 `GS_PROMOTERS`（逗號分隔）內；**未設定即拒絕**（fail closed）。
- `blocked` 不得晉升，沒有人工覆寫；`conditional` 必須附 `--note` 簽註。
- 晉升時重算 sha256，與 `screened` 不符即拒絕（策略內容凍結）；改策略視為同 lineage 的另一分支，需重新 `evaluate`。
- 同一 sha256 不可重複晉升。

## 6. CLI

```bash
python scripts/promotion_gate.py evaluate --strategy-dir strategies/X --strategy-id X --perf perf.parquet \
  --lineage-id L1 --branch main --framework-version zipline-tej@abc --data-snapshot 2026-10-30 \
  --cost-model-version L1-v0 --stress stress.parquet --external param_robustness=0
GS_PROMOTERS=kevin python scripts/promotion_gate.py promote --strategy-dir strategies/X --strategy-id X --by kevin [--note "..."]
```

結束碼：0 成功、2 被拒（`PromotionError`）、3 輸入錯誤。`--perf`／`--stress` 為含 `returns` 欄的 parquet；`--candidates` 為 T×N 的 parquet。

## 7. 餵入 SPA p 值的注意事項

`--spa-p` 的數值由外部提供。`reality_check.hansens_spa()` 回傳 `p_lower`／`p_consistent`／`p_upper` 三個值，**不得挑最小的 `p_lower`**：合成資料實測（500 筆、1 個弱正報酬候選＋40 個明顯虧損候選）`p_lower=0.047`（會通過 0.05）但 `p_consistent=0.135`、`p_upper=0.784`。Hansen 建議用 `p_consistent`；`p_upper` 最保守。**決定：一律用 `p_consistent`**（Hansen 2005 的建議值）。`p_upper` 在候選池混有很多爛策略時會被推到接近 1（上例 0.784），使閘門幾乎永遠擋下，失去鑑別力；`p_lower` 太寬鬆。門檻只准收緊，所以不得改用 `p_lower`。

## 8. 信任邊界與已知限制

**閘門只重算它收到的東西，不驗證來源。** 因此：

- `--spa-p` 與 `--external name=code` 是呼叫者手填的數字。這兩類檢查在報告中標記 `self_declared: true`，`detail` 註明「自行申報，閘門未驗證來源」。**只要有任何自行申報的檢查，判定最多 `conditional`**（有 `blocked` 時仍是 `blocked`）；也就是說經 CLI 走完整流程的策略一律需要人工簽註才能晉升。
- 報酬序列、候選矩陣、成本壓力序列由呼叫者提供。報告的 `inputs_sha256` 記錄這三份陣列的雜湊，讓後人能比對「這份報告是用哪組數字算的」；**它不證明這些數字真的來自該策略的回測**。
- `--by` 是自報身分，registry 的 append-only 只是慣例（程式不改不刪，但檔案本身人人可寫）。**真正的控制是對 `log/promotions.jsonl` 的 PR 審查**：晉升事件必須經過有權限者審查才能進 main。`GS_PROMOTERS` 只是第一道防線。

### 反覆篩選
同一個 `strategy_sha256` 可以被 `evaluate` 多次（例如換輸入檔重跑）。這些重篩**不計入 trial ledger**，所以 registry 會記 `screening_count` 與 `previous_verdict`；CLI 在第二次起於 stderr 警告；`promote()` 對 `screening_count > 1` 的版本一律要求附簽註。

### 撤銷
同一 sha 之後若又被 screened 且判定 `blocked`，先前的 `promoted` 視為失效（`registry.is_promoted()` 回 False），不可再被當成已晉升。

### N 與 DSR 的假設
- `n_trials` 讀 ledger 並與 `stat_tree`／`audit_ledger` 同口徑：同 `trial_id` 取最後一筆（gs-strategy#50），沒有 `trial_id` 的逐筆計入（見 `test_n_trials_uses_deduplicated_ledger`）。
- `deflated_sharpe_ratio` 未傳 `sr_variance_across_trials`，使用函式預設的 `1/(n-1)`（`n` 為該策略的觀測數），**不是**真實的跨 trial 夏普變異數。若實際各 trial 的夏普分散得比這更開，DSR 的 deflate 會偏輕。D1 沿用預設；要改用真實變異數需要 ledger 保存各 trial 的夏普，屬後續工作。

### 其他已知限制
- `strategies/_common/promotion/gate.py` 反向 import `scripts.triage_generated`（`strategies/_common/graph/ledger.py` 也是同樣做法）。尚未重構成單向依賴。
- 成本模型：`per_contract_cost` 是**單邊、每口、每筆成交**——zipline-tej `PerContract.calculate` 以 `abs(transaction.amount * cost_per_unit)` 計費（另有每張訂單一次性的 `exchange_fee`，本 repo 設為 0）。所以 MTX=100 代表每口每次成交 100 TWD，來回 200。`spread_points` 另計滑價。**決定：「成本乘二」＝把 `per_contract_cost` 與 `spread_points` 同時乘二後重跑回測**，並以乘二後的報酬序列餵入 `--stress`。閘門本身只忠實計算收到的序列，不驗證它是否真的乘二。

## 9. 營運設定

- **`GS_PROMOTERS`**：只列實際有權按晉升的人，建議只放 `gsinvest017-ai`（與 main 合併權限一致，另加 admin 一人即可）。未設定一律拒絕；agent 的環境不應設定此變數。
- **registry 的 PR 審查**：`log/promotions.jsonl` 的任何變更都必須走 PR，由 `gsinvest017-ai` 審查後才能進 main。
