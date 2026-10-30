# 實驗：台指期能否用 VolatilityVolumeShare 取代 FixedSlippage(6 點)

日期：2026-10-30　分支：`dev/experiment-slippage-vvs`　性質：可行性實驗，**未改任何既有策略、`futures_setup.py` 或 zipline**，未下任何單。

背景：genesis-deck 的 dry run 規格（`dev/strategy-live-dryrun-spec`，§6、§16）打算讓 L1 成本模型採「價差＋√ 衝擊（`VolatilityVolumeShare`）＋成交量上限」。目前期貨實際用 `FixedSlippage(spread=6.0)`，`VolatilityVolumeShare`（下稱 VVS）無人使用，且 eta 表沒有台指期。本實驗在 D3 動工前驗掉這個未驗證項。

## 結論（先看這裡）

1. **VVS 在這個 zipline-tej fork 上原樣不能用**：`MarketImpactBase.process_order(self, data, order)` 沒有 fork 新增的 `execution_price_type` 參數，但 `SlippageModel.simulate` 會傳入，回測第一筆委託就丟 `TypeError: MarketImpactBase.process_order() takes 3 positional arguments but 4 were given`（`vgrsi_vvs_raw.json`，只跑 2024-01～06 即可重現）。補一個只轉呼叫的 shim 子類後可正常跑完（vgrsi 2020–2026、tsmom 2018–2026），σ、ADV 皆取得，換月期間**沒有** NaN、沒有例外、沒有落到 `NO_DATA` 後備值（`n_nodata_fallback = 0`）。
2. **在台指期 1 口的規模下，市場衝擊項小到可忽略，而且小得不合理**：ψ（成交口數 / 20 日 ADV）中位數 2.3e-5（TX，vgrsi）與 1.6e-4（TX＋MTX，tsmom），最大 1.7e-3；預設 eta（0.049）下平均每口滑價 **0.0001–0.0005 點**，全期總成本約 NT$9–35（FixedSlippage 為 NT$26 萬）。eta 加倍結果仍是同一量級。VVS 在此規模等於「零滑價」。
3. **用 VVS 取代 FixedSlippage 會讓淨報酬與夏普變好，但那是假象**，不是模型更準：它拿掉了每邊 3 點的價差成本，卻沒有補上等價的價差項。
4. **FixedSlippage 6 點（每邊 3 點）相對 tick 代理偏保守**：Roll 有效價差代理在 TX/MTX 約 0.7–1.5 點（一檔 = 1 點），6 點約為代理的 4–8 倍。不過這是沒有報價的代理。後續驗證（見「後續驗證」）顯示收盤窗代理約 1.0–1.8 點，仍遠低於 6 點，但不應直接把 6 點砍到 1 點（見建議）。
5. **個股期貨是另一回事**（後續驗證 C）：`xsmom_stkfut_rmt` 在 VVS 下遇 NaN 成交量／基準價會崩潰、出現 75 次 `NO_DATA` 後備、ψ 最大 2.9；而 FixedSlippage(6 點) 讓同策略從 +15%（零滑價）變 −99%。L1 不能把 TX 的點數成本直接套到個股期貨。

### 對 L1 成本模型的建議

- **不要採用 VVS 的預設 eta 當 L1 的衝擊項**：它在台指期的值實質為 0，且 zipline fork 原樣會丟例外。
- **L1 成本 = 價差項（主）＋ 衝擊項（保險）＋ 成交量上限**：
  - 價差項：沿用固定點數，以每邊 `spread/2` 計。預設值先保留 6 點（保守），要調降請等有真實報價或實盤成交資料（見「未能驗證的部分」）；Roll 代理支持把下界設在約 1–2 點、情境分析用 2/4/6 點三檔。
  - 衝擊項：若要保留 √ 形式，eta 必須「校準」而非沿用 0.049。以本實驗的 σ≈0.16、ψ 中位數 2.3e-5 反推，要讓 1 口衝擊等於 3 點（約 1.5 bps）需 eta ≈ 2,000（預設的 4 萬倍）；即使 ψ 取成交量上限 5%，eta 仍需約 42。實務上 1 口規模衝擊項不是主要成本，建議 L1 在預設只放價差項＋成交量上限，衝擊項留作大部位情境的選配開關。
  - 成交量上限：1 口遠低於 5%（ψ ≤ 1.7e-3），不會成為約束；只在部位放大百倍以上才有意義。
- **個股期貨要另立成本參數**：價差以價格百分比（bp）表達，不沿用 TX 的「點」；對 NaN 成交量／NaN 基準價與 `NO_DATA` 後備路徑明確處理（拒絕成交或套保守成本）；成交量上限必須真正限制 ψ ≥ 1 的訂單。證據見「後續驗證 C」。
- **若仍要在 zipline 內用 VVS**：需先修 fork（`MarketImpactBase.process_order` 補 `execution_price_type`）。注意 VVS 以 `(high+low)/2` 為基準價，不是收盤價，會讓結果和 FixedSlippage 的基準不同（見下「基準價差異」）。這是 zipline-tej 的改動，不在本實驗範圍，只列為建議。

## 方法與環境

- zipline-tej：WSL `/home/kevin/gs-zipline-tej`（HEAD `b6c1504`）複製到 `~/exp-vvs/zl`，在獨立 venv（Python 3.11）安裝；原本機沒有可用的 zipline 環境。需要降版才能跑：`pandas==2.2.3`、`sqlalchemy<2`、`alembic<1.14`（最新版 pandas 3.x / SQLAlchemy 2.1 會讓 `run_algorithm` 當場失敗，與 VVS 無關）。
- 資料：`~/.zipline/data/tquant_future`（2026-10-29 ingest），有 `TEJAPI_KEY`。
- 策略：`vgrsi_tx`（TX，1 口，2020-01-01～2026-04-30，資本 NT$500 萬）與 `tsmom_tx_mtx`（TX＋MTX，2018-01-01～2026-04-30，資本 NT$1,000 萬），皆用其現有 `config.yaml`，日頻、收盤成交。
- 做法：[`scripts/experiments/slippage_compare.py`](../../scripts/experiments/slippage_compare.py) 在 import 策略前，於執行期把 `futures_setup.apply_taiwan_futures_costs` 換成帶指定滑價模型的版本（佣金設定與原函式相同）；每筆成交記錄參考價、成交價、口數、ψ、σ、ADV。VVS 用 `volume_limit=0.05`。
- 變體：`fixed6`（現行）、`vvs_default`（預設 eta：TX/MTX 不在 `ROOT_SYMBOL_TO_ETA`，`get_simulated_impact` 的 `except` 落到 `DEFAULT_ETA = 0.049018`）、`vvs_x2`（2 倍）、對照組 `fixed0`（零滑價、收盤價）與 `vvs_eta0`（eta=0、`(H+L)/2` 價）。
- 原始輸出：[`2026-10-slippage-vvs-data/`](2026-10-slippage-vvs-data/)。

## 結果 1：VVS 能不能跑

| 項目 | 結果 |
|---|---|
| 原樣 VVS | 失敗，`TypeError`（`process_order` 簽章與 fork 的 `simulate` 不合） |
| shim 後 TX／MTX | 正常；vgrsi 436 筆成交、tsmom 248 筆，無例外 |
| σ（`_get_window_data`：20 日收盤 pct_change 標準差 × √252，**小數**，非 %） | 中位數 0.160（TX）／0.158（tsmom） |
| ADV（20 日平均 `volume`） | 中位數 44,081（vgrsi，TX）／17,594（tsmom，TX＋MTX 混合） |
| 換月（continuous future，下單物件為實際合約） | `n_nodata_fallback = 0`、無 NaN；換月後新合約未觸發「歷史不足」後備路徑（由 `n_nodata_fallback=0` 推得，未逐筆檢查換月日） |
| 單位 | `MI = eta·σ·√ψ` 以 bps 計，σ 是小數：σ=0.16、ψ=1 時 MI 也只有 0.0078 bps，公式本身就讓衝擊極小 |

附帶觀察：bundle 的 TX ADV（約 4.4 萬）比逐筆資料算出的前月日盤成交量（`volume_bs` 約 12–17 萬）低 2–4 倍，此落差已在「後續驗證 A」查明：`volume_bs` 是雙邊合計（÷2 才是口數），且 bundle 只含日盤。

## 結果 2：三組成本回測（vgrsi_tx，TX 1 口）

| 變體 | 淨報酬 | 夏普(年化) | 成交口數 | 滑價總點數 | 滑價總成本 NT$ | 每口平均滑價（點） |
|---|---:|---:|---:|---:|---:|---:|
| (a) `fixed6`（現行） | 78.7% | 0.866 | 440 | 1,320 | 264,000 | 3.000 |
| (b) `vvs_default` | 82.7% | 0.915 | 440 | 0.04 | 8.7 | 0.0001 |
| (c) `vvs_x2` | 82.7% | 0.915 | 440 | 0.09 | 17.5 | 0.0002 |
| 對照 `fixed0` | 84.0% | 0.925 | 440 | 0 | 0 | 0 |
| 對照 `vvs_eta0` | 82.7% | 0.915 | 440 | 0 | 0 | 0 |

ψ：中位數 2.27e-5、最大 1.44e-4。

`tsmom_tx_mtx`（TX＋MTX，約 1,100 口）：

| 變體 | 淨報酬 | 夏普 | 滑價總點數 | 滑價總成本 NT$ | 每口平均滑價（點） |
|---|---:|---:|---:|---:|---:|
| (a) `fixed6` | 105.4% | 0.692 | 3,258 | 258,750 | 3.000 |
| (b) `vvs_default` | 113.0% | 0.722 | 0.25 | 17 | 0.0002 |
| (c) `vvs_x2` | 113.0% | 0.722 | 0.51 | 35 | 0.0005 |
| 對照 `fixed0` | 108.2% | 0.707 | 0 | 0 | 0 |
| 對照 `vvs_eta0` | 113.0% | 0.722 | 0 | 0 | 0 |

ψ：中位數 1.6e-4、最大 1.7e-3。

注意：
- FixedSlippage 的「6 點」是**完整價差**，每邊以 `close ± 3` 成交，所以每口平均滑價是 3 點，不是 6 點。
- 佣金（PerContract）在所有變體中維持開啟且相同。
- **基準價差異**：VVS 以 `(high+low)/2` 為基準價，不是收盤價。`vvs_eta0`（零衝擊）與 `fixed0`（零價差）的淨報酬差：vgrsi −1.3 個百分點、tsmom +4.8 個百分點，方向不一致，是基準價選擇造成的雜訊，不是成本。因此 (b)(c) 與 (a) 的報酬差**不能**全部解讀為成本差；比成本請看「滑價總成本」欄。
- 單次回測、單一路徑，沒有信賴區間；報酬差異用來說明量級，不做統計推論。

## 結果 3：6 點相對於 tick 有效價差代理

資料：QuantData `silver/ticks/taifex_futures`（只有成交，沒有報價），2023–2025，近月、純單腿合約（排除價差單）。方法：Roll (1984) `spread = 2·√(−cov(Δp_t, Δp_{t−1}))`，逐日算 lag-1 共變異數，報告逐日中位數。腳本 [`scripts/experiments/tick_spread_proxy.py`](../../scripts/experiments/tick_spread_proxy.py)。

| 商品／年 | 日盤 Roll（逐筆） | 日盤 Roll（1 秒收盤） | 夜盤 Roll（逐筆） |
|---|---:|---:|---:|
| TX 2023 | 0.73 | 1.00 | 0.62 |
| TX 2024 | 0.97 | 1.27 | 0.84 |
| TX 2025 | 1.08 | 1.45 | 0.85 |
| MTX 2023 | 0.84 | 1.02 | 0.72 |
| MTX 2024 | 1.03 | 1.17 | 0.96 |
| MTX 2025 | 1.13 | 1.33 | 1.00 |

（單位：指數點，完整價差；每個格子為逐日估計值的中位數，逐日 p90 約 1.2–1.4。各商品／年／時段至少 99% 的交易日 lag-1 共變異數為負，估計量有定義。）

判讀：
- 有效價差代理約 0.7–1.5 點，即約 1 檔（TX、MTX 最小跳動 = 1 點）。FixedSlippage 的 6 點（每邊 3 點）約為代理的 4–8 倍，**以「連續交易、一檔價差」的視角看是偏保守**。
- 代理有偏誤，方向不一：Roll 對有深度的簿通常**低估**真實成本（沒有納入逆選擇與掃單的價格衝擊；1 秒聚合值高於逐筆值就是旁證）；另一方面它也不含報價資訊，無法區分「價差」與「成交價的資訊性變動」。
- 我們的策略是日頻、**在收盤價成交**。收盤附近的價差與到達價落差（implementation shortfall）通常比盤中平均價差大，且 dry run 實際下單會有排隊與延遲；因此 6 點比代理保守，但**不能**僅憑這個代理就把它砍到 1–2 點。

## 後續驗證（2026-10-30 補做）

針對原先列為「未能驗證」的項目，能用現有資料驗的都補做了。

### A. `volume_bs` 與 bundle ADV 的差異：已查明

[`bundle_vs_tick_volume.py`](../../scripts/experiments/bundle_vs_tick_volume.py)：把 bundle 的 TX 合約日成交量，與 tick 中同合約同日的 `volume_bs` 對比（2023–2025，870 個合約日）。

- bundle `volume` ÷ tick **日盤** `volume_bs` 的中位數 = **0.50**（四分位距 0.500–0.540；各年中位數 0.501–0.503）。
- 結論：**`volume_bs` 是買賣雙邊合計（實際口數的 2 倍），bundle 的 `volume` 是單邊日盤口數**。前文「bundle ADV 比 tick 低 2–4 倍」的落差來源：兩倍來自雙邊計數，其餘來自 bundle 不含夜盤。ADV 與 ψ 的數量級因此是一致的，ψ 結論不變。
- 因此「結果 1」附帶觀察中引用的 tick 成交量（12–17 萬）應除以 2 才是實際口數（約 6–8.5 萬，日盤）；Roll 價差不受影響。

### B. 收盤時段的價差代理

[`tick_close_window.py`](../../scripts/experiments/tick_close_window.py)：Roll（逐筆，近月，日盤）依時段分窗，單位點，逐日共變異數按筆數加權。

| 窗 | TX 2023 | TX 2024 | TX 2025 | MTX 2023 | MTX 2024 | MTX 2025 |
|---|---:|---:|---:|---:|---:|---:|
| 開盤 08:45–09:00 | 0.94 | 2.06 | 1.76 | 1.19 | 2.29 | 1.75 |
| 盤中 09:00–13:30 | 0.70 | 1.00 | 1.05 | 0.82 | 1.03 | 1.12 |
| 收盤 13:30–13:45 | 0.89 | 1.31 | 1.47 | 0.86 | 1.14 | 1.22 |
| 收盤 13:40–13:45 | 1.07 | 1.68 | 1.78 | 0.96 | 1.42 | 1.49 |

收盤窗代理約為盤中的 1.2–1.7 倍，最貴的是開盤窗（最高 2.3 點）。最大值 2.3 點仍不到 6 點的一半。收盤窗每天只有約 1,000–1,500 筆（13:40–13:45），單日估計雜訊較大，各窗皆有 ≥ 99% 的交易日共變異數為負。**這仍是成交價代理，不是報價價差**，且沒有納入到達價落差與下單延遲，所以「6 點偏保守」的方向成立，但幅度不能由此確定。

### C. 其餘兩支策略

用相同腳本跑 `cubic_momentum_tx`（TX，2018–2026）與 `xsmom_stkfut_rmt`（個股期貨，2020–2026）。

**cubic_momentum_tx**（TX，955 口）

| 變體 | 淨報酬 | 夏普 | 滑價總成本 NT$ | 每口平均滑價（點） |
|---|---:|---:|---:|---:|
| `fixed6` | 102.8% | 0.642 | 573,000 | 3.000 |
| `vvs_default` | 103.7% | 0.680 | 1,645 | 0.0086 |
| `vvs_x2` | 103.7% | 0.680 | 1,667 | 0.0087 |
| `vvs_eta0` | 103.7% | 0.680 | 1,623 | 0.0085 |

與前兩支策略同向。這次出現 **1 次 `NO_DATA` 後備**：2018-04-20 的 `TX201805`，20 日收盤報酬標準差為 NaN，VVS 改用 7.5 bp 的固定衝擊，該筆滑價 8.1 點，**比 eta 衝擊大三個量級**，也比 FixedSlippage 的 3 點大。也就是說，VVS 在 TX 的主要成本其實來自這條後備路徑，不是 eta 公式。

**xsmom_stkfut_rmt**（個股期貨，多至 ~60 個 root）

- **VVS 原樣加 shim 後仍會崩潰**：`ValueError: cannot convert float NaN to integer`，位置在策略 `order_target_percent` 的口數換算。我加了診斷用守衛（`VVS_NAN_GUARD=1`：當 bar 成交量或 (H+L)/2 基準價為 NaN 時略過該筆），守衛後可跑完，共 45 次略過。這證實崩潰來自成交價為 NaN 汙染部位價值，而不是策略邏輯；守衛是我的診斷手段，不是建議的修法。
- 守衛後 VVS 結果：淨報酬 20.1%、夏普 0.49，3,211 筆、26,339 口；ψ 中位數 0.040、最大 **2.94**（成交量大於 ADV 的 2.9 倍，成交量上限之外也沒擋住）；ADV 中位數僅 83 口；**75 次 `NO_DATA` 後備**。eta 為 0／預設／2 倍的結果幾乎相同（20.10／20.10／20.10%），總滑價點數 35 幾乎全是後備路徑貢獻。
- **FixedSlippage(6 點) 對個股期貨不適用**：同一策略 `fixed6` 淨報酬 **−99.1%**（夏普 −2.83），`fixed0`（零滑價、同樣收盤價）則是 +15.3%（夏普 0.375）。6 點對股價約數十到數百元的個股期貨是價差的好幾個百分點，這是「點數」單位跨商品沿用造成的問題，不是策略邏輯。注意：`fixed6` 因部位被打爆，成交筆數（492）和 `fixed0`（1,314）不同，不能按成交比較。
- VVS 守衛版與 `fixed0` 的報酬差（20.1% vs 15.3%）同樣含基準價（(H+L)/2 vs 收盤）效應。

對 L1 的意義：**成本模型不能只為 TX／MTX 設計**。個股期貨需要：(1) 以價格百分比（bp）而非固定點數表達價差；(2) 對 NaN 成交量／NaN 基準價與 `NO_DATA` 後備路徑有明確處理（拒絕成交或保守成本），而不是靠 zipline 預設；(3) 對 ψ ≥ 1 的訂單要有真正生效的成交量上限。

## 未能驗證的部分

- **沒有買賣報價**：Roll 只是代理，不是有效價差本身。真實的有效價差（成交價對中間價的偏離）需要 L1 報價或實盤／模擬單成交資料，本實驗無法提供。
- **到達價落差與下單延遲**：收盤窗代理只反映成交價的彈跳，沒有納入從下單到成交的價格漂移。
- **大部位下的市場衝擊**：只測了 TX／MTX 1 口等級（及個股期貨小量），√ 衝擊在大部位下是否成立、eta 的合理值，無資料可校準。
- **單一路徑回測**：每個策略×變體只跑一次，沒有 bootstrap，報酬差異只作量級參考。
- **fork 修補後的行為**：shim 只是轉呼叫；若日後修 `MarketImpactBase.process_order`，需重跑。個股期貨的 NaN 崩潰也只做了診斷守衛，沒有找出該修在哪一層。
- **個股期貨的合理價差**：只證明 6 點不適用，沒有為個股期貨估計價差（tick 資料有個股期貨商品但本次沒分析）。

已在「後續驗證」補驗的項目：`volume_bs` 定義與 ADV 差異（A）、收盤時段價差代理（B）、`cubic_momentum_tx` 與 `xsmom_stkfut_rmt`（C）。

## 如何重現

在有 zipline-tej、`tquant_future` bundle 與 `TEJAPI_KEY` 的環境（見上「方法與環境」的降版需求）：

```bash
python scripts/experiments/slippage_compare.py strategies/vgrsi_tx fixed6 out/vgrsi_fixed6.json
python scripts/experiments/slippage_compare.py strategies/vgrsi_tx vvs_default out/vgrsi_vvs_default.json
python scripts/experiments/tick_spread_proxy.py <QUANTDATA>/silver/ticks/taifex_futures 2023 2024 2025
python scripts/experiments/tick_close_window.py <QUANTDATA>/silver/ticks/taifex_futures 2023 2024 2025
VVS_NAN_GUARD=1 python scripts/experiments/slippage_compare.py strategies/xsmom_stkfut_rmt vvs_default out/x.json
```

tick 腳本需 `pyarrow`、`pandas`；`partitioning=None` 是因為 hive 路徑 `year=...` 會與檔內的 `year` 欄位型別衝突。
