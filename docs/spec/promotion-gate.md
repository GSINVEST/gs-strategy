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
