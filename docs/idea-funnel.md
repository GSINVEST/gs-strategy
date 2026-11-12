# 策略 idea 漏斗

把論文／RSS／GitHub 的文字收進來，經機械分診與形式化檢查，每批最多 20 個單一期貨 FSM 策略進入回測登記。
程式在 `quant_crawler/ideas/`，卡片放 `ideas/<id>.md`，暫存的 Gslang／IR／策略碼放 `ideas/_work/<id>/`。

## 漏斗圖

```
S0 收集   papers.db（arXiv 等既有爬蟲）＋ crawlers/rss.py ＋ crawlers/github_search.py（RSS／GitHub 預設停用）
   ↓
S1 抽取   LLM 抽成 idea 卡；hypothesis／mechanism 必須有「逐字引用」才保留，否則清空            [extract]
   ↓
S2 分診   機械規則（不看績效）：無假說→drop；簽章重複→drop；非單一期貨／Gslang 表達不了→defer   [triage]
   ↓
S3 FSM    LLM 草稿 Gslang → 編譯 → TLC 模型檢查，寫 verdict；只有 pass_with_bounds 往下         [fsm]
   ↓
S4 實作   Gslang codegen 產 Python strategy.py（暫存 ideas/_work/<id>/）；通過者另可產 Rust 做 parity
   ↓
S5 登記   select_batch 取 ≤20 個、register_batch 只追加登記 ledger／preregistration            [batch --register]
   ↓      （人跑回測 → 卡片轉 bt_done）
   入庫   admit：寫 bundle、跑 check_preregistration＋validate_dashboard_bundle、通過才進索引    [admit]
```

卡片狀態只能往前走：`new → triaged → fsm → impl → bt_registered → bt_done → admitted`；`defer`／`drop`／`admitted` 為終點。

## 子命令

全部以 `python -m quant_crawler.ideas <子命令>` 執行（repo 根目錄）。

| 子命令 | 作用 | 需要 |
|---|---|---|
| `extract --since YYYY-MM-DD [--limit N] [--source S]` | 從 `data/papers.db` 取論文抽成卡，既有 id 跳過 | `GDECK_LLM_*`；缺少時明確報錯（exit 2） |
| `triage` | 對 `status=new` 的卡分診並存回 | 無 |
| `fsm [--root GDECK_FORMAL]` | 對 `triaged` 卡草稿→編譯→TLC；只有 `pass_with_bounds` 進 `impl` 並產 `strategy.py` | `GDECK_LLM_*`、Gslang 快照、Java（TLC） |
| `batch [--register]` | 沒有 `--register` 只印候選（dry-run）；加了才寫登記。有 `--register` 但沒有候選時 **exit 4**、不登記 | 無 |
| `backtest <id>` | **不代跑回測**。沒有 `TEJAPI_KEY` 時印「回測未實跑」並 exit 3；有 key 也只提示手動執行 `scripts/run_strategy.sh <id>` | `TEJAPI_KEY`、已 ingest 的 `tquant_future` |
| `admit <id> --bt-result PATH` | 入庫閘門，見下 | 卡片為 `bt_done`、回測結果檔 |
| `report` | 各狀態數量與 defer 原因統計 | 無 |

`run_all(cards_dir, known_signatures)`（`cli.py`）是 triage 迴圈的純函式版，供測試使用。

結束碼：0 成功；1 作業失敗或入庫被拒；2 用法／環境錯誤（缺 `GDECK_LLM_*`、缺 Gslang 快照、id 或 `--since` 格式錯）；3 回測未實跑；4 `batch --register` 沒有候選。

`fsm` 逐卡處理：單卡失敗（LLM、編譯、TLC、codegen）會把「階段＋原因」寫進該卡 `reason` 並印到 stderr，然後繼續下一張，最後以 exit 1 結束；TLC 暫存目錄用完即清。`extract --since` 先依日期過濾再套 `--limit`，格式必須是 `YYYY-MM-DD`。

## 環境變數

| 變數 | 用途 |
|---|---|
| `GDECK_FORMAL` | Gslang 快照根目錄（預設 `/home/kevin/gdeck-formal`）；`fsm` 與相關測試用 |
| `GDECK_LLM_URL`／`GDECK_LLM_KEY`／`GDECK_LLM_MODEL` | OpenAI 相容端點；金鑰只讀環境變數，不寫入 log 或卡片 |
| `GITHUB_TOKEN` | GitHub 搜尋爬蟲；只放 header，未帶時 API 額度很低 |
| `TEJAPI_KEY` | 回測資料；本漏斗的 dry-run 與測試完全不使用 |

## 每批上限與選取規則

- 每批最多 **20** 個（`MAX_BATCH`），超過即拒絕。原因：Deflated Sharpe 門檻隨試驗數 N 上升，且影響所有既有策略。
- 選取規則：`status==impl` 且 `verdict==pass_with_bounds`，依 `sha256(card.id)` 排序取前 N 個。**完全不看績效**。
- 登記只追加：`log/trials.jsonl`、`experiments/preregistration/` 既有內容不動；寫入前先全數驗證，任何失敗都不留半成品。
  同一天第二批必須明確指定 `batch_id`。

## Gslang v0.1 不支援清單（defer 的由來）

分診時若假說文字命中下列任一項，卡片標 `defer`（待語言擴充，不是 drop）：

- 多標的／截面／配對／排名
- 停損、停利、移動停損
- 加碼、減碼、分批
- 盤中、tick、限價、委託簿（只支援日頻、收盤決策、下一根開盤成交）
- 交叉、斜率、歷史引用（沒有歷史引用語法）
- 參數優化／網格搜尋（參數由人給定）

另有市場限制：只收**單一期貨**（TX／MTX 等）。個股現貨會 defer。

## TLC PASS 的免責聲明

`pass_with_bounds` 只代表「**在示範參數的有限模型內，TLC 未發現違反**」。它不代表策略有獲利、不代表統計顯著、
不代表在真實參數或真實市場成立。示範參數會被標記為 demo；下游判斷是否通過必須嚴格等於 `pass_with_bounds`。
卡片、bundle README 與 manifest 都帶有同樣的免責文字。

## Rust v1 範圍與已知限制

- 支援 `CLOSE@1`／`SMA@1`／`EMA@1` 與 `zone_cmp` 訊號；其餘拋 `RustUnsupported`。
- 不含換月、成本、保證金；語意與 Python 參考模擬器（`ir_sim.simulate`）對齊，以逐筆成交 parity 測試驗證。
- SMA 在 Rust 與 Python 的浮點加總順序可能不同，極端邊界值（比較兩者恰好相等附近）可能出現差一個 ulp 的分歧；
  parity 測試涵蓋的資料以外未保證逐位元一致。沒有 `cargo` 時 parity 測試 skip。

## preregistration 需人填

`batch --register` 產生的 `experiments/preregistration/<batch_id>.yaml` 是**草稿**，下列欄位刻意留空，
**未填完 `scripts/check_preregistration.py` 不會通過，`admit` 也會因此拒絕入庫**：

- `author`、`hypothesis`（方向要先押定）、`economic_mechanism`
- `stat_plan.holding_periods`（`overlap=overlapping` 時必填；沒有合法預設值，不捏造）
- `sample.start`／`end`／`contains_full_bear_market`／`holdout`
- `thresholds.primary_metric`／`pass_condition`
- `falsification`（至少一條事先寫下的放棄條件）

## 入庫閘門（admit）

`admit` 的順序：檢查卡片 `bt_done`、verdict、回測結果檔存在且非空 → 寫入 bundle →
**實際執行** `check_preregistration.py`（批次登記檔）與 `validate_dashboard_bundle.py`（剛寫的 bundle）→
任一失敗（含驗證腳本逾時 120 秒、無法執行，一律視為未通過）即移除**自己剛建立**的 bundle 並回報原因（不更新索引、卡片維持 `bt_done`）。
`strategies/<id>` 已存在時一律拒絕且不刪除既有目錄；`card.id` 不合法、`write_bundle` 失敗也轉成拒絕原因（exit 1），不丟 traceback。
全過後先寫 `strategies/INDEX.yaml`，再把卡片存成 `admitted`。

**失敗模式：索引已更新但存卡失敗。** 此時不回滾（索引已記載），CLI 會明確印出「索引已更新、bundle 已入庫，但存卡失敗……卡片需手動設為 admitted」。
人手把該卡 `status` 改成 `admitted` 即可；若直接重跑 `admit`，會得到「已入庫過（bundle 與索引都已有）」的可辨識訊息，不會重複寫入。

## 已知未驗證項

- 真實 LLM 抽取與 Gslang 草稿品質未實測（測試用假 LLM）；無 `GDECK_LLM_*` 時 CLI 不會假裝成功。
- 沒有以 `TEJAPI_KEY` 實跑過任何一個漏斗產物的 S5 回測；「e2e」測試實際涵蓋登記與入庫閘門（tmp 目錄）；S1→S4 由各單元測試與 Rust parity 覆蓋，沒有以真實 LLM／TLC 從頭串到尾。
- RSS／GitHub 爬蟲預設停用，feed URL 需人確認；未對真實網路實測。
- Rust parity 在沒有 `cargo` 的環境會 skip。
- 入庫索引採新增 `strategies/INDEX.yaml`；gs-zipline-tej dashboard 的匯入規格若要求其他索引位置，入庫前需調整 `update_index`。
- `bt_registered → bt_done` 的轉換由人跑完回測後手動執行（`set_status`），CLI 尚無對應子命令。
