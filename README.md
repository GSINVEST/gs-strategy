# quant_crawler — 期貨量化研究論文爬蟲

實驗用 sandbox 內的小型爬蟲系統，蒐集**最新且具公信力**的期貨/量化交易研究論文與報告。
不靠任何 SaaS / API key，純 Python + stdlib + `requests` + `feedparser` + `bs4`。

## 涵蓋的來源

| Tier | source key | 介面 | 內容 |
|------|------------|------|------|
| ✅ S | `arxiv`     | Atom API   | q-fin.TR / PM / ST / CP / RM 最新 preprint |
| ✅ S | `nber`      | RSS        | NBER Working Papers（每週新論文） |
| ✅ S | `repec`     | NEP HTML   | nep-fmk (Financial Markets) / nep-rmg (Risk Mgmt) / nep-mst (Microstructure) / nep-inv |
| ✅ A | `fed_feds`  | RSS        | Federal Reserve FEDS Notes / Working Papers |
| ✅ A | `wiley`     | RSS        | *Journal of Futures Markets* TOC（核心期刊） |
| ✅ B | `aqr`       | HTML       | AQR Capital Insights/Research（publisher-curated，全部收） |
| ❌ — | `ssrn`      | (停用)     | Cloudflare anti-bot；需 Playwright 才能繞過 |
| ❌ — | `cme`       | (停用)     | 同上 |
| ❌ — | `man_ahl`   | (停用)     | JS-rendered |

關於停用來源的決策過程，見 `docs/EXPERIMENT_LOG.md`。

## 安裝

跨平台一鍵啟動（Linux / macOS / WSL 用 `run.sh`，Windows 用 `run.ps1`）：

```bash
./run.sh test             # pytest (執行所有測試，包含 P0 案例)
```


```powershell
# Windows
.\run.ps1                 # 同上（子指令一致）
```

或手動：

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install -r requirements-rag.txt   # 可選：啟用 RAG/MCP
```

## 用法

```bash
# 跑所有 enabled 來源並把摘要追加到 EXPERIMENT_LOG.md
.venv/bin/quant-crawl run --log-run

# 只跑特定來源
.venv/bin/quant-crawl run -s arxiv -s wiley

# 包含已停用的來源（會嘗試但通常失敗）
.venv/bin/quant-crawl run --include-disabled

# 列出最新 20 篇
.venv/bin/quant-crawl list -n 20

# 只看 wiley 來源
.venv/bin/quant-crawl list -s wiley -n 10

# JSON 輸出（給下游程式吃）
.venv/bin/quant-crawl list -n 50 --json

# 標題/摘要全文搜尋
.venv/bin/quant-crawl search "trend following"

# 統計
.venv/bin/quant-crawl stats

# 看 source 設定
.venv/bin/quant-crawl sources
```

## 管理介面 (web UI)

本地網頁管理面板，彙整爬蟲 + 策略產生 pipeline 狀態。零外部依賴（stdlib
`http.server`），唯讀（不寫 DB、不改策略檔）。

```bash
./scripts/run_webui.sh                 # http://127.0.0.1:5057
./scripts/run_webui.sh --port 6060     # 或自訂 port
PORT=6060 ./scripts/run_webui.sh
```

> **改碼後要重啟**：`http.server` 不會 hot-reload Python，改了 `server.py`/
> `stats.py` 後必須重啟 server 才生效（靜態檔 index.html/app.js 則即時讀）。
> `run_webui.sh` 啟動前會自動停掉同 port 的舊 server；footer 也會顯示
> 「server 啟動於 … · code <git hash>」讓你一眼看出跑的是不是最新版。

> **緊湊版面**：寬螢幕（≥1180px）採 2 欄 grid，全部 panel 約 2.5× viewport
> 即可看完；長表格（papers / strategies / RAG）內部捲動 + 黏頂表頭。窄螢幕
> 自動 fallback 單欄。設計記錄見 `docs/progress-layout-compact.md`。

面板顯示：

- **論文/報告總量** + 各來源分布（arxiv / wiley / aqr / repec / nber / fed_feds）
- **爬蟲 routine 執行紀錄**（可選日期，來自 `crawl_runs` 表：起訖、抓取/保留數、錯誤）
- **新增資料**（指定日 fetched 的論文；無則 fallback 顯示最近 N 筆）
- **策略清單**：`strategies/`（手寫）+ `strategies/_generated/`（自動產生）的
  id / 來源 / 模板 / 標籤 / 待審
- **匯出狀態**：每支策略是否已匯出到 `~/gs-zipline-tej/strategies/<id>/`
  （可用 `ZIPLINE_TEJ_STRATEGIES_DIR` 覆寫目標路徑）
- **檔案超連結**：論文列連到 PDF（優先本地 `data/pdfs/<slug>.pdf`，否則遠端
  `pdf_url`）；策略列連到 spec markdown（`README.md`）與 `manifest.yaml`，
  皆由 webui 經 `/files/*` 路由 serve（path-traversal + 副檔名白名單防護）。
  本地 PDF 連結即時反映 `data/pdfs/`（無需重建）。
- **Strategy / Factor 雙 panel**：論文區分「策略」「因子」兩個 tab 切換查詢
  （依 `quant_crawler/paper_class.py` 自動分類 strategy vs factor）。每個 tab
  可依**子類別**篩選（factor: value/size/momentum/quality/… ；strategy:
  trend-following/mean-reversion/pairs-trading/…），子類別自動偵測。
- **手動標籤**：每篇論文可在 dashboard 手動新增/移除子類別標籤、或覆寫 kind
  （修正誤判）。手動標註存 `paper_labels` 表，**re-crawl 不會被洗掉**。
  寫入走 `POST /api/labels`（dashboard 因此具備有限寫入能力）。
- **手動批次上傳 PDF**：透過瀏覽器 file selector 一次選多個 PDF 上傳，存進
  `data/pdfs/` 並在 `papers.db` 建 `source='manual'` 的論文 row，立即出現在
  論文清單、可被 `rag-ingest` 索引。走 `POST /api/upload`（multipart）；
  單檔上限 50 MB、驗 `.pdf` 副檔名 + `%PDF` magic bytes。中文檔名正確處理
  （UTF-8 還原，不亂碼）。上傳時「歸類到」可選**策略 / 因子 / 自動分類**，
  非自動時寫 `paper_labels` kind override，論文直接進對應 tab。
  > 既有亂碼標題（舊版上傳留下的）一次性修復：
  > `.venv/bin/python scripts/fix_manual_titles.py --apply`（預設 dry-run）。

JSON API（同一 server）：`/api/summary`、`/api/runs?date=`、`/api/papers?date=`、
`/api/strategies`、`/api/dates`；寫入：`POST /api/labels`、`POST /api/upload`
（multipart 批次 PDF）；檔案：`/files/pdf/<name>`、`/files/strategy/<id>/<file>`。
設計記錄見 `docs/progress-webui.md`、`docs/progress-pdf-and-schedule.md`、
`docs/progress-pdf-upload.md`。

### 每日排程 + PDF 下載

```bash
# 手動下載尚未抓的 PDF 到 data/pdfs/
.venv/bin/quant-crawl fetch-pdfs            # 全部；或 -s arxiv -n 5 限量

# 安裝每日排程（crawl → fetch-pdfs → strategy_gen → validate）
./scripts/install_daily_refresh.sh                       # 預覽 cron 行
./scripts/install_daily_refresh.sh --apply --schedule "30 6 * * *"
./scripts/install_daily_refresh.sh --uninstall           # 移除
```

`daily_refresh.sh` 四階段：(1) `quant-crawl run` 更新 papers.db →
(2) `fetch-pdfs` 下載新 PDF → (3) `strategy_gen` 產生 skeleton bundle →
(4) validator。log 在 `data/logs/daily_refresh_<date>.log`。

## RAG + MCP（讓 Claude 取原文公式生成忠實 spec）

把下載的 PDF 全文索引進可檢索 store，透過 MCP 讓 Claude 在生成 strategy/factor
spec 時取回**原始論文 context**（正確數學公式、參數定義），避免憑記憶杜撰。

技術棧（純 Python、不靠 SaaS）：`pypdf` 抽文字 + **SQLite FTS5**（BM25 檢索，
與 `papers.db` 同庫）+ **FastMCP** server。需額外安裝：

```bash
.venv/bin/pip install pypdf mcp        # 或見 requirements-rag.txt
```

用法：

```bash
.venv/bin/quant-crawl rag-ingest               # 抽 data/pdfs/*.pdf → chunk → FTS5
.venv/bin/quant-crawl rag-stats --list          # 索引統計 / 列出已索引論文
.venv/bin/quant-crawl rag-search "cubic momentum critical threshold" --kind strategy
```

MCP server（Claude Code 透過根目錄 `.mcp.json` 自動掛載 `gs-strategy-rag`）暴露：

| MCP tool | 用途 |
|---|---|
| `search_paper_chunks(query, kind?, limit?)` | 跨全 corpus BM25 搜段落 |
| `get_paper_context(source, source_id, query)` | 在**已知論文**內取最相關段落（公式 lookup） |
| `get_paper_fulltext(source, source_id)` | 取某論文全文 |
| `list_indexed_papers(kind?)` / `rag_stats()` | 列出已索引 / 索引統計 |

自動產生的 bundle README 會**直接內嵌**從 RAG store 取回的 top 段落（依
strategy/factor 模板下 targeted query），讓 reviewer/Claude 不必再多跑一次
MCP 就能對照原文公式；同時保留 `get_paper_context(...)` 提示供進一步查詢。
未索引的論文則 fallback 顯示 `rag-ingest` 指引。
`daily_refresh.sh` 在 fetch-pdfs 後自動 `rag-ingest`，閉環：
crawl → fetch-pdfs → rag-ingest → strategy_gen。

設計記錄見 `docs/progress-rag-mcp.md`、`docs/progress-generator-rag-context.md`。

### Dashboard 內瀏覽 RAG / MCP

webui（`./scripts/run_webui.sh`）多了兩個面板，不會 SQL 也能用：
- **RAG 全文檢索**：關鍵字搜原文（BM25）、依 strategy/factor 篩選、點論文看
  chunks/全文。對應 `/api/rag/{stats,search,paper}`。
- **MCP server**：顯示 `gs-strategy-rag` 設定、transport、暴露的 tool 清單、
  索引健康、運行偵測（stdio 無常駐 process 時標示由 client 按需啟動）。
  對應 `/api/mcp/info`。

## 架構

```
quant_crawler/
├── config.py                # SourceConfig + 關鍵字清單 + 路徑
├── cli.py                   # argparse subcommands
├── orchestrator.py          # 註冊 + 順序執行 + EXPERIMENT_LOG 追加
├── crawlers/
│   ├── base.py              # BaseCrawler（共用 run loop + relevance filter）
│   ├── arxiv.py / nber.py / repec.py / fed.py / wiley.py / aqr.py / ssrn.py
├── storage/
│   ├── models.py            # PaperRecord dataclass
│   └── db.py                # SQLite (papers.db) — primary key (source, source_id)
└── utils/
    ├── http.py              # RateLimitedSession：per-host delay + retry/backoff
    ├── logging.py           # console + rotating file handler
    └── text.py              # relevance regex / hash / normalize
```

### 設計重點

- **禮貌爬蟲**：每個 source 有 `min_delay`，HTTP wrapper 以 host 為粒度執行最小延遲；429/503 退避重試
- **去重**：`(source, source_id)` 是 SQLite 主鍵，重跑只更新不重複
- **相關性過濾**：`config.RELEVANCE_REGEX` 涵蓋 futures/momentum/carry/term-structure/managed futures/CTA/stat arb 等
- **publisher-curated 旁路**：AQR 的研究本來就是篩過的，`bypass_relevance=True` 不再過濾
- **失敗隔離**：`BaseCrawler.run` 整個 wrap 在 try/except，單一 source 出錯不會中斷其他

## autogo dashboard test-plans

本 repo 提供符合 autogo `Source repo test plans` import contract 的測試計畫，
讓 autogo dashboard `/plans` 一鍵跑（spawn Claude / Playwright，自動收 video /
trace / step screenshots / summary）。詳見 [`test-plans/README.md`](test-plans/README.md)。

```bash
# 列出本 repo 所有 plans + 狀態
.venv/bin/python scripts/test_plan_format.py list

# 新增一支 fuzzy plan stub
.venv/bin/python scripts/test_plan_format.py new 003-my-plan \
    --title "我的 plan" --runner playwright-mcp

# 驗證所有 plans 是否符合 autogo schema（autogo refresh 之前的 self-check）
.venv/bin/python scripts/test_plan_format.py validate --all
```

設計記錄見 `docs/progress-test-plans.md`。

## 開發

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/ -v
```

## 已知限制

1. **SSRN/CME/Man Group**：Cloudflare 或 JS-render 阻擋，需要 Playwright 才能繞過
2. **PDF 下載**：預設關閉（`SourceConfig.download_pdfs=False`），只抓 metadata
3. **NBER 作者解析**：用簡單字串切分；偶爾會被多 dash 干擾
4. **AQR / Wiley 不抓 abstract**：listing/RSS 只給標題；要 follow-up 個別抓

## 下一步

- 接 Playwright 重新啟用 SSRN
- arXiv 加 incremental fetch（last-published 後才抓）
- 加 BIS / IMF / ECB working papers（需找對 RSS）
- export to JSON Lines / Parquet 給下游 backtest 用

### Live Strategy Graph（Phase A／B）

以 CPU 執行節點圖、預覽中間資料並記錄 selection 試驗；React Flow 編輯器由同一個本機 server 提供。

```bash
# 需要 node/npm 與已安裝 requirements-test.txt 的 .venv-bt
# 第一次（或來源變動後）會 npm ci + build，再啟動同源編輯器
bash run.sh graph-ui --fixture
# 顯示 http://127.0.0.1:9102；無需 TEJ 金鑰，使用真 Zipline 合成資料

# 真實資料模式：需要本機 bundle 與日曆環境
bash run.sh graph-ui
```

PowerShell 對應 `./run.ps1 graph-ui --fixture`。可加 `--port 9103`。
fixture 模式頂列常駐 `FIXTURE 資料・獨立 ledger`；圖、版面、bundle、快取、sidecar 與 ledger
均放在每次啟動專用的系統暫存工作區，不寫正式 `log/trials.jsonl`。關閉後不保留示範資料。
fixture 回測每根 bar 加短暫可取消等待，方便展示進度與取消；orders、成交、成本與損益仍走真實引擎。

畫布依資料／特徵／訊號與部位／回測／驗證與檢定分欄，虛線標示 N 記帳邊界。
參數修改約 300ms 防抖更新上游預覽，Backtest 及下游保留舊結果並標示過期；必須按執行才會回測。
執行前顯示預計 N 變化，成功後以 ledger 更新常駐讀數。取消在回測提交前生效，已提交的試驗不能撤銷 N。
接點常駐型別文字，錯誤接線不修改圖；點節點可查看圖表、分頁原始數值與來源資訊。
存檔寫入 `graph.json`，節點拖動另存 `graph.layout.json`，不改 dirty／hash／N。
版控中的初始 layout 為空座標，依卡片實測高度自動排版；首次拖曳後保存具體座標。
「從產出載入」接受工作區相對 `.validation.json` sidecar 路徑，實作指紋不同時顯示警告。

JS、CSS 與 IBM Plex Sans／Mono、Noto Sans TC 字型經 npm 打包；執行時完全離線。
`dist/` 不入版控，來源指紋未變時啟動器跳過建置；首次安裝相依套件需要 npm 可用。
開發用 Vite proxy 連到 `127.0.0.1:9102`，驗收使用正式同源 build。

```bash
cd strategies/_common/graph/ui
npm ci
npm test
npm run build
# 首次準備瀏覽器（完成安裝後測試本身離線）
npx playwright install chromium
npm run test:e2e
```

e2e 會在 `127.0.0.1:19102` 啟動獨立 fixture 真 server，不 mock API，並斷言沒有外部瀏覽器請求。
1280×800 完成狀態截圖寫入系統暫存目錄 `live-strategy-graph-fixture-1280x800.png`。
完整 API 與版面契約見 [規格附錄 B](docs/spec/live-strategy-graph.md#附錄-bphase-a-本機-http-json-api)。

```bash
# Windows 請用 Git Bash；Linux 可用 python3.11
bash scripts/setup-bt.sh python
bash run.sh graph-api --port 9102
curl http://127.0.0.1:9102/api/node-types

# 需已 ingest 的 tquant_future bundle；預覽不跑回測、不增加 N
bash run.sh graph-run --graph strategies/tsmom_tx_mtx/graph.json --preview
bash run.sh graph-run --graph strategies/tsmom_tx_mtx/graph.json
```

PowerShell 對應 `./run.ps1 graph-api --port 9102`、`./run.ps1 graph-run --graph strategies/tsmom_tx_mtx/graph.json`。API 預設只綁 `127.0.0.1`，端點與 payload 見 [規格附錄 B](docs/spec/live-strategy-graph.md#附錄-bphase-a-本機-http-json-api)。節點清單/API 啟動不需要 TEJ 金鑰；讀取真實 bundle 才需要相應資料與日曆環境。

成功回測自動追加 `log/trials.jsonl`，以 Backtest 及全部上游的快取鍵去重；只改下游統計宣告不增加 N，HTTP 無關閉開關。產出保存在 `.graph-runs/<hash>.validation.json`，trial.config_ref 指向 `.graph-runs/<backtest_key>.config.json` 的完整快照。只有明確存檔且目前圖已有成功回測時，才更新 manifest.validation。`auto`／`initial` 在每次執行副本鎖定 ingestion timestamp，不改使用者圖或 dirty；可從 sidecar 還原固定版本。統計不足的成功回測仍計 N，紀錄標為 pending，既有 audit 會要求補檢定。

測試使用暫存 ledger 與合成期貨 bundle，真實 Zipline 執行舊路徑及圖路徑比對：

```bash
.venv-bt/Scripts/python.exe -m pip install -r requirements-test.txt
PYTHONUTF8=1 .venv-bt/Scripts/python.exe -m pytest tests/ -q
# Linux 將 Scripts/python.exe 改成 bin/python
# 有真實 bundle／日曆權限的機器可另外執行：
GS_TEST_REAL_BUNDLE=1 PYTHONUTF8=1 .venv-bt/Scripts/python.exe -m pytest tests/test_live_graph_tsmom_equivalence.py -k real_tquant -q
```

離線 fixture 只替代日曆的遠端輸入，不替代 orders、成交、成本或損益事件迴圈。fixture 匯入期間會暫存並還原日曆套件的快取，請勿在同一虛擬環境並行執行 fixture 測試。

### Agent 交易員分頁（StratyUI 唯讀六畫面＋核准草稿）

策略圖頁首的「Agent 交易員」連到 `/agent-trader`，只讀 agent-trader 匯出的 UI 快照
（契約：specfeed-test `agent-trader/docs/ui-contract.md`；`latest.json`、`drafts.json`、`days/<date>.json`）。
實作在 `strategies/_common/graph/agent_trader.py`，無新增依賴。

| 環境變數 | 作用 |
|---|---|
| `AGENT_TRADER_UI_DIR` | 快照目錄（`ops export-ui` 的輸出，預設 `<state-dir>/ui`）。未設或目錄不存在 → 顯示「尚無資料」 |
| `AGENT_TRADER_APPROVE_CMD` | 核准命令前綴，須以 `ui-decide` 結尾，例：`/path/python -m agent_trader.ops ui-decide`。未設 = 核准功能關閉（預設） |
| `AGENT_TRADER_APPROVE_EXTRA_ARGS` | 附加在命令最後的固定參數，例：`--state-dir /data/state --config-dir /app/config` |
| `AGENT_TRADER_IDENTITY_HEADER` | 指名「由可信反向代理注入的已驗證身分標頭」，例：`X-Auth-Request-Email`。未設 = 無身分機制，核准關閉 |

六畫面：總覽（NAV／指數／不動作反事實，依 agent 版本分段著色，並顯示樣本天數與「統計上尚不足」警語）、
今日持倉、決策日誌、推論鏈（失效引用紅、無法驗證灰）、覆盤報告、待核准草稿。
快照 `stale`、超過 `stale_after_hours`（`AGE_EXCEEDED`）或 `schema_version` 不是 1 時，頁首顯示警示條。
所有快照值一律 HTML 跳脫；LLM 的 markdown 報告以純文字 `<pre>` 呈現，不渲染。頁面 CSP 只允許同源外部腳本。

**核准／駁回是唯一的寫入，預設關閉。** 啟用需同時設定上表前兩個變數與身分標頭，且該次請求帶有效身分：

- `--by` 一律由伺服端從身分標頭決定，請求本文的任何 `by` 都被忽略。
- 以參數陣列呼叫（無 shell）；草稿 id 須符合 `kind-<hex>`、備註不含控制字元且 ≤ 500 字，並先對照快照確認草稿存在且為 `pending_approval`。
- 名單檢查在 agent-trader 的 `ui-decide`（`config/ui.toml` 的 `approvers`，預設空 = 全拒）；被拒時回對應錯誤碼（403／404／409），不洩漏 CLI 原始輸出。
- 現況：`main` 上的圖伺服器**沒有**身分機制（`--public-host` 與 `X-Auth-Request-Email` 的處理在尚未合併的 `9f4a71a`）。
  因此預設核准端點回 503；只有部署在會剝除並重新注入該標頭的 OIDC 反向代理之後，才應設定 `AGENT_TRADER_IDENTITY_HEADER`。
  伺服器只綁 127.0.0.1，但本機其他使用者可自帶標頭偽冒身分，這是信任代理模型的已知邊界。

測試：`tests/test_agent_trader_tab.py`（渲染、空狀態、stale、跳脫、核准權限與注入防護）。

### 組合式研究 workflow：QUANTDATA + RAG → 可熱抽換 LLM → walk-forward 回放

在 Live Strategy Graph 上加了參數化接點型別、`>>`／`@` 組合 DSL，以及第一張 LLM 策略圖 `strategies/llm_view_tx`。
完整規格見 [`docs/spec/compositional-research-workflow.md`](docs/spec/compositional-research-workflow.md)。

```bash
# 離線示範：合成行情、內建研究摘要、確定性假模型，使用獨立的 ledger
bash run.sh graph-ui --fixture --graph strategies/llm_view_tx/graph.json

# 真實資料：QUANTDATA REST + data/papers.db + Genesis Deck（只放行公司出口 IP）
GS_LLM_API_KEY=... bash run.sh graph-run --graph strategies/llm_view_tx/graph.json
bash run.sh graph-ui --graph strategies/llm_view_tx/graph.json

# graph.json 由組合式產生；改節點後重產，CI 以測試確認兩者一致
python -m strategies.llm_view_tx.compose_graph
```

- **換模型**：改 `LLM 判斷` 節點的 `model` 下拉選單即可。要加新模型，在 `strategies/_common/compose/models.yaml` 加一筆，並寫明知識截止日上界 `cutoff`。
- **防 look-ahead**：
  - `cutoff + buffer` 之前的決策日不呼叫模型、不計績效；
  - 乾淨樣本外天數不足時回測直接失敗；
  - RAG 只取決策日前已公開的文件；
  - prompt 預設匿名（不放日期與標的名稱）；
  - `記憶探測` 節點檢查模型是否記得歷史價格。
- **回放**：頂列的「回放」按鈕，可以逐個決策日查看模型看到的特徵與研究、CoT、決策、OOS 損益、污染區間與 fold 切點。
- **N 記帳**：整個巢狀 walk-forward 算一次 selection trial。換模型、改門檻網格就是新試驗；只改下游統計宣告則不增加 N。
