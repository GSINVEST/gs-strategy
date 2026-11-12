# 策略 idea 漏斗

把論文／RSS／GitHub 的文字收進來，經機械分診與形式化檢查，每批最多 20 個單一期貨 FSM 策略進入回測登記。
程式在 `quant_crawler/ideas/`，卡片放 `ideas/<id>.md`，暫存的 Gslang／IR／策略碼／staging bundle 放 `ideas/_work/<id>/`
（`ideas/_work/` 與 `strategies/.*.tmp-*` 已列入 `.gitignore`；卡片內記錄的路徑一律相對 repo 根目錄）。

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
S4 實作   Gslang codegen 產 Python strategy.py（暫存 ideas/_work/<id>/）
   ↓
S5 登記   選 ≤20 個 → 寫 staging bundle → 登記 ledger（記 bundle sha256）＋ preregistration 草稿  [batch --register]
   ↓      （人填 prereg → 人用 runner 跑回測 → 人把卡片轉 bt_done）
   入庫   admit：核對 sha、複製 bundle、跑 check_preregistration＋validate_dashboard_bundle、才進索引 [admit]
```

卡片狀態只能往前走：`new → triaged → fsm → impl → bt_registered → bt_done → admitted`；`defer`／`drop`／`admitted` 為終點。

## S5 操作流程（從 impl 到入庫）

1. `python -m quant_crawler.ideas batch` 看候選（dry-run；已在 ledger 的卡不會再被選入）。
2. `python -m quant_crawler.ideas batch --register --start YYYY-MM-DD --end YYYY-MM-DD [--batch-id ID]`：
   - 對每個候選寫 **staging bundle** `ideas/_work/<id>/bundle/`（`strategy.py`、`config.yaml`、`manifest.yaml`、vendored `gslang/`）；
   - 回測起訖由 `--start/--end` 一次押定，同時寫進 prereg 草稿的 `sample.start/end` 與 bundle 的 `config.yaml`（不再寫死）；
   - bundle 根目錄含 `futures_setup.py`（自 `strategies/_common/` 複製；產生的策略會 `from futures_setup import ...`，`runner` 與 `validate_dashboard_bundle` 的 `sys.path` 不同，必須自含）；
   - `config.yaml` 的 `validation.n_trials` 寫入登記當下的 N（與 ledger 該卡 `decision_path.n_trials` 同口徑；若登記時有其他行程同時追加，兩者可能差一個批次內的順位，以 ledger 為準）；
   - 登記 ledger，各 trial 的 `config_sha256` 是 staging bundle 的內容雜湊（對所有檔案以「長度前綴＋相對路徑＋長度前綴＋內容」依路徑排序計算，排除 `__pycache__`/`*.pyc`）；
   - bundle 內有任何 symlink（檔或目錄）即拒絕 staging 與入庫複製。
3. 人填 `experiments/preregistration/<batch_id>.yaml`（見下節）。
4. 有 `TEJAPI_KEY` 與已 ingest 的 `tquant_future` 時，`python -m quant_crawler.ideas backtest <id>` **只印出**確切指令（不代跑）：

   ```
   .venv-bt/bin/python strategies/_common/runner.py --strategy ideas/_work/<id>/bundle/strategy.py \
       --config ideas/_work/<id>/bundle/config.yaml --output results/<id>.pkl \
       && sha256sum results/<id>.pkl > results/<id>.pkl.sha256
   ```

   runner 會把 bundle 目錄放進 `sys.path`，所以 vendored `gslang/` 可 import。沒有 `TEJAPI_KEY` 時此子命令報「回測未實跑」並 exit 3。
   需要依 prereg 的 sample 重建 staging bundle 時用 `backtest <id> --stage`（不需要 key）；重建後內容雜湊若與登記不同，入庫會被拒絕。
   **路徑與環境**：指令的相對路徑要在含 `.venv-bt` 的主 checkout 根目錄執行（worktree 沒有 `.venv-bt`）。
   **已承認限制**：staging 內的 `__pycache__`／pyc 不在雜湊內，無法證明回測實際執行的程式碼＝被雜湊的程式碼。
5. 回測完成後由人把卡片 `status` 設為 `bt_done`（CLI 尚無對應子命令），再執行 `admit <id> --bt-result results/<id>.pkl`。

## 子命令

全部以 `python -m quant_crawler.ideas <子命令>` 執行（repo 根目錄）。

| 子命令 | 作用 | 需要 |
|---|---|---|
| `extract --since YYYY-MM-DD [--limit N] [--source S]` | 從 `data/papers.db` 取論文抽成卡；已有卡的論文先跳過（不呼叫 LLM） | `GDECK_LLM_*`；缺少時明確報錯（exit 2） |
| `triage` | 對 `status=new` 的卡分診並存回 | 無 |
| `fsm [--root GDECK_FORMAL]` | 對 `triaged` 卡草稿→編譯→TLC；只有 `pass_with_bounds` 進 `impl` 並產 `strategy.py` | `GDECK_LLM_*`、Gslang 快照、Java（TLC） |
| `batch [--register --start S --end E] [--batch-id ID]` | 沒有 `--register` 只印候選（dry-run）；加了才 staging＋登記。`--batch-id` 須符合 `^[a-z0-9][a-z0-9_-]{0,63}$` | 無 |
| `backtest <id> [--stage]` | **不代跑回測**，見上節 | `TEJAPI_KEY`（`--stage` 不需要） |
| `admit <id> --bt-result PATH` | 入庫閘門，見下 | 卡片為 `bt_done`、回測結果檔＋旁附 `.sha256` |
| `report` | 各狀態數量與 defer 原因統計 | 無 |

`run_all(cards_dir, known_signatures)`（`cli.py`）是 triage 迴圈的純函式版，供測試使用。

結束碼：0 成功；1 作業失敗或入庫被拒（含 `batch --register` 登記失敗）；2 用法／環境錯誤（缺 `GDECK_LLM_*`、缺 Gslang 快照、id／`--since`／`--batch-id`／`--start`／`--end` 格式錯）；3 回測未實跑；4 `batch --register` 沒有候選。

`fsm` 與 `extract` 逐筆處理：單筆失敗不中斷整批，結束時 exit 1。失敗訊息（stderr 與卡片 `reason`）只記「階段＋例外型別（＋HTTP 狀態碼）」，
**不記 `str(e)`**，避免 URL、金鑰外洩。TLC 暫存目錄用完即清，TLC 子行程只拿 `PATH/HOME/JAVA_HOME/LANG/GDECK_FORMAL` 等必要環境變數
（不含 `GDECK_LLM_KEY`、`GITHUB_TOKEN`、`TEJAPI_KEY`）。`extract --since` 先依日期過濾再套 `--limit`。

card id 規則：`^[a-z0-9][a-z0-9_]{0,63}$`；由來源名稱產生時過長截 55 字加 `_`＋雜湊 8 碼，全非 ASCII 時改用雜湊，不會因為過長而讓整個流程當掉。

## 環境變數

| 變數 | 用途 |
|---|---|
| `GDECK_FORMAL` | Gslang 快照根目錄（預設 `/home/kevin/gdeck-formal`）；`fsm` 與相關測試用 |
| `GDECK_LLM_URL`／`GDECK_LLM_KEY`／`GDECK_LLM_MODEL` | OpenAI 相容端點；金鑰只讀環境變數，不寫入 log 或卡片 |
| `GITHUB_TOKEN` | GitHub 搜尋爬蟲；只放 header，未帶時 API 額度很低 |
| `TEJAPI_KEY` | 回測資料；本漏斗的 dry-run 與測試完全不使用 |

## 每批上限與選取規則

- 每批最多 **20** 個（`MAX_BATCH`），超過即拒絕。原因：Deflated Sharpe 門檻隨試驗數 N 上升，且影響所有既有策略。
- 選取規則：`status==impl` 且 `verdict==pass_with_bounds`，依 `sha256(card.id)` 排序取前 N 個，**完全不看績效**；ledger 內已有 `t-idea-<id>` 的卡不會再被選入
  （若卡片仍是 `impl`，會警告需手動設為 `bt_registered`）。
- 登記只追加：`log/trials.jsonl`、`experiments/preregistration/` 既有內容不動；寫入前先全數驗證，任何失敗都不留半成品。
  「判重＋三個寫入」包在與 `strategies/_common/graph/ledger.py` 相同協定的排他鎖（`log/trials.jsonl.lock`）內，與圖引擎、其他登記行程互斥。
  同一天第二批必須明確指定 `--batch-id`；自訂 id 若去掉 `idea-` 前綴後符合 card id 規則（例如 `idea-bar`），
  會與卡 `bar` 的 `t-idea-bar` 撞名而被拒絕（預設 `idea-batch-<日期>` 含連字號，不受影響）。

### ledger 行的形狀與審計

登記時的 trial 沿用 `graph/ledger.py` 的 pending 慣例：`delta_n=1` 先計入 N（`n_selection_recomputed` 會上升），但 `stat_decision` 為
`status: pending`、`rule_id: null`、`inference_available: false`，`decision_path` 只含已宣告事實（estimand／design／overlap／family／purpose／n_trials），
**不捏造 `n_eff`**，並帶 `preregistration_id: <batch_id>`；頂層 `purpose: selection`。
**審計副作用（需 PR 前與 owner 決定）**：`decision.audit_ledger` 對**每一行**跑 `audit_record`，pending 行因缺 `n_eff` **永久**回報一筆問題，
而且追加完成行也不會讓它消失（`audit_ledger` 逐行檢查，舊的 pending 行仍在；完成行本身還可能多報缺 `holding_periods`）。
受影響：`quant_crawler/webui/stat_tree.py` 的 `audit_summary()`（`auditable` 會翻成 False、`n_problems` 每批增加批次大小）與 `stat-tree.html` 徽章；
`graph/ledger.py` 的 pending 行有同樣慣例，同樣會被報。本分支**不改** `strategies/_common/validation/`。
PR 前需與 owner 決定：另案讓 `audit_record` 認得 pending 行，或接受審計徽章因本漏斗而變紅。

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
- **Rust 不在漏斗的入庫路徑上**：v1 入庫的是 Python 版 bundle；`admit` 不檢查 Rust parity，也不產生或入庫任何 Rust 產物。

## preregistration 需人填

`batch --register` 產生的 `experiments/preregistration/<batch_id>.yaml` 是**草稿**，下列欄位刻意留空，
**未填完 `scripts/check_preregistration.py` 不會通過，`admit` 也會因此拒絕入庫**：

- `author`、`hypothesis`（方向要先押定）、`economic_mechanism`
- `stat_plan.holding_periods`（`overlap=overlapping` 時必填；沒有合法預設值，不捏造）
- `sample.contains_full_bear_market`／`holdout`（`sample.start/end` 已由 `--start/--end` 寫入，改動它會讓 `admit` 因與 bundle 起訖不一致而拒絕）
- `thresholds.primary_metric`／`pass_condition`
- `falsification`（至少一條事先寫下的放棄條件）

**填完之前不得 commit 這份草稿**：`experiments/preregistration/README.md` 規定一旦 commit 就凍結（不可修改、刪除、改名），
把空白草稿 commit 進去就再也補不了。另外檔名 `<batch_id>.yaml`（預設 `idea-batch-<日期>`）不符合該 README 規則 1 的 `NNN-<slug>.yaml` 命名；
`check_preregistration.py` 只要求 `id` 與檔名一致，故可通過，但要符合慣例時請在人工簽核時用 `--batch-id NNN-<slug>` 重新登記（登記只追加，無法改名）。

## 入庫閘門（admit）

內容唯一來源是登記時的 staging bundle。順序：

1. 預檢：卡片 `bt_done`、verdict 為 `pass_with_bounds`、回測結果檔**必須是 `results/<id>.pkl`**、存在且非空，**且旁附 `<結果檔>.sha256` 與內容相符**。
2. 讀 ledger 內該卡登記的 `config_sha256`，重算 staging bundle 雜湊，不符即拒絕（登記後被改過）。
3. **實際執行** `check_preregistration.py`（批次登記檔）；核對 bundle 的回測起訖與 prereg 的 `sample.start/end` 一致。
4. 把 staging bundle 複製（暫存目錄＋rename，複製後再比對雜湊）到 `strategies/<id>`，再**實際執行** `validate_dashboard_bundle.py`。
5. 任一失敗（含驗證腳本逾時 120 秒、無法執行，一律視為未通過）即移除**自己剛建立**的 bundle 並回報原因（不更新索引、卡片維持 `bt_done`）。
   `strategies/<id>` 已存在時一律拒絕且不刪除既有目錄；`card.id` 不合法、複製失敗也轉成拒絕原因（exit 1），不丟 traceback。
6. 全過後先寫 `strategies/INDEX.yaml`（entry 含 `result_sha256`，即結果檔雜湊），再把卡片存成 `admitted`。

**驗證器環境**：`validate_dashboard_bundle.py` 會 import 策略（進而 import `zipline`），需在裝有 zipline 的 `.venv` 內執行 `admit`，環境可能還要求 `TEJAPI_KEY`；否則驗證失敗，入庫依 fail-closed 被拒。

**回測結果檔的驗證限制**：runner 輸出的是 pandas pickle。不跑回測就無法驗證其內部結構，也不應對不受信的 pickle 反序列化，
所以 `admit` 只檢查「存在、非空、與旁附 sha256 一致」，防的是誤放或截斷的檔案，**不能防偽造**；結果內容是否真的對應這個 bundle 需人工核對。

**失敗模式：索引已更新但存卡失敗。** 此時不回滾（索引已記載），CLI 會明確印出「索引已更新、bundle 已入庫，但存卡失敗……卡片需手動設為 admitted」。
人手把該卡 `status` 改成 `admitted` 即可；若直接重跑 `admit`，會得到「已入庫過（bundle 與索引都已有）」的可辨識訊息，不會重複寫入。

新建的 bundle 與 `INDEX.yaml` 的權限依 umask 還原（目錄 0o777&~umask、檔案 0o666&~umask，可執行位保留；既有 INDEX 的 mode 保留），
避免 `mkdtemp`／`mkstemp` 的 0o700／0o600 讓其他使用者讀不到。

## 已知未驗證項

- **TLC 從未用真實 Java 跑過**：所有測試以假的 `fsm_atlas` 輸出驗證判定邏輯。
- **S1→S4 從未以真實 LLM 串過**：抽取與 Gslang 草稿以假 LLM 測試；無 `GDECK_LLM_*` 時 CLI 不會假裝成功。
- **沒有跑過任何一個漏斗產物的回測**（S5 只做到登記與入庫閘門）；「e2e」測試涵蓋登記與入庫閘門（tmp 目錄），
  S1→S4 由各單元測試與 Rust parity 覆蓋，沒有從頭串到尾。
- staging bundle 能否被 runner 在真實 `tquant_future` 環境下 import 並跑完，未實測（只驗證 `validate_dashboard_bundle` 的 import 檢查）。
- RSS／GitHub 爬蟲預設停用，feed URL 需人確認；未對真實網路實測。
- Rust parity 在沒有 `cargo` 的環境會 skip。
- 入庫索引採新增 `strategies/INDEX.yaml`；gs-zipline-tej dashboard 的匯入規格若要求其他索引位置，入庫前需調整 `update_index`。
- `bt_registered → bt_done` 的轉換由人跑完回測後手動執行（`set_status`），CLI 尚無對應子命令。
