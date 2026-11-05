# research/ — 尚未成為策略的研究草稿

放「只有假說、指標或檢定計畫，還沒有進出場規則」的研究。這裡**不是策略集合**：沒有 `strategy.py`，不會被 `scripts/validate_dashboard_bundle.py` 或 runner 掃到，也不得宣稱已驗證。

| 草稿 | 性質 | 處置 | 狀態與缺口 |
|---|---|---|---|
| [`fin_otc_spread_tx`](fin_otc_spread_tx/draft.json) | 金融／櫃買價差對台指期的領先假說 | **留在研究區**（Jason 建議） | 開發期 9 組檢定 4 組通過但效果小；進出場、部位、成本未定；驗證組未揭盲；盤中即時指數來源未解（硬性前提）；盤中頻率超出 Gslang 第一版與現有 runner 的日頻範圍（IR 本身能否表達盤中未驗證） |
| [`pledge_wall_00631l`](pledge_wall_00631l/draft.json) | 00631L／0050 質借斷頭價位重建 | **只當風險指標，不當策略**（Jason 建議） | 無可用訊號門檻；不下單，進出場／部位／成本／成交時機皆不適用；質借成數 60% 為假設；「25%」所指待 Kevin 確認 |

[`jason-reply-20261105.md`](jason-reply-20261105.md) 是 Jason 對缺口的回覆原文（2026-11-05，#gs-quant-software），兩份草稿的 `answer_zh` 皆出自此。

## 格式

`strategy-research-draft/0.1`（格式與 linter 在 genesis-deck：`scripts/formal/lint_research_draft.py`，見 GSINVEST/genesis-deck#488）。每份草稿含 `gaps`（還差哪幾格）與 `ir_status`（`extractable: false` ＝ 缺轉換規則，不能抽 `fsm.ir.json`）。`provenance.sources[].sha256_lf` 綁定同目錄的 `source.html`（Jason 的進度報告原檔）。

## 晉升成策略的路

1. 人補齊 `gaps`（尤其進出場規則；不得由 agent 補數字）。
2. 抽 `strategies/<id>/fsm.ir.json`（strategy-fsm-ir/0.1.1），過 genesis-deck 的 lint／TLA+。
3. 依 spec v1.2 寫 `strategy.py`＋`config.yaml`＋`manifest.yaml`，通過 `validate_dashboard_bundle.py` 後移到 `strategies/<id>/`，並刪掉這裡的草稿。
4. 統計判定走 `docs/spec/statistical-decision-tree.md` 與 D1 晉升閘門；本目錄不做任何通過／失敗結論。
