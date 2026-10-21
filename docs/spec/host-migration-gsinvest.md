# gs-strategy 搬遷到 gsinvest：多人共用部署規格

- 狀態：草稿，待使用者審閱（2026-10-21）
- 範圍：只動 gs-strategy。gs-zipline-tej、FORGE、MINT 只當介面，不改。
- 來源決策：`specfeed-test/gs-strategy-bench/gs-strategy-host-decision.html`（2026-10-21，8 項治理決定已全部定案）。
- 相關規格：`statistical-decision-tree.md`（N、事前登記、規則集）、`auto-research-funnel.md`（screening／selection 帳本）、`strategy-pool-and-results.md`、`live-strategy-graph.md`（StratyUI）。
- 對應計畫：`docs/plans/2026-10-21-host-migration-gsinvest.md`。

## 0. 目標與非目標

**目標**：把 StratyUI（策略圖服務）、每日爬蟲、第三期 LLM 自動寫策略從 Kevin PC 的 WSL 搬到常開的 gsinvest，讓 joker、kevin、jason 三人共用，同時**不削弱**現有的統計紀律（全域 N、事前登記不可改、帳本只能追加）。

**非目標**：不重寫回測引擎；不做 IdeaFlow 匯出對帳（genesis-deck PR #169，另案，本規格只留 §9 的介面）；不改 gs-zipline-tej／FORGE／MINT；不處理 strategy.gsinvest.com 的 UniFi DNS（使用者處理）。

**成功標準**（驗收見 §12）：三人可同時各自操作且互不覆蓋；任何一筆 selection 都能追到「誰、哪份已簽核登記、第幾次」；帳本被改寫可被偵測；LLM 服務在回測高峰期間 TTFT 不惡化；Kevin PC 可在 2 週內無痛回退。

## 1. 現況盤點（本規格據以設計的事實）

以下皆讀自部署版 `~/gs-strategy-deploy`（commit 9f4a71a，分支 `dev/intranet-host`）：

1. **StratyUI 是單一 session 服務。** `GraphService` 持有單一 `engine`／`graph`／`revision`／`active` 工作，`GraphHTTPServer` 只建一個實例。兩人同時開會互相覆蓋圖與 revision（現在的 `graph_revision_conflict` 是為「同一人兩個分頁」設計的）。**這是搬遷最大的缺口**，決策文件「未量：多人同時操作」指的就是它。
2. **身分只被讀、沒有被用。** `api.py` 讀 `X-Auth-Request-Email` 傳成 `actor`，進了 `context.services['actor']`，但 `SelectionLedger.record_success` 沒有把它寫進帳本，也沒有名單檢查。
3. **帳本沒有登記連結。** selection 記錄（`level: trial`、`backtest_key`、`graph_hash`、`stat_decision`）沒有 `prereg_id`、`actor`、`planned_trials` 配額欄位；`/api/run` 不檢查事前登記。
4. **事前登記只靠本機 pre-commit 保護。** `scripts/check_append_only.py`（pre-commit hook，`--no-verify` 可繞）與 `scripts/check_preregistration.py`（`validate()` 回傳問題清單，驗證 `stat_plan` 可解出處方）。伺服器端沒有任何強制。
5. **帳本在 git 裡**（`log/trials.jsonl`，721 行，約 1MB；`.lock` 檔配 `flock`），由 `count_existing_selection_trials` 計 N；`/api/run-estimate` 已回 `selection_n`、`next_selection_n`、`delta_n`。
6. **執行器會改 repo。** `runner.py` 在寫 validation sidecar 時呼叫 `validation/report.py::apply_to_manifest`，把驗證結果回寫到 `strategies/<id>/manifest.yaml`（strategy-import-spec v1.2 §2）。程式碼 checkout 必須可寫才跑得動。
7. **可寫路徑散在 repo 內**：`log/`、`data/backtests.sqlite`、`data/papers.db`、`.cache/live-strategy-graph`、`.graph-runs/`、`strategies/_generated`、`strategies/_llm_generated`、`.zipline`（ZIPLINE_ROOT）。`GraphService` 已有 `--root`／`--cache-dir`／`--ledger` 參數，`results.py` 用 `Path(root)/'data'/...`。
8. **gsinvest 沒有免密 sudo**，但有 docker（2026-10-21 bench 以 `python:3.12-slim` 容器跑）。因此：不能建新 Unix 使用者、不能 `chattr +a`、不能寫 `/etc/systemd/system`；能做的是 docker 容器、使用者層 systemd（`systemctl --user`）、docker named volume。
9. 部署版與開發版（`~/gs-strategy`，`dev/stat-decision-tree`）是不同分支；搬遷以**部署版分支為準**，stat-decision-tree 的待合併工作不在本規格範圍。

## 2. 目標架構

```
 研究員瀏覽器
   │  內網：strategy.gsinvest.com → wiki-caddy(OIDC) → gs-linux 轉埠 ─┐
   │  外網：Cloudflare Access(email+OTP) → cloudflared ───────────────┤
   ▼                                                                 ▼
 ┌──────────────────────────── gsinvest（docker，--cpus=16 --memory=64g --cpu-shares=128）──────────────────────────┐
 │  gs-strategy-app      StratyUI（127.0.0.1:9112）＋ 每人一個 GraphService session ＋ 8 個回測 semaphore        │
 │     │ ro:  /app（程式碼 checkout，固定 commit）  ro: /zipline（TEJ bundle 82MB）                              │
 │     │ rw:  /data（見 §3，named volume gs-data：帳本、結果庫、快取、論文庫）                                  │
 │  gs-strategy-cron     每日爬蟲＋第三期 LLM 寫策略（同映像、同 /data、同一把服務帳號 LLM key）                │
 │  forgejo              權威 bare repo（gs/gs-strategy）＋ pre-receive hook（§5）                               │
 │  gs-backup            唯讀掛 gs-data，15 分鐘同步到 jsonhuang、每日封存（§7）                                 │
 └───────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
        ▲ 雙軌期：Kevin PC（WSL）仍可跑，但切換日起凍結
 jsonhuang：只收備份（帳本、結果庫、bare repo mirror），無 TEJ 金鑰、無 bundle、無 Python 環境
```

**設計原則**：（a）程式碼唯讀、資料可寫，兩者分開；（b）帳本單寫者（app 容器），其他一律唯讀；（c）統計紀律由**伺服器**強制，客戶端 hook 只當提早提示；（d）每一項決定都留一條偵測路徑，不只靠權限。

## 3. 檔案佈局與服務身分

gsinvest 無 sudo，故以 docker 實現「服務帳號單寫」：

| 路徑（容器內） | 來源 | app | cron | backup | 說明 |
|---|---|---|---|---|---|
| `/app` | image 內固定 commit | ro | ro | – | 程式碼；映像以 git SHA 當 tag，回退＝換 tag |
| `/zipline` | host `~/gs-strat/zipline`（82MB） | ro | ro | – | `.zipline` bundle；只在 Kevin PC 與 gsinvest 存在 |
| `/data` | named volume `gs-data` | rw | rw | **ro** | 見下 |
| `/run/secrets/tej_env` | host 檔 0600 | ro | ro | – | `TEJAPI_KEY`，只在此處；不進映像、不進日誌、不進 `docker inspect`（用 file secret，不用 `-e`） |
| `/run/secrets/llm_key` | host 檔 0600 | – | ro | – | 服務帳號 LLM key（§10） |

`/data` 內容：`log/trials.jsonl`（+`.lock`）、`data/backtests.sqlite`、`data/papers.db`、`data/pdfs/`、`.cache/live-strategy-graph/`、`.graph-runs/`、`strategies/_generated/`、`strategies/_llm_generated/`、`experiments/preregistration/`（由 Forgejo post-receive 同步的唯讀檢視，見 §5.5）。

**為何用 named volume**：volume 內容在 `/var/lib/docker/volumes`，非 root 的人類帳號讀不到也寫不到，只有掛載它的容器能動；backup 容器以 `:ro` 掛載。這在沒有 sudo 的條件下達成「服務帳號單寫」，不依賴檔案權限約定。代價：人類要看帳本得走 `docker run --rm -v gs-data:/data:ro` 或 StratyUI 的唯讀 API（§4.6），可接受。

**需改 gs-strategy（F1：資料根目錄）**：新增環境變數 `GS_DATA_ROOT`（預設＝repo 根，行為不變）。`log/`、`data/`、`.cache`、`.graph-runs`、`strategies/_generated`、`strategies/_llm_generated`、`experiments/preregistration` 一律由一個 `paths.py` 解析；`GraphService`／`results.py`／`stat_tree.py`／`daily_refresh.sh`／`check_*.py` 全改用它。測試：同一套 pytest 在 `GS_DATA_ROOT=<tmp>` 與預設下各跑一次。

**需改 gs-strategy（F2：manifest 回寫）**：`apply_to_manifest` 在 `GS_READONLY_CODE=1`（服務環境預設）時改寫到 `$GS_DATA_ROOT/manifest-overlay/<strategy>.yaml`，讀取 manifest 的地方先看 overlay 再看 `/app`。理由：manifest 的驗證區塊是衍生資料，不該污染程式碼 checkout；也避免多人同時回測同一策略時互寫同一檔。測試：唯讀 `/app` 下跑一次回測不報錯且 overlay 產生、`git status` 乾淨。

## 4. 應用層變更

### 4.1 身分與執行人名單（決定 ①）

- 名單檔 `/data/config/roles.yaml`（由部署流程寫入，app 唯讀）：
  ```yaml
  executors: [joker@…, kevin@…, jason@…]     # 可按「執行」
  signers:   [joker@…, kevin@…]              # 可簽核登記、核准 N 超線
  admins:    [kevin@…]                       # 可調資源上限（§8）
  ```
  email 實際值在部署時填，不進 repo。**不在名單的已驗證使用者＝唯讀**：可看圖、帳本摘要、結果，不能 `POST /api/run`、不能改圖後存檔到共用路徑。
- **身分來源**（F3）：優先 `Cf-Access-Jwt-Assertion`（驗 RS256 簽章、`aud`、`iss`、過期，取 `email` claim；JWKS 啟動時抓取並快取、定時刷新）；否則只有來自受信代理（wiki-caddy 所在 gs-linux 的固定來源 IP，設定檔列出）的請求才採信 `X-Auth-Request-Email`；兩者皆無或驗證失敗＝匿名＝唯讀。**用戶端自帶的 `X-Auth-Request-Email` 永不採信**（現有程式只靠「經 public host」判斷，本次加上來源 IP 檢查）。
- 每筆寫入動作（run、簽核、核准）記 `actor`（email 小寫正規化）。

### 4.2 每人一個 session（F4，單一 session 缺口）

`GraphHTTPServer` 持有 `SessionRegistry`：key＝actor email，value＝`GraphService`（各自的 engine／graph／revision／active／cache 子目錄）；**共用** `SelectionLedger` 與結果庫。匿名使用者共用一個唯讀 session。session 閒置 60 分鐘卸載（圖已存檔者不丟）。`/api/session` 回該 actor 的 session。圖檔存檔路徑加 actor 前綴 `graphs/<actor-slug>/`，唯讀名單者不可寫。

**全域回測 semaphore**：同時執行的 backtest 節點上限 8（`GS_MAX_CONCURRENT_BACKTESTS`，admin 可在不重啟下調整），超過者排隊並顯示佇列位置。這與 §8 的 cgroup 上限互補（cgroup 防失控，semaphore 防排隊堆積）。每人同時最多 1 個執行中的 job（沿用現有 `active` 語意）。

### 4.3 帳本：欄位、雜湊鏈、單寫（決定 ②⑥）

`SelectionLedger.record_success` 的 selection 記錄新增欄位（`schema` 升為 `research-trial/v2`，v1 行不改）：

```json
{"schema":"research-trial/v2", "...v1 欄位...":"…",
 "actor":"kevin@…", "prereg_id":"007-tsmom-vol-scaling", "prereg_commit":"<sha>",
 "family":"bounded_multiple", "prereg_trial_index":4,
 "prev_hash":"<sha256 of previous line bytes>", "seq":722}
```

**雜湊鏈（F5）**：`prev_hash`＝上一行**完整位元組**（不含結尾 LF）的 SHA-256 十六進位。既有 721 行為 legacy、不改：第一筆 v2 記錄 `kind:"chain_anchor"`，其 `prev_hash`＝整個 legacy 檔案位元組的 SHA-256、`legacy_lines:721`（切換日匯入時以當時實際行數為準）。之後每筆（含 screening、diagnostic、核准記錄）都帶 `prev_hash` 與遞增 `seq`。`scripts/verify_ledger_chain.py` 從頭驗：鏈斷、`seq` 跳號、legacy 區段被改（與 anchor 不符）皆回傳非零並指出第一個壞行。app 啟動時與每次寫入前各驗尾端一筆（O(1)），每日封存時全驗（O(n)，1MB 級）。

**單寫者**：只有 app／cron 容器掛 `gs-data` 為 rw；寫入走既有 `_exclusive` flock。雜湊鏈在同一把鎖內讀尾、算雜湊、追加、fsync。`check_append_only.py` 現有「git 歷史只准追加」邏輯保留給 Kevin PC 雙軌期與 CI；伺服器上帳本不再進 git。

### 4.4 事前登記閘門與配額（決定 ②③④）

**執行閘門**（`POST /api/run`，在 `record_success` 之前以 `ledger.estimate` 同一把鎖檢查，任一失敗回 403/409 與中文原因，不落帳）：

1. actor ∈ `executors`。
2. 圖的 `stat.facts` 節點宣告 `prereg_id`；該登記存在於**已簽核**清單（§5.3），且狀態為 `active`。
3. `prereg.family ≠ open_mining` 時：該登記已記的 selection 數 `< planned_trials`（上限 10；`validate()` 加規則：`family ≠ open_mining` 且 `planned_trials > 10` 為錯誤）。`family = open_mining` 走 online FDR，不吃 planned_trials，但仍須簽核。
4. actor 作為 author 的 `active` 登記同時 ≤ 3（超過則**新登記**在 pre-receive 被擋，不是等到執行才擋，見 §5.2）。
5. 全域 selection N：`< 30` 正常；`30 ≤ N < 60` 回應附 `warning`；`N ≥ 60` 需存在一筆未消耗的 `n_approval`（§4.5）才放行。
6. `screening`／`diagnostic` 目的的圖**不得**經 `/api/run` 以 selection 記帳，也不得由自動化帳號觸發 selection（見 §10）。

**配額狀態**由帳本推導，不另存計數器：某登記的已用次數＝帳本中 `prereg_id` 相同且 `purpose:"selection"` 的記錄數；因此帳本是唯一真相，備份／還原不會讓兩者分歧。登記**用完**或被 closure 檔關閉即非 `active`；要再試必須開新登記（`supersedes` 指向舊的並寫明理由）。

### 4.5 N 提示線與超線核准

`/api/run-estimate` 擴充回傳：`selection_n`、`next_selection_n`、`threshold_now`、`threshold_next`、`line`（`ok`／`warn30`／`block60`），其中門檻＝`expected_max_sharpe(n)`（沿用 `scripts/triage_generated.py` 的函式，變異取 `1/(T-1)`，T 取該圖的日報酬筆數；無資料時以 T=2000 估並標註）。StratyUI 在確認視窗顯示：「再 +1 會讓運氣門檻從 a 變成 b」。

超過 60：任一 signer 發 `POST /api/n-approval {from_n, reason}` 建立待核准；**另一位** signer（≠ 發起者）`POST /api/n-approval/<id>/approve`。兩筆動作各自追加為帳本記錄（`kind:"n_approval_request"`／`"n_approval_grant"`，帶 actor 與 `prev_hash`）。一筆 grant 解鎖 **到 N+10**（避免一次核准永久開閘）；超過再核准。joker 與 kevin 為唯二 signer，互相覆核。

### 4.6 唯讀與匯入 API（唯讀名單者與 IdeaFlow 用）

- `GET /api/ledger/summary|tail?n=|verify`：摘要、尾 n 行（遮蔽 `graph_ref.snapshot`）、鏈驗證結果。
- `GET /api/preregistrations`：全部登記（含 `active`／`closed`／`signed_by`／`used`／`planned_trials`）。
- 匯入介面見 §9。

### 4.7 回測相關效能與資源護欄

沿用 bench 結論：服務環境一律 `OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=MKL_NUM_THREADS=1`（image `ENV` 與 compose 雙設）。WSL 上可交易性檢定 400 倍變慢的原因未追，**搬遷後 Kevin PC 的回退實例也必須設這三個變數**（寫進回退 runbook）。

## 5. 伺服器端強制事前登記（決定 ③④）

### 5.1 為何用 Forgejo 承載「bare repo + pre-receive」

決策指定「gsinvest 放權威 bare repo＋pre-receive hook」。裸用 `git init --bare` 需要 sshd 帳號與 per-user 公鑰（需 sudo），在無 sudo 的 gsinvest 做不到。Forgejo 容器本身就是「權威 bare repo + server-side hook」，且帶來**伺服器端認定的推送者身分**（hook 環境變數 `GITEA_PUSHER_NAME`／`GITEA_PUSHER_EMAIL`／`GITEA_PUSHER_ID`，不是可偽造的 commit author 欄位）。Kevin PC 的 WSL 已有 `forgejo` remote（`ssh://git@localhost:2222/gs/gs-strategy.git`），三人熟悉。**這是本規格對決策的唯一實作性調整，審閱時請確認（§13 Q1）。**

### 5.2 pre-receive 檢查（掛 `check_append_only.py` 與 `check_preregistration.py`）

對每個被推送的 ref 更新 `old..new`（僅 `refs/heads/main`，其餘分支只做 3、5）：

1. `check_append_only.py --rev-range old..new`：`experiments/preregistration/*.yaml` 與 `*.signoff.yaml`、`*.closure.yaml` 只准新增、不准改／刪／改名。（`PREREG_NON_REGISTRATION` 的範本與說明可改。）
2. `check_preregistration.py <本次新增或變更的登記檔>`：`validate()` 通過（含新增規則 `planned_trials ≤ 10` unless `open_mining`）。
3. **作者一致**：登記檔內的 `author` email 必須等於推送者 email（server 認定），防止代他人登記。
4. **作者同時進行中的登記 ≤ 3**：計算新增登記後該 author 的 `active` 數（active＝有 signoff、無 closure、used < planned_trials；**尚未簽核者也計入**，防止囤積待簽登記）。
5. 禁止 force push 與刪分支（`refs/heads/main`），伺服器端 `--no-force`，Forgejo 分支保護設為「僅 hook 通過可推」。
6. 失敗時訊息為中文並指向本規格章節；hook 腳本不得印出任何環境變數（避免洩漏）。

hook 以薄包裝 `hooks/pre-receive`（bash）呼叫 repo 內的 Python 腳本；**腳本版本來自被推送 repo 的 `main` 目前版本**會讓攻擊者先推新版腳本繞過，因此 hook 使用 **Forgejo 容器內獨立安裝的固定版本**（`/opt/gs-hooks/`，與 repo 分離，升級走 PR＋手動部署）。

### 5.3 簽核（決定 ④）

簽核＝在登記 `NNN-slug.yaml` 之後**另加一個新檔** `NNN-slug.signoff.yaml`（凍結規則下只准新增，天然符合）：

```yaml
schema: preregistration-signoff/1
prereg_id: "007-tsmom-vol-scaling"
prereg_sha256: "<該登記檔內容雜湊>"
signer: "kevin@…"          # 必須 = 推送者
signed_at: "…"
```

pre-receive 檢查：signer ∈ signers 名單；signer == 推送者；**signer ≠ 該登記的 author**；`prereg_sha256` 與當前檔案相符；同一登記只准一份 signoff。Jason 的登記由 joker 或 kevin 簽；joker 的由 kevin 簽，反之亦然。**登記與簽核不得在同一次推送內由同一人完成**（signer ≠ author 已涵蓋）。

closure：作者或 signer 可新增 `NNN-slug.closure.yaml`（`reason`、`closed_by`、`closed_at`）提早關閉登記；用完配額者視同關閉，不必手動。

### 5.4 本機 hook 保留

`scripts/install_hooks.sh` 的 pre-commit（`check_append_only.py`）保留為**提早提示**，文件註明它不是強制；強制在伺服器。

### 5.5 登記檢視同步

Forgejo post-receive（或 app 啟動／每 60 秒輪詢）把 `main` 的 `experiments/preregistration/` 同步到 `/data/experiments/preregistration/`（app 唯讀）。app 只信這個目錄＋ signoff 檔的 `prereg_sha256` 與內容相符，不信 URL 參數。

## 6. TEJ 金鑰管控（決定 ⑤）

- 金鑰檔只存在 gsinvest 的 `~/gs-strat/secrets/tej_env`（0600，作為 docker secret 掛入）與 Kevin PC 既有位置。**jsonhuang、gs-linux 不得出現**（含備份內容、docker 映像層、日誌）。
- 備份清單（§7）明確**排除**：`.env`、`secrets/`、`.zipline`（bundle 屬 TEJ 資料，備份機不存）、任何 Python 環境。還原到備份機只能「看」帳本與結果，不能回測。
- 日誌過濾：app／cron 啟動時對 stdout/stderr 掛 redaction filter（遮蔽 `TEJAPI_KEY` 值與常見金鑰樣式）；測試以假金鑰斷言不外洩（沿用既有 gitleaks 放行的「執行期組成假金鑰」手法）。
- 驗收：`grep -r` 對 jsonhuang／gs-linux 的備份與家目錄搜 `TEJAPI_KEY` 值的雜湊前綴（不輸出明文）為零。
- TEJ 併發限制**未查證**；cron 與 app 同時抓 TEJ 的情況在影子期觀察，計畫列為待量項。

## 7. 備份、防竄改與保存（決定 ⑥）

| 項目 | 機制 | 目標 |
|---|---|---|
| 帳本即時同步 | `gs-backup` 容器每 15 分鐘 `rsync -a --append-verify`（ssh forced-command、write-only key）到 jsonhuang `/backup/gs-strategy/ledger/`；jsonhuang 上該目錄只增不改 | RPO ≤ 15 分鐘 |
| 每日快照 | jsonhuang 端用 `cp -al` 硬連結輪替保留（永久保存，不輪出；體積 1MB/日可忽略） | 還原到任一日 |
| 結果庫 | 每日 `sqlite3 .backup` 一致性快照後再同步（不直接複製 live 檔） | 無撕裂 |
| bare repo | Forgejo 每日 `git bundle`／mirror 到 jsonhuang | 登記與簽核不丟 |
| 第三方時間戳 | 每日 00:10 `seal`：全驗鏈後產生 `seals/YYYY-MM-DD.json`（`lines`、`head_hash`、`file_sha256`、`legacy_lines`），commit 到 GitHub 的**孤立分支 `ledger-seals`**，打 **SSH 簽章 annotated tag `seal/YYYY-MM-DD`** 並推送（不開新 repo；不碰 main，合乎分支保護政策；以 deploy key 寫入） | 事後可證明某日帳本狀態 |
| 離線 | 每週以 `age` 公鑰加密的 tarball 送到 F 槽備份機（public-pc，版本化）；私鑰由 Kevin 離線保管 | 異地＋防勒索 |
| 保存 | 永久 | – |

**還原目標**：資料遺失上限 15 分鐘（RPO）、還原約 1 小時（RTO）。還原演練（§12 驗收）：在 gsinvest 另起空 volume，從 jsonhuang 還原最新快照＋ replay，`verify_ledger_chain.py` 全綠、N 與還原前一致。

**偵測 vs 預防**：因無 `chattr +a`，帳本被改寫**不能預防到底**，靠三層偵測：雜湊鏈（改任一行即破鏈）、jsonhuang 的只增副本（與本地分歧即告警）、GitHub 簽章 seal（鏈尾被整段重寫也對不上）。告警：每次同步比對本地與 jsonhuang 的 `head_hash`／`seq`，分歧或鏈斷即推播給 kevin（沿用現有推播通道），並讓 app 進入**唯讀**（拒絕 `/api/run`）直到人工解除。

## 8. 資源與對外存取（決定 ⑦）

### 8.1 資源

- app、cron 兩容器共用一組硬上限：`--cpus=16 --memory=64g --memory-swap=64g --cpu-shares=128`（與 bench 同；用 compose 的 `deploy.resources` 或 `docker run`，**同一個 cgroup parent** 以共享 16 核而非各 16）。Forgejo、backup 另設小額度（各 ≤1 核、≤2GB）。
- 同時回測 ≤ 8（§4.2）；CPU 權重壓低使 LLM（vLLM）永遠優先。
- 三個執行緒環境變數設 1（§4.7）。
- **TTFT 告警**：gateway 的 TTFT p95 在研究服務執行期間（以 app 的「執行中 job 數 > 0」旗標對時）比過去 7 天同時段基線高 30% 且持續 5 分鐘 → 推播 kevin。實作在 gs-obs-radar／gateway 側（另案，只在計畫列介面：app 暴露 `GET /api/load`＝執行中 job 數與佇列長度）。
- 誰能調上限：kevin（`admins`；改 `GS_MAX_CONCURRENT_BACKTESTS` 走 `POST /api/admin/limits`，改 cgroup 上限需重建容器，由 kevin 手動）。
- 上線前補量：不設上限的 192 執行緒預設值**不量**（會影響正式 LLM 流量）；24h 負載曲線取回後補進決策文件（取樣程序見記憶檔，約 2026-10-22 下午）。

### 8.2 對外存取

- 兩條入口並存：內網 `strategy.gsinvest.com`（wiki-caddy OIDC，DNS 由使用者處理）；外網 Cloudflare Access（application 名單三個 email＋OTP，session 24h）。
- 外網 hostname 與內網不同（例 `strategy-ext.gsinvest.com`，§13 Q3），避免同名雙路徑的 Host／Origin 檢查混淆；app 的 `--public-host` 同時列兩個。
- **兩層驗證**：Access 擋第一層；app 再驗 `Cf-Access-Jwt-Assertion`（§4.1），email 必須在 `roles.yaml` 才有寫入權。Access 的 email 通過但不在 roles 名單＝唯讀。
- Cloudflare API token **只在 dgx-a4d7**：建立 tunnel 與 Access 規則從該機操作；gsinvest 只放 tunnel 的 connector token（`cloudflared` 容器，無 API 權限）。本規格不要求把 CF API token 帶到任何其他機器。

## 9. IdeaFlow 介面（另案，本規格只留接口）

genesis-deck PR #169 的草稿匯出與對帳原本讀 `\\wsl.localhost`。搬遷後改為 HTTP：

- `POST /api/import/ideaflow-draft`：body＝草稿 JSON（沿用 #169 的格式）；actor 必須在 `executors`；**只做驗證與階段判定，不執行、不記 selection**（ΔN=0，對應 screening）；回傳判定與缺漏欄位。
- `GET /api/export/ideaflow-reconcile?since=`：回傳 ledger 的對帳視圖（`trial_id`、`prereg_id`、`purpose`、`actor`、`finished_at`、`stat_decision.status`），唯讀名單者亦可呼叫。
- 認證同 §4.1。回應 schema 版本化（`ideaflow-bridge/1`），由 genesis-deck 側契約測試守護。
- 本規格**不**實作這兩個端點的具體 payload，只要求計畫的「API 骨架」任務保留路由與 schema 版本，另案填內容。

## 10. 爬蟲與第三期 LLM 自動寫策略（決定 ⑧ 後段）

- `daily_refresh.sh` 與 `quant_crawler/strategy_gen`、`paper_class`、codegen 一起搬入 `gs-strategy-cron` 容器；排程用容器內 supercronic 或 host `systemctl --user` timer（06:30，與現況一致）。輸出落 `/data`（`papers.db`、`pdfs/`、`strategies/_generated`、`_llm_generated`、`data/backtests.sqlite` 的 codegen 實驗）。
- **服務帳號 LLM key**：單一把，掛為 secret，標註用途；用量歸戶由 key 區分，不用任何個人 key。
- **「未審」策略**：`_llm_generated` 下的策略標 `review_status: unreviewed`（現有標記沿用）。API 規則：`unreviewed` 的策略只有 `signers` 能在 StratyUI 載入與執行；`executors` 中的 jason 只能執行已被 signer 把 `review_status` 改為 `reviewed`（記為帳本 `kind:"strategy_review"` 記錄，含 actor）的策略。
- **自動化不得按執行**：cron 容器的帳本寫入路徑只允許 `purpose ∈ {screening, diagnostic}`、`delta_n = 0`；以程式層（`SelectionLedger.record_success` 拒絕 `actor == "service:cron"`）加測試強制，不靠約定。G0–G2 閘門（含 G1 煙霧：走 gs-zipline-tej `dashboard.registry` 的介面）結果不進 selection。
- G1 煙霧依賴 gs-zipline-tej 的 `dashboard.registry`；該 repo 不搬。容器 image 以 pip 安裝固定版 gs-zipline-tej（或 vendored wheel），**只當介面**，在計畫中列為「影子期確認 G1 能跑」的驗證項。

## 11. 切換三階段與回退（決定 ⑧ 前段）

### 階段 A：準備（不影響現況）
建 image、volume、Forgejo、roles、備份通道、CF Access；全部在 gsinvest 上，**不寫正式 N**。

### 階段 B：影子期（1–2 週）
- gsinvest 以**帳本拷貝**（Kevin PC 當日快照，`GS_LEDGER_MODE=shadow`，寫入另一份影子帳本，app 頁首顯示「影子環境」橫幅）跑；同樣輸入對照 Kevin PC：回測 `backtest_key` 一致、績效數字容差內相同（`pytest` 比對腳本，容差在計畫定義）。
- 同時觀察 gsinvest PCIe：`dmesg`／`journalctl -k` 連續 1–2 週無 Xid／AER 才算「可當唯一正式環境」；任一次出現 → 影子期歸零重計。
- 驗證：多人同時操作（3 人 × 並行 run）、16 個**不同設定**同時寫帳本（bench 未測）、備份還原演練、TTFT 告警演練、CF Access 兩層驗證、G1 煙霧。

### 階段 C：切換日（運維窗口，建議非交易時段）
1. 公告同仁（通道：公司群組）；T-24h 公告，T-0 重申。
2. 凍結 Kevin PC：停 StratyUI 寫入（服務改唯讀旗標）、停 `daily_refresh` timer、`.git/hooks/pre-push` 加擋。
3. 最後一次匯入：把 Kevin PC `log/trials.jsonl` 全檔複製到 gsinvest（記錄 sha256），以匯入腳本寫入 `/data`，產生 `chain_anchor`，`verify_ledger_chain.py` 全綠；比對 N 與行數與 Kevin PC 完全一致才續。
4. 切 `strategy.gsinvest.com` 指向（gs-linux 轉埠目標改 gsinvest；DNS 由使用者處理，此步等其完成）；CF Access hostname 啟用。
5. 煙霧：三人各登入一次、一次 selection run 成功、帳本 N+1、備份同步看到新 seq。
6. 回報使用者；開始 2 週回退期計時。

### 階段 D：回退期（2 週）
Kevin PC 保留**唯讀實例**（可看、不可執行）。回退條件（任一）：gsinvest 出現 Xid／AER、帳本鏈驗證失敗、重大資料不一致。回退步驟：gsinvest 停寫 → 把切換日之後新增的帳本行（以 `seq` > 匯入時 anchor 的）複製回 Kevin PC → 驗鏈 → 解除凍結 → 轉埠指回。2 週無事後，Kevin PC 實例退役（保留為備援冷備份）。

**雙軌原則**（使用者既有偏好）：新路徑測完才拆舊權限、先公告員工、不一次切斷。

## 12. 驗收標準（可機器檢查者優先）

1. `pytest` 全綠（現有＋本規格新增測試），且在 `GS_DATA_ROOT` 非預設值、`/app` 唯讀下亦綠。
2. 兩個 session 同時操作互不影響（自動化 e2e：A 改圖不改變 B 的 `revision`）。
3. 非名單者 `POST /api/run` → 403；名單者無已簽核登記 → 409；登記用完 → 409；N=60 無 grant → 409。
4. pre-receive：改既有登記、無簽核推 signoff 自己登記、author≠推送者、第 4 份進行中登記、force push、`planned_trials=11` 的非 open_mining 登記，**每一項皆被拒**，且錯誤訊息為中文（`git push` 實測，在測試用 Forgejo 實例）。
5. 帳本改任一舊行或刪行 → `verify_ledger_chain.py` 非零；app 拒絕寫入。
6. 還原演練通過（§7）。
7. 全部備份、jsonhuang、gs-linux 搜不到 TEJ 金鑰。
8. 16 個不同設定同時寫帳本：行數正確、無重複、鏈完整。
9. 影子期對照：同輸入的 `backtest_key` 相同、績效在容差內。
10. gsinvest 連續 ≥14 天無 Xid／AER（切換前）。

## 13. 開放問題（審閱時請拍板；已附我的預設）

- **Q1**：以 Forgejo 承載權威 bare repo 與 pre-receive（§5.1）。預設：是。替代：自架 `git-http-backend`＋反向代理驗身分（程式更多、且仍需自己做 per-user 認證）。
- **Q2**：帳本放 docker named volume（§3）而非 host 目錄。預設：是。代價：人類看帳本需走 API 或唯讀容器。
- **Q3**：外網 hostname。預設 `strategy-ext.gsinvest.com`，由 dgx-a4d7 建 tunnel；DNS／CF 變更要使用者確認。
- **Q4**：N 超線核准一次解鎖「+10」（§4.5）。預設如此；也可改成「單次一票一筆」。
- **Q5**：GitHub `ledger-seals` 孤立分支存 seal（§7）。預設在 gs-strategy repo；也可另開專用 repo（需使用者建立）。
- **Q6**：`roles.yaml` 的 email 實際值（joker／jason 的公司 email）與 CF Access 名單，由使用者提供，不寫進 repo。
- **Q7**：影子期績效對照的容差（預設：夏普絕對差 ≤ 1e-6、`backtest_key` 必須完全一致）。

## 14. 風險

- **PCIe**：gsinvest 剛修好，已以「備份＋影子期＋回退期」緩解；仍可能在切換後才發作 → 回退 runbook 需演練過。
- **Forgejo 單點**：登記與簽核都靠它；每日 mirror 到 jsonhuang，且 app 讀的是同步到 `/data` 的檢視，Forgejo 掛掉時**不能新登記**但現有登記仍可執行。
- **帳本非 git 管**：失去 git 歷史保護，由雜湊鏈＋異地只增副本＋seal 取代；風險是三者皆被同時繞過，需 gsinvest 本機 root 才可能，已超出本威脅模型（三位研究員、善意但可能疏忽）。
- **單 session → 多 session 的改動**面積大（F4）：計畫把它拆成最小可驗證步驟並先在影子環境跑。
- **CF Access 與 OIDC 雙入口**的 Host/Origin 檢查回歸：兩條入口各有 e2e 測試。
- **TEJ 併發限制未查證**：影子期觀察，必要時 cron 與 app 錯開。
