# 統計方法決策樹

> 規則集：`strategies/_common/validation/rulesets/stat-ruleset-1.1.yaml`
> 實作：`strategies/_common/validation/decision.py`
> 依據：《策略研究統計檢定規範 v1.1》（gs-bulltrap-plan/docs/統計檢定規範.md）

> **模組可用性（2026-10-30 查證）**：`reality_check.py`（White RC / Hansen SPA /
> Romano-Wolf StepM）與 `tradability.py`（VR / Hurst / permutation entropy /
> Ljung-Box / runs test）**已於 2026-10-02 隨 PR #28（`dev/pv-mtf-mdd-disposition`）
> 併入 `main`**。2026-09-01 的舊註記（「只存在於未合併分支」）已過時。依賴這兩支的
> 環節（L2 標的可交易性、`best_of_M` 的 SPA/RC 腿）現在可在 `main` 上跑；
> Ljung-Box 需要 `statsmodels`（已列於 `requirements-test.txt`）。

---

## 0. 這份文件要解決的問題

規範原文把「該用哪個檢定」寫得很清楚，但它是散文。散文能規定「重疊持有期一律
Newey-West」，擋不住的是另一件事：

> 研究者（或 agent）先看到結果，再回頭挑一個讓結果顯著的檢定。

那不是違反規範，那是**在規範沒有規定的空間裡做選擇**。規範說了「厚尾走無母數」，
但沒有規定「你必須先跑 Jarque-Bera 才能宣稱不厚尾」；說了「多重檢定要修正」，
但沒有規定「N 從哪裡讀」。這些縫隙就是 p-hacking 住的地方。

本決策樹把那些縫隙關掉。做法是把選擇的方向倒過來：

**不是「我要用哪個檢定」，而是「我宣告了這些事實，規則集告訴我只能用哪個檢定」。**

宣告的事實是可以被第三者重算的（Jarque-Bera 的 p 值、Ljung-Box 的 p 值、
ledger 的行數）。檢定不能。所以把判斷的重量全部移到事實上，判決就變得可稽核。

---

## 1. 樹的形狀：五個問句，五個槽位

規範 §零 列了五個判斷問題。本樹保留那五題，但把它們的作用寫死成**各自決定
處方的一個槽位**——不是五題一起查一張大表，而是五題各答各的，最後合成。

```
                        ┌─ Q1 估計量是什麼？ ─┐
                        │  Q2 幾組？配對嗎？  ├──► base    主檢定家族
                        └────────────────────┘
                        ┌─ Q4 觀測獨立嗎？ ───┐
                        │     （重疊／群聚）  ├──► se      標準誤修正
                        │  Ljung-Box 過嗎？   │
                        └────────────────────┘
                        ┌─ Q3 常態嗎？ ───────┼──► primary 參數 vs 無母數
                        └────────────────────┘
                        ┌─ Q5 第幾次檢定？ ───┐
                        │  N 讀 ledger 得出   ├──► threshold 顯著門檻
                        └────────────────────┘
                        ┌─ Q6 有效樣本多少？ ─┼──► gates   最小樣本閘門
                        └────────────────────┘
```

拆成五個獨立槽位不是為了好看，是為了讓**「換一個檢定」必然對應「改一個宣告的
事實」**。若五題合查一張大表，改任何一格都可能悄悄換掉整個處方；拆開之後，
處方的每一個部分都能被單獨追問「你憑哪個事實」。

### 為什麼多了 Q6

規範原文只有五題。Q6（有效獨立觀測數）在規範 §五 是隱含的——所有 n 門檻都在
講它——但沒有被列為選檢定前的判斷題。實務上它是最常被搞錯的一個：把資料列數
當成 n，等於假裝重疊取樣的每一天都是一次獨立的賭注。所以本樹把它升格成必答題，
而且**缺值時拒絕作答**。

---

## 2. 槽位一：base — 估計量 × 設計

| 你想回答什麼 | estimand / design | 主檢定 | 無母數並列 |
|---|---|---|---|
| 事件後超額報酬 ≠ 0？ | `mean_return` / `one_sample` | 單樣本 t | Wilcoxon 符號檢定 |
| 策略贏基準？（同期間） | `mean_return` / `paired` | 成對 t（逐期差） | Wilcoxon 符號檢定 |
| A 組事件 vs B 組事件？ | `group_difference` / `two_independent` | Welch t | Mann-Whitney U |
| 五分位報酬有差異？ | `group_difference` / `k_groups` | 單因子 ANOVA | Kruskal-Wallis |
| 分位報酬單調嗎？ | `monotonicity` / `none` | 斜率檢定 | Spearman |
| 因子預測未來報酬？ | `rank_ic` / `one_sample` | IC 序列均值 t | （IC 本身即無母數） |
| 勝率 ≠ 50%？ | `win_rate` / `one_sample` | 二項檢定 | （已是精確檢定） |
| Sharpe 顯著 > 0？ | `sharpe` / `one_sample` | Lo (2002) 修正 SE | block bootstrap CI |
| 濾網版贏抱著不動？ | `sharpe` / `paired` | 夏普差 bootstrap CI | block bootstrap CI |
| 勝負 × 多空頭年有關？ | `contingency` / `two_independent` | 卡方 | Fisher（期望值<5） |
| 兩腳長期均衡？ | `cointegration` / `series_vs_series` | Engle-Granger / Johansen | — |
| 報酬常態嗎？ | `distribution_shape` / `one_sample` | Jarque-Bera | Shapiro-Wilk（n<2000） |
| 這個標的值得做技術分析嗎？ | `serial_structure` / `none` | 變異比 + Ljung-Box | runs / PE / DFA Hurst |
| 最大回撤會有多深？ | `drawdown` / `none` | — | 交易順序重排蒙地卡羅 |

**規則**：無母數欄位為空者，規則集必須寫 `nonparametric_absent_because` 說明理由。
靜默省略會讓「這個估計量沒有無母數版本」跟「忘了跑」長得一模一樣。

**明列的禁用**：`mean_return` / `paired` 不得用獨立雙樣本 t。同期報酬高度相關，
獨立假設不成立——規範 §零 Q2 稱這是最容易選錯的一題，所以規則集把它列進處方的
`forbids`，讓它主動出現在使用者眼前，而不是靠人記得。

---

## 3. 槽位二：se — 重疊 × 自相關

| overlap | autocorr | 標準誤 | lag |
|---|---|---|---|
| `none` | `no` | iid | — |
| `none` | `yes` | Newey-West | `floor(4·(n/100)^(2/9))` |
| `none` | `unknown` | **拒答** | — |
| `overlapping` | 任何 | Newey-West | `holding_periods − 1` |
| `clustered` | 任何 | cluster（需 `cluster_by`） | — |

兩個設計決定值得說明：

**重疊規則不看 Ljung-Box。** 重疊取樣本身就保證了自相關，再檢定一次沒有意義；
更重要的是，若這條規則去看 `autocorr`，一個宣告 `autocorr=no` 的重疊樣本就會拿到
iid 標準誤——而那組宣告在重疊取樣下本來就不可能成立。

**`autocorr=unknown` 拒答，`normal=unknown` 不拒答。** 界線畫在「預設方向是否
保守」：`normal` 缺值併入非常態，走無母數，那是更嚴的方向，而且規範 §零 已經
明訂了這個預設立場。`autocorr` 沒有這種保守方向——iid 是唯一會讓標準誤變小的
選項，預設它等於**獎勵沒跑檢定的人**。

---

## 4. 槽位三：primary — 常態性決定誰是主檢定

```
normal = yes  →  參數檢定為主，無母數並列
normal = no   →  無母數為主，參數並列        ← 缺值時走這條
兩者結論不一致  →  一律以無母數為準（規範 §零）
```

規範的預設立場是「台股日報酬非常態、事件報酬厚尾」。本樹把它實作成 `unknown → no`，
並在處方的 `notes` 裡寫明是套了預設——使用者要走參數主檢定，就得先跑 Jarque-Bera
並主動宣告 `normal=yes`。

---

## 5. 槽位四：threshold — 這是第幾次檢定

| family | 條件 | 門檻 | 出處 |
|---|---|---|---|
| `single_preregistered` | 事前登記的唯一主假說 | \|t\| ≥ 2 | 傳統標準 |
| `bounded_multiple` | N ≤ 5 | p < 0.05/N（Bonferroni） | 規範 §一 關卡四 |
| `bounded_multiple` | N ≥ 6 | \|t\| ≥ 3 | Harvey-Liu-Zhu (2016) |
| `open_mining` | 連續挖掘型 | \|t\| ≥ 3 ＋ online FDR | 規範 §五 |
| `best_of_M` | 從 M 個跑完的裡挑最好 | DSR > 0.95 且 PBO < 0.5 | Bailey & LdP (2014) |

### N 從哪裡來

`n_trials` **不得由執行者自估**，必須讀 trial ledger。規則集在 `family=bounded_multiple`
而 `n_trials` 缺值時直接拒答，並在錯誤訊息裡指向 ledger——包含跑壞的、放棄的、
以及結果難看沒寫進報告的那些。漏記一個，DSR 就被高估一分。

### 未事前登記是降級不是作廢

規範 §一 關卡零：事後發現的顯著結果一律視為「探索性發現」，門檻自動升到 |t| ≥ 3，
且需要新資料重新驗證才能升回正式假說。規則集用 `preregistered=False` 表達，效果是
把 `family` 強制降級為 `open_mining`。

### best_of_M 為什麼要跟 bounded_multiple 分開

**這是本樹對規範原文的增補。** 規範把「比較多個策略變體的夏普值」交給 DSR，
但沒有把它跟「單一策略測了 N 組參數」在流程上分開。兩者的正確工具不同：

- 單一策略 × N 組參數 → Bonferroni 或 HLZ 門檻
- M 個策略挑最好 → DSR（扣掉 E[max SR]）＋ Hansen SPA／White RC；要逐一指認
  哪幾個勝出時用 Romano-Wolf StepM

混用會讓「選最好那個」的偏差被當成「測了 N 組參數」處理，低估了偏差。

---

## 6. 槽位五：gates — 最小有效樣本

| 情境 | 門檻 | 未達時 |
|---|---|---|
| 勝率宣稱 | n_eff ≥ 30 | **不得宣稱勝率** |
| 規則型系統（交易為單位） | n_eff ≥ 30 | 不進統計，標注觀察中 |
| 事件研究 | n_eff ≥ 250 判決；≥ 50 只報方向 | `blocked` / `provisional` |
| Rank IC 可交易參考線 | \|IC\| ≥ 0.03 且 ICIR ≥ 0.3 | 標為參考線以下 |
| 樣本期 | 至少含一次完整空頭 | 判決降級為繼續累積 |

閘門的回傳值刻意分成 `ok` / `provisional` / `blocked` 三態，而不是 pass/fail：

> **「樣本不足以宣稱」與「檢定不顯著」是兩件事。**

前者是「我們還不知道」，後者是「我們知道它沒有」。在報告裡寫成同一句話，會把
一個未完成的研究說成一個已完成的否定結論。型別上分開，是為了讓寫報告的人分不開
也錯不了。

---

## 7. N 記帳：purpose 三分法

**這是本樹對規範原文的第二處增補**，也是讓大規模候選篩選在統計上站得住的關鍵。

| purpose | ΔN | 可被選用 | 判決上限 |
|---|---:|---|---|
| `screening` | 0 | ✕ | 候選 |
| `diagnostic` | 0 | ✕ | 無 |
| `selection` | **1（無條件）** | ✓ | 可交易 |

規範要求「誠實記錄嘗試過的組合總數」，但沒有區分「為了理解反應面形狀而跑」與
「為了挑一個來用而跑」。若不分開，任何診斷性探索都會推高 N，讓後續**所有**策略的
門檻上升——結果是研究者被誘導成少做診斷，方向完全相反。

隔離的代價是必須在機制上保證 `diagnostic` 的結果永不被選用。**一旦從中挑出
「表現最好的」，這條防線就失效。** 規則集用 `may_be_selected: false` 表達，
`audit_record` 會驗它。

### selection 的四條硬紀律

1. N 無條件累加，包含跑壞的、中途放棄的、結果難看沒寫進報告的。
2. 任何參數微調都算一次新 trial，無例外。
3. 冠軍任一關失敗即本輪 FAIL；改用亞軍＝新一輪，N 不歸零。
4. **降低門檻永遠不是同一輪的選項。門檻只准向上收緊。**

第 4 條是唯一能擋住自動化 p-hacking 的一條。auto-loop 最致命的失效模式是 agent
自己把門檻調鬆讓自己過關；用資料結構強制「只准收緊」比用 prompt 約束可靠。

---

## 8. 可回溯性：判決怎麼被稽核

每個 trial 記錄帶一個 `stat_decision` 區塊：

```jsonc
{
  "trial_id": "t-xsmom-v3",
  "stat_decision": {
    "ruleset_version": "1.1",
    "decision_path": {          // 當時宣告的事實
      "estimand": "rank_ic", "design": "one_sample",
      "overlap": "overlapping", "holding_periods": 5,
      "autocorr": "yes", "normal": "no",
      "family": "bounded_multiple", "n_trials": 9,
      "purpose": "selection", "n_eff": 520
    },
    "rule_id": "sr1.1-2d9de11172",   // 由上面那組事實推導出來的指紋
    "primary_test": "IC 時間序列的均值 t 檢定",
    "se_correction": "newey_west",
    "threshold": "|t| >= 3",
    "delta_n": 1,
    "may_be_selected": true
  }
}
```

稽核的機制很單純但關鍵：**把 `decision_path` 餵回 resolver 重算，比對結果。**

```python
from strategies._common.validation.decision import audit_ledger
audit_ledger(records)
# {'n_records': 41, 'n_selection_recomputed': 41, 'problems': [], 'auditable': True}
```

抓得到的三類問題：

1. **事後換檢定** — 記錄說跑的是 t 檢定，但那組事實依規則集該走 Wilcoxon。
2. **N 記帳造假** — 把 selection 的 ΔN 記成 0，好讓 DSR 的分母看起來小一點。
3. **規則集版本對不上** — 用新版規則替舊判決背書。

`n_selection_recomputed` 刻意**逐筆重算**而不信任任何人寫在報告裡的 N。N 是 DSR
的分母，也是這個專案裡最容易被悄悄低估的數字。

### rule_id 為什麼是槽位的指紋而不是整份處方的

處方裡的說明文字日後會潤飾。若 `rule_id` 跟著措辭變動，歷史 trial 就無法跨版本
比對。所以 `rule_id` 只由五個槽位（＋規則集版本＋purpose）算出來：措辭改了 id
不變，換了任一槽位 id 必變。

### 版本不可回頭改寫

既有 trial 記錄裡的 `ruleset_version` 是凍結的。修訂規則集要跳版本，舊判決依舊版
規則成立。用新版規則去重新解釋舊判決，就是事後改判準的另一種寫法。

---

## 9. 不做統計宣稱的記錄

L0 機械分診（見 `auto-research-funnel.md`）做的是檔案比對，不是統計判決。硬要
附一份檢定處方，等於把「用程式比對了兩份檔案」包裝成統計結論。這類記錄改標：

```jsonc
"stat_decision": {
  "purpose": "screening",
  "delta_n": 0,
  "no_statistical_claim": true,
  "basis": "mechanical_dedup: normalized-AST + config fingerprint"
}
```

`audit_record` 對這類記錄只驗兩件事：**沒有偷佔 N**，以及**有寫下憑據**。

---

## 10. 已知限制

誠實列出這份樹**沒有**解決的事：

- **規則集是一個分割，但覆蓋率有限。** 測試對 14 × 3 × 5 = 210 組事實組合窮舉
  過「零命中／多重命中」，但那只涵蓋目前列出的估計量。遇到表上沒有的問題，正確
  做法是補規則集（並跳版本），不是在呼叫端硬塞一個檢定。resolver 會為此丟
  `RulesetError` 而不是靜默挑一條。
- **本樹管的是「檢定選得對不對」，不管「資料對不對」。** 規範 §一 關卡一的存活者
  偏差、前視偏差、還原價、漲跌停不可成交，全部在本樹的射程之外。檢定做得再對，
  資料有偏差一樣全部作廢。
- **`n_eff` 由呼叫端宣告，本樹不驗算。** 它拒絕在 `n_eff` 缺值時作答，但無法判斷
  你填的 520 是不是真的。事件簇的客觀定義（EVT declustering）是另一項工作。
- **online FDR 的 p 值來源要自己接。** ADDIS 本身已實作
  （`validation/online_fdr.py`，並在測試裡對 `online-fdr` 套件逐步交叉驗證），
  但「策略搜尋的 p 值該怎麼定義」沒有現成答案——把 anytime-valid 直接套在
  Sharpe 上的論文查無。所以 `AddisBudget.test_one` 強制要求 `p_value_source`，
  而那個定義必須在 pre-registration 裡凍結。
- **把 e-value / anytime-valid 直接套在 Sharpe 或策略搜尋上的論文查無。** 若日後
  要走那條路，是自己接線不是照抄，且必須在 pre-registration 裡寫明 p 值的定義與來源。
