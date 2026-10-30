# Quant Auto-Research 漏斗：349 個候選怎麼驗

> 統計判準見 [`statistical-decision-tree.md`](statistical-decision-tree.md)
> 分診工具：`scripts/triage_generated.py`
> 相關計畫：gs-bulltrap-plan/docs/PLAN-auto-research-loop.md（M7–M13）

> **模組可用性（2026-10-30 查證）**：`reality_check.py`（White RC / Hansen SPA /
> Romano-Wolf StepM）與 `tradability.py`（VR / Hurst / permutation entropy /
> Ljung-Box / runs test）**已於 2026-10-02 隨 PR #28（`dev/pv-mtf-mdd-disposition`）
> 併入 `main`**。2026-09-01 的舊註記（「只存在於未合併分支」）已過時。依賴這兩支的
> 環節（L2 標的可交易性、`best_of_M` 的 SPA/RC 腿）現在可在 `main` 上跑；
> Ljung-Box 需要 `statsmodels`（已列於 `requirements-test.txt`）。

---

## 0. 先講結論，因為它改變了問題

harness 的搜尋前沿（`http://127.0.0.1:9101/trials`）顯示 gs-strategy 有 **349 個
候選從未被嘗試**。原始需求是「快速有效率精確地驗證這 349 個」。

實測之後，這個問題需要被重新表述。三個查證過的事實：

**事實一：349 個候選是 3 個相異的回測。**
把每份 `strategy.py` 剝掉 docstring 之後取正規化 AST 指紋，349 份檔案只剩 **3 個
相異值**（316 buy_and_hold、20 momentum、13 mean_reversion）。再比對完整組態
（params / bundle / symbols / 期間 / 資金），每一組內部**完全相同**。349 份檔案
的位元組確實全不同——差在 docstring 裡嵌的論文標題。

**事實二：316 個（90.5%）連假說都沒有。**
它們的 `matched_keywords` 是空的，`template` 是 fallback `buy_and_hold`。產生器
沒有從論文抽出任何訊號，只是掛了一個「買進 TX 並持有」的骨架上去。其中包括
《An Interview with Cliff Asness》《Tax-Aware Investing》這類根本不是策略的文章。

**事實三：349 個沒有任何一個實作了自己的論文。**
每份 manifest 的 `review_checklist` 第一行自己就寫著：

> Replace `_generate_signal()` with the paper's actual signal formula

連那 20 個被標成 `momentum` 的也一樣——關鍵字命中 `momentum` 就掛上一個
252/21 的日頻動能骨架。其中 `arxiv_2605_04004` 那篇的標題是《Structural Limits
of OHLCV-Based Intraday Signals in MNQ Futures: A Systematic Falsification Study》，
是一篇論證「盤中動能訊號無效」的**證偽研究**，被自動掛上了一個做多動能的骨架。
關鍵字匹配對「這篇論文說了什麼」幾乎不帶資訊。

### 所以「驗證這 349 個」不可能照字面執行

沒有東西可以驗證。可以驗證的只有 3 個骨架，而那 3 個骨架不是任何人的假說。

真正的問題是：**從 349 篇論文裡，分出哪些值得投入人力寫成假說。**

---

## 1. 為什麼「全部跑一遍」是錯的答案

即使拋開事實三，「把 349 個都跑一遍」在統計上也有可計算的代價。

Deflated Sharpe 的 deflation benchmark（真實 Sharpe = 0 時 N 次獨立試驗的
E[max SR]）隨 N 上升：

| 母體 N | E[max SR] | 相對現況 |
|---:|---:|---:|
| 33（現況） | 2.112 | — |
| 35（分診後） | 2.136 | +1.1% |
| 382（全部跑完） | 2.971 | **+40.6%** |

關鍵在於**這個門檻是全域的**。它不只套在新候選上，而是套在 repo 裡每一支現有與
未來的策略上。跑完那 349 個骨架的代價，是讓 `vgrsi_tx`、`tsmom_tx_mtx`、
`xsmom_stkfut_rmt` 全部變得更難通過——換來的是 316 次重複同一個買進持有。

> 這不是「多做一點研究總是好的」。多做的研究若不帶新資訊，就是純粹的自傷。

---

## 2. 漏斗：五層，按成本排序，N 的帳分開記

設計原則是**貴的檢定放後面，而且只有最後一層計入 N**。

```
L0  機械分診        349 → 5     成本 ~1 秒     ΔN = 0
L1  假說萃取        5 → k       成本 人×論文    ΔN = 0
L2  標的可交易性     k → k'      成本 秒級       ΔN = 0
L3  單次確認回測     k' → k'     成本 分鐘級     ΔN = 1／個
L4  統計判決        k' → 判決    成本 分鐘級     ΔN = 0（沿用 L3 的）
```

### L0 — 機械分診（`scripts/triage_generated.py`）

兩個可重跑的判準，都不看績效：

1. **無假說骨架** — `matched_keywords` 為空且模板是 fallback。規範 §一 關卡零
   要求假說先於資料；沒有假說者從未進入檢定家族，排除它是免費的。
2. **與既有候選同組態** — 正規化 AST ＋ 組態指紋皆相同。重跑不產生新資訊，
   卻會讓 N 加一。

實測產出：

```
相異等價類：3
  [9b3021c211cd:ccf694f1cd] x303  buy_and_hold    keywords=—
  [94e44fee0f5e:6cca7e0cdd] x20   momentum        keywords=['momentum']
  [fb21009159b3:ce50bf9959] x13   mean_reversion  keywords=['reversal']

excluded:無假說骨架          303
excluded:與既有候選同組態     31
excluded:manifest 無法解析    13
候選                          2
```

**排除不是證偽。** disposition 一律標 `excluded:` 而非 `rejected`——harness 的搜尋樹
本來就把兩者分色，因為「這從來不是一個候選」與「這被測過而且輸了」是完全不同的
兩件事。記錄另標 `no_statistical_claim: true`，因為本關做的是檔案比對。

**每筆排除都附 `resurrect_when`。** 排除的對象是**這個骨架**，不是那篇論文。人讀過
論文、寫下假說、把訊號實作進 `strategy.py` 之後，組態指紋就會改變，它就是一個
全新的獨立候選。少了這一欄，這份分診會被日後的人讀成「這 347 篇論文都沒價值」，
而那是本關沒有做過的宣稱。

### L1 — 假說萃取（人或 LLM，但產出必須是可證偽的東西）

L0 之後剩下的、以及任何人想從 303 篇裡撈回來的，都走這一關。輸入是論文，
輸出是一份 **pre-registration**，不是程式碼。每份必須回答：

| 欄位 | 為什麼要問 |
|---|---|
| 假說（一句話） | 沒有一句話講得完的，通常是還沒想清楚 |
| 經濟直覺：誰在另一邊持續虧錢 | 規範 §一 關卡零；答不出來的訊號多半是資料挖出來的 |
| 研究類型（A–F 類） | 決定關卡二與關卡三的具體內容 |
| 標的可及性 | 論文做 MNQ／美股，我們只有 `tquant_future` 的 TX／MTX |
| 主要指標與事前門檻 | 事後不得更改 |
| 預計測試的參數組合總數 | 供關卡四的修正使用 |
| 證偽條件 | 事先寫下「什麼結果出現就放棄」 |

**這一關 ΔN = 0，因為還沒有碰資料。** 也是整條漏斗唯一無法自動化的一關——
把論文讀成一個可證偽的假說是研究工作本身，不是預處理。

> **這裡有一個必須主動防的失效模式。** LLM 讀論文寫假說，會撞上 *Profit Mirage*
> (arXiv:2510.07920) 指出的預訓練污染：模型「記得」某些策略在歷史上有效，寫出的
> 假說會系統性偏向那些已被回測過頭的方向。緩解是 L1 的產出必須經人簽核，且
> pre-registration 一旦 commit 就凍結（見 §4）。

**淘汰條件**：標的不可及、假說無法在 `tquant_future` 上表達、或答不出「誰在
另一邊虧錢」→ `excluded:標的不可及` / `excluded:無經濟機制`。

### L2 — 標的可交易性（`validation/tradability.py`）

**這一關檢定的是標的，不是策略，所以 ΔN = 0。** 規範原文沒有這一關；補上它是
因為若 TX 在候選的持有期上根本與隨機漫步無法區分，任何策略在它上面的顯著都是
取樣運氣。成本極低（不需回測），所以放在回測前面。

跑 `ta_suitability(prices, qs=[候選的持有期])`：變異比、Ljung-Box、runs test、
permutation entropy、DFA Hurst。

**三個使用紀律**（`tradability.py` 的 docstring 已寫死，這裡重申）：

1. **看的是各成分的 p 值，不是 0–100 的 score。** 那個 score 沒有抽樣分佈、沒有
   信賴區間、沒有理論說 55 是對的切點。它只用來排序人的注意力。
2. **成分之間不獨立**，所以 score 不是多重檢定校正，加更多成分也不會變成校正。
3. **必須在訓練窗上跑並凍結結論，再回測其後的窗。** 在同一段樣本上既篩選又評估
   就是選樣偏差，而這個檢定偵測不到你這樣做了。

**通過是必要條件，不是充分條件。** 它只說「這條序列在這個樣本上可與鞅區分」，
沒說那個相依性樣本外還在、扛得住成本、或大到值得交易。

### L3 — 單次確認回測（**ΔN = 1**）

**每個存活的候選只跑一次，用 pre-registration 裡凍結的那一組參數。不掃參數。**

這是整條漏斗上唯一計入 N 的一關，所以紀律最緊：

- 想調參數？那是**新的 trial**，N 再加一，無例外。
- 跑壞了、中途放棄了、結果難看沒寫進報告——**照樣計入 N**。
- 想理解反應面形狀（哪個參數重要、最佳點是高原還是尖峰）？走 `purpose=diagnostic`
  的獨立路徑：Sobol / Latin Hypercube 抽點 ＋ PED-ANOVA / Morris screening，
  **ΔN = 0，但永遠不得從中挑出「表現最好的」來用**。一旦挑了，這條防線就失效。

### L4 — 統計判決（`validation/decision.py` ＋ 既有四支驗證）

跑 `resolve(facts)` 拿到處方，照處方執行，把 `stat_decision` 寫進 trial 記錄。
四支既有驗證各管一種失效模式，彼此不可替代：

| 模組 | 擋的是什麼 |
|---|---|
| `cpcv.py` | 標籤序列重疊造成的洩漏 |
| `sharpe.py` | 單一策略 Sharpe 的估計誤差（PSR / DSR） |
| `pbo.py` | 樣本內冠軍樣本外崩掉的機率 |
| `reality_check.py` | 跨 M 個策略的資料窺探（White RC / Hansen SPA / Romano-Wolf） |

判決三選一：**可交易** / **繼續累積** / **證偽**。判「可交易」之後還要進每日紙上
追蹤至少一季，複驗後才談小資金實盤——統計顯著不等於直接上實盤。

---

## 3. 漏斗跑完的預期形狀

| 層 | 進 | 出 | 累計 ΔN |
|---|---:|---:|---:|
| L0 機械分診 | 359 | 2 + 可撈回的 | 0 |
| L1 假說萃取 | 2 + 撈回 | 讀完論文才知道 | 0 |
| L2 可交易性 | ↑ | ↑ | 0 |
| L3 確認回測 | k' | k' | **+k'** |
| L4 判決 | k' | 判決 | 0 |

**k' 不填數字，因為現在填的任何數字都是編的。** 它取決於有多少人願意讀論文寫
假說，而那是 L1 的產出，不是可以事先推算的。可以確定的只有：N 的增量等於 L3
實際跑的次數，而不是 349。

---

## 4. 讓判決可回溯：pre-registration ＋ ledger

### Pre-registration

`experiments/preregistration/<id>.yaml`，內容照 Arnott, Harvey & Markowitz (2019)
的七項 checklist。存證方式：**git commit ＋ GPG 簽章的 annotated tag ＋ push**，
推到遠端即形成第三方時間戳。

一條硬規則：**agent 不得改寫既有的 pre-registration 檔**，用 pre-commit hook 擋。
沒有這條，「事前登記」就只是一個晚一點才寫的檔案。

### Trial ledger

`log/trials.jsonl`，append-only，每筆帶 `stat_decision` 區塊。`purpose=selection`
的每一筆都要能指回一份 pre-registration。

稽核用 `audit_ledger(records)`：把每筆的 `decision_path` 餵回 resolver 重算，
比對記載；同時**逐筆重算 N** 而不信任報告上的數字。

### 這如何成為「策略搜尋決策樹的一部分」

harness 的搜尋歷史樹（`/trials`）本來只記「試了什麼、結果如何、為什麼放棄」。
加上 `stat_decision` 之後，每個節點還帶著**它憑什麼下這個判決**：用了哪條規則、
基於哪組宣告的事實、當時的 N 是多少、門檻多少。

於是樹上的一條死路不再只是「這個變體輸了」，而是可以被追問：

- 它是**證偽**（測過而且輸了）還是**排除**（從來不是候選）？— disposition 前綴
- 它輸在哪一關？— `gate_status` 與 `threshold`
- 它當時的門檻為什麼是那個數字？— `n_trials_used` 與 `threshold_id`
- 那個門檻用對了嗎？— `audit_record` 重算

L0 分診的樹形也刻意反映結論：根是分診本身，第二層是每個等價類的代表，重複者
掛在代表底下。這樣 349 個節點會直接畫成「其實是 3 個相異組態」，而不是排成
349 條平行的假分支繼續讓人以為有 349 個研究方向。

---

## 5. 順帶查出的資料品質問題

分診時發現 **13 份 manifest 不是合法 YAML**，全部來自 `repec`：

```
UTF-8 被誤以 latin-1 解碼後再編碼（mojibake）。
例：'2018–2020' → '2018â\x80\x932020'，U+0080 是控制字元，YAML 拒收。
```

修復處在**爬蟲的解碼層**（`quant_crawler/`），不在分診腳本裡。分診刻意只辨識、
不就地修字串——把檔案修好會讓它能解析，然後產生器繼續產出同樣壞掉的下一批。

這 13 份標 `excluded:manifest 無法解析`，`resurrect_when` 是「爬蟲修好並重新
產生 manifest 之後重跑分診」。

---

## 6. 明確不做的事

| 不做 | 為什麼 |
|---|---|
| 把 349 個骨架全部跑成 selection trial | N 33→382，全域門檻 +40.6%，換來 316 次重複的買進持有 |
| 用 LLM 自動把論文寫成 strategy.py 然後直接跑 | *Profit Mirage* / *The Alpha Illusion* 記錄的預訓練污染；L1 產出必須經人簽核 |
| 在 L0 加入「績效太差就排除」的判準 | 看績效再決定計不計入 N，就是選擇性回報 |
| 就地修好那 13 份 mojibake manifest | 修了症狀，爬蟲繼續產出壞的下一批 |
| 讓 autopilot 一路跑過統計 gate | gate 前的停頓正是讓產出可信的來源；已記錄過低 effort 多 agent 偽造 20/20 PASS 的實例 |
| 更精細的 CPCV 切法來「增加」樣本 | 不解決事件稀少，只是把同一批樣本重複使用並製造假多樣性 |

---

## 7. 現況與下一步

**已完成**

- 機器可讀規則集 `stat-ruleset-1.1.yaml` ＋ resolver ＋ 43 個測試
- `audit_record` / `audit_ledger` 可回溯稽核
- L0 分診工具（dry-run 預設），349 → 3 個等價類已實測

**已完成（2026-09-01 續作）**

- `--apply` 已寫入 ledger，harness 前沿由 349 降到 **0**
- 修掉 mojibake 的病因（`http.py` 的 charset fallback）、症狀（`papers.db` 32 筆）、
  以及讓它無法被修的死結（`generate_bundle` 讀不了壞 manifest 就整個炸掉）。
  359 份 manifest 現在 0 份無法解析
- online FDR（ADDIS）已實作並接進 resolver：`open_mining` 的處方會帶
  `online_fdr_required`，測試對 `online-fdr` 套件逐步交叉驗證
- `experiments/preregistration/` ＋ `TEMPLATE.yaml` ＋ 驗證器 ＋ 擋改寫的
  pre-commit hook（端到端驗過：登記檔擋、README 放行、`--no-verify` 仍可繞過）

**待做**

1. L2 的 `ta_suitability` 接成可重跑的 CLI（模組已在 `main`，見開頭註記；CLI 尚未做）
2. `stat_gate` 作為 CI required check：不過門檻就 exit 1
3. ~~把 `reality_check.py` / `tradability.py` 落地到 `main`~~——已於 2026-10-02 隨 PR #28 完成

**卡住、需要人決定的**

- 那 303 篇「無假說骨架」要不要有人系統性地讀過一遍。這是唯一能把候選數從 2
  拉上來的路，但它是人力問題不是工具問題。
- 產生器（`quant_crawler.strategy_gen`）目前的關鍵字匹配對「論文說了什麼」幾乎
  不帶資訊（見 §0 事實三的 MNQ 證偽研究例）。要嘛改進它，要嘛承認它只是一個
  書籤產生器並停止把它的產出稱為「候選策略」。
