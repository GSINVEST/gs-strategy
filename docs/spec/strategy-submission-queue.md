# 策略提交佇列：最小可交付版本設計

> 狀態：**設計草稿，未實作、未驗證任何程式。** 本文所有「現況」皆來自 2026-10-30 對 `origin/main`（`deed371`）的唯讀檢視，列出的指令與行號沒有在本機重跑測試。
> 上游規格：genesis-deck `docs/superpowers/specs/2026-10-29-strategy-trader-platform-design.md` §6、§7
> 要對齊的形式模型：genesis-deck `specs/tla/strategy-queue/Queue.tla`（六個不變量）、`tests/formal/strategy_queue_ref.py`（參考實作）、`docs/formal-verification/strategy-queue.md`
> 相鄰規格：[statistical-decision-tree.md](statistical-decision-tree.md) §7、[auto-research-funnel.md](auto-research-funnel.md) §4

## 0. 結論先講

1. gs-strategy **沒有**提交佇列，也沒有任何一條路徑能把「一次 selection 回測」寫進 ledger：`log/trials.jsonl` 719 行、360 個相異 `trial_id`，**全部** `delta_n = 0`（L0 機械分診，`no_statistical_claim: true`）；`experiments/preregistration/` 只有 `README.md` 與 `TEMPLATE.yaml`，**一份真的登記檔都沒有**。所以六個不變量裡沒有一個在現況下成立，只是它們還沒有被違反的機會。
2. 最小設計：新增一個 `strategies/_common/submission/` 套件，狀態只有 `pending → approved → retired` 三種；**核准 = 在 flock 下對 ledger 追加一行 `delta_n=1` 的 trial（含簽核欄位）**，這一次 append 就是同一個動作的提交點；提交本身只寫事件檔、不呼叫回測。CLI 即可，不做 StratyUI 頁。
3. 先修三個會讓第一筆 selection trial 就出事的既有缺口（§1.3），這些不修，佇列一上線就會把 DSR 分母算錯。
4. 切 3 個 PR（gs-strategy 2 個、對真程式的回放 1 個，後者需 genesis-deck 配合小改）。粗估 3 個工作天，**這是我的猜測，沒有實測依據**。

## 1. Ledger 現況

### 1.1 事實（已檢視）

| 項目 | 現況 | 出處 |
|---|---|---|
| 格式 | JSONL，`schema: research-trial/v1`，每行一個 trial 節點；`stat_decision` 區塊帶 `purpose`、`delta_n`、`no_statistical_claim`、`basis`，或帶 `decision_path`/`rule_id`/`threshold`（selection 用） | `log/trials.jsonl`、`strategies/_common/validation/decision.py` |
| 規模 | 719 行／360 個相異 `trial_id`／`delta_n` 總和 0／`purpose` 欄位在頂層全為空（`purpose` 只在 `stat_decision` 內） | 本次統計 |
| 誰寫 | **唯一**寫入者是 `scripts/triage_generated.py --apply`（`open("a")` 直接追加）。`runner.py` 不寫 ledger；`quant_crawler/webui/stat_tree.py` 只讀 | `triage_generated.py` 尾段、`stat_tree.py` |
| append-only | 靠 `scripts/check_append_only.py` 擋「既有行被改、被截短、被刪、被改名」，掛在 `.git/hooks/pre-commit`（`scripts/install_hooks.sh` 安裝）。**只在本機 commit 時生效**；`.github/workflows/ci.yml` 沒有呼叫它（對 ci.yml grep `append` 與 `rev-range` 無命中）；`--no-verify` 可繞過（設計上刻意留著） | `check_append_only.py` |
| 同 id 多行 | 允許。讀端取「同 `trial_id` 最後一筆」：`stat_tree.ledger_summary()` 這樣做（`n_selection` 是對**最後一筆**的 `delta_n` 加總） | `stat_tree.py:155-170` |
| n_trials 怎麼算 | 有三套互不相連的算法：(a) `stat_tree.ledger_summary()`：去重後加總 `delta_n`；(b) `triage_generated.count_existing_selection_trials()` 與 `decision.audit_ledger()`：**不去重**，逐行加總 `delta_n`；(c) `runner.py` → `validation.report.resolve_n_trials()`：config `validation.n_trials` > 環境變數 `GS_VALIDATION_N_TRIALS` > 預設 `1`，**完全不讀 ledger** | 三處原始碼 |
| p 值 | selection trial 需要 `stat_decision.p_value` 與 `p_value_source`，否則 `online_fdr.budget_from_ledger()` 丟 `PValueProvenanceError`（整個面板的 online FDR 區塊變 unavailable）。目前沒有任何 selection trial，所以沒觸發過 | `online_fdr.py:260-306` |
| prereg 連結 | `experiments/preregistration/README.md` 規則 4 寫「每筆 `purpose: selection` 的 trial 都要能指回一份登記檔（`stat_decision.preregistration_id`）」，但 `strategies/_common/validation/*.py` 全文 grep `preregistration_id` **零命中**——這條規則目前只存在於文件，沒有任何程式檢查 | grep |

### 1.2 與模型六個不變量的差距

| 不變量 | 模型的意思 | gs-strategy 現況 | 差距 |
|---|---|---|---|
| `TrialMonotone` | trial 只增不減，退出池也不扣 | hook 擋「改既有行」，但**允許追加**。追加一行同 id、`delta_n` 較小的記錄，讀端取最後一筆，count 就減少；hook 與 `audit_ledger` 都不會報 | **有缺口**：需要「同 id 的 `delta_n` 不得下降」的稽核（程式＋CI），且 CI 要跑 `check_append_only --rev-range` |
| `TrialLedger` | trial 帳本 = 實際回測次數 | 沒有任何東西把「跑了一次」寫進 ledger；`runner.py` 的 DSR n_trials 來源是 config/env/預設 1。兩條記帳線完全分離 | **整條缺**：需要「核准即記帳」與「runner 的 n_trials 讀 ledger」 |
| `PreregBeforeSubmit` | 沒有 prereg 草稿不能提交 | 沒有「提交」。登記檔機制本身（凍結、`validate()` 把 `stat_plan` 餵 `resolve()`）是好的，但目錄是空的；genesis-deck 的 prereg 草稿寫在**金庫（vault）**，不在本 repo，兩邊沒有「草稿 → 凍結登記檔」的路徑 | **缺提交概念**；另缺「vault 草稿 → `experiments/preregistration/NNN-*.yaml` 並 commit」的晉升步驟（見 §7 未確認項） |
| `RunNeedsApproval` | 跑過回測就一定有人核准；提交不跑回測 | `runner.py` 任何人任何路徑都能跑；沒有核准概念 | **缺**：至少要讓 `strategies/_submitted/` 底下的策略只能經核准路徑執行 |
| `NoDoubleRun` | 重複核准／重送不重複執行、不重複計 trial | 沒有核准；且 (b) 那種不去重的加總會讓「追加第二行同 id 記錄」被雙重計入 | **缺**，且與 §1.1 的不一致耦合 |
| `SignedBeforePool` | 核准同時完成簽核，未簽核不得入池 | 沒有「池」也沒有簽核欄位。登記檔凍結後不能再寫入簽核；README 的 GPG tag 是手動存證，沒有被任何程式讀取 | **缺**：簽核欄位要放在 ledger 記錄，不能放登記檔 |

### 1.3 第一筆 selection trial 寫入前必須先修的三件事

1. **去重一致化**：`decision.audit_ledger()` 與 `triage_generated.count_existing_selection_trials()` 改成與 `stat_tree`/harness 相同的「同 id 取最後一筆」。否則下面「完成行」會讓 N 變 2 倍。
2. **同 id `delta_n` 不得下降**：寫進 `audit_ledger`（回報為 problem），並在 CI 跑 `check_append_only.py --rev-range origin/main..HEAD`。
3. **p 值缺口的處置**：selection trial 在回測完成前沒有 p 值，`budget_from_ledger` 會直接丟例外。設計上用「完成行」補（§2.4）；補之前 `online_fdr_summary()` 顯示 unavailable 並說明缺哪一筆，這是現有的誠實行為，不改。

## 2. 最小設計

### 2.1 範圍

做：提交（含靜態前置檢查）、待審列表、CLI 核准、核准即記帳＋簽核、執行回測、退出。
不做：StratyUI 頁、HTTP 收件端點（genesis-deck `strategyproxy` 的對口，另案）、拒絕（`rejected`，見 §7）、排行榜。**提交不跑回測**——模組內 `submit()` 不得 import runner／zipline，由測試鎖住。

### 2.2 狀態與介面

```python
# strategies/_common/submission/models.py
class Status(str, Enum):
    PENDING  = "pending"    # 已提交，未核准；無 ledger trial
    APPROVED = "approved"   # ledger 已有 delta_n=1 的 trial 且帶 approval 區塊
    RETIRED  = "retired"    # 退出池；trial 不減

class Outcome(str, Enum):   # 對齊 strategy_queue_ref.py 的回傳字串，adapter 直接用
    QUEUED = "queued"; NOOP = "noop"; REJECTED = "rejected"
    APPROVED = "approved"; RETIRED = "retired"

@dataclass(frozen=True)
class Result:
    outcome: Outcome
    submission_id: str
    reason: str | None = None          # REJECTED 時必填，指出是哪個守衛擋的

# strategies/_common/submission/service.py
class SubmissionQueue:
    def __init__(self, root: Path, *, approvers: Collection[str],
                 backtest: Callable[[Submission], BacktestRun],
                 clock: Callable[[], str] = utc_now): ...
    def submit(self, bundle_dir: Path, prereg_id: str, submitter: str) -> Result
    def approve(self, submission_id: str, who: str, *, run: bool = True) -> Result
    def retire(self, submission_id: str, who: str) -> Result
    def status(self, submission_id: str) -> Status | None
    def snapshot(self) -> QueueSnapshot   # 唯讀；回放與 CLI list 共用

# CLI：python -m strategies._common.submission {submit,list,show,approve,retire}
```

`root` 與所有路徑可注入（現行 `stat_tree.py`、`triage_generated.py` 是模組級常數，不利測試與回放，新套件不沿用）。

**`submission_id` = 內容雜湊**（`strategy.py`＋`config.yaml`＋`manifest.yaml` 與 `prereg_id` 的 sha256 前 12 碼）。後果：同內容重送 → 同 id → `NOOP`（對應模型的 `SubmitAgain`）；任何內容改動 → 新 id → 新 pending → 核准時新增一筆 trial，符合決策樹 §7「任何參數微調都算一次新 trial」。代價：模型沒處理「同一策略改版重跑」，這裡用「改版 = 新策略」迴避而非解決，見 §7。

### 2.3 檔案落點

| 路徑 | 內容 | 保護 |
|---|---|---|
| `strategies/_common/submission/{models,store,service,cli,__main__}.py` | 本設計的程式 | — |
| `strategies/_submitted/<submission_id>/{strategy.py,config.yaml,manifest.yaml}` | 提交的 bundle，**寫一次不覆寫** | `check_append_only.py` 新增「此目錄既有檔只准新增」（比照 `is_frozen_registration`） |
| `log/submissions.jsonl` | 事件檔：`submitted`、`retired`、`run_started` | 加進 `APPEND_ONLY_PATHS` |
| `log/trials.jsonl` | 既有；核准時追加一行 selection trial | 既有 hook |
| `log/.submission.lock` | `fcntl.flock` 用 | `.gitignore` |
| `config/approvers.yaml`（新） | 核准者名單（見 §7 身分問題） | 一般檔案 |
| `tests/test_submission_queue.py`、`tests/test_submission_cli.py` | 單元／CLI | — |

為什麼不放 `strategies/_generated/`：最近一個 commit（`c82c67b`）剛把它**退出版控**；提交的 bundle 是稽核證據，必須在版控內。

**狀態不另存，由兩份 append-only 檔摺出來**：`PENDING` = 有 `submitted` 事件、ledger 無對應 trial；`APPROVED` = ledger 有 `trial_id = t-sub-<id>` 且帶 `approval`；`RETIRED` = 有 `retired` 事件。沒有第三份可變狀態可以與前兩者不一致。

### 2.4 「同一個動作」：核准的寫入順序

```
approve(sid, who):
  with flock(log/.submission.lock):                     # 序列化所有寫入者
    state = fold(submissions.jsonl, trials.jsonl)
    守衛（任一不成立 → REJECTED，不寫任何東西）：
      who ∈ approvers
      state[sid] == PENDING      （已 APPROVED → NOOP；RETIRED/不存在 → REJECTED）
      prereg 重驗：檔案存在於 git HEAD、validate()==[]、記下 sha256
      resolve(Facts(**stat_plan, n_trials=<ledger 現有 selection 數>)) 解得出處方
    append 一行到 trials.jsonl，fsync：                  # ← 提交點（唯一）
      trial_id   = "t-sub-<sid>"
      stat_decision = { purpose:"selection", delta_n:1, ruleset_version, decision_path,
                        rule_id, threshold, may_be_selected:true }   # 由 resolve() 產出
      approval   = { approved_by: who, signed_by: who, signed_at, prereg_id,
                     prereg_sha256, bundle_sha256 }
      state      = "approved"（尚未完成）
  if run: _execute(sid)                                 # 鎖外執行，見下
```

- **記帳與簽核在同一次 append**：ledger 那一行同時就是 trial、核准、簽核、入池。因此 `SignedBeforePool`、`TrialLedger` 的「核准那一刻」在結構上不可能分離，而不是靠兩步都記得做。
- **crash 語意**：append 之前 crash → 什麼都沒發生（仍 PENDING）；append 之後、回測之前 crash → trial 已計、runs=0。這是**保守方向**（多計不少計，符合決策樹 §7 紀律 1「跑壞的也算」），但與模型「每個動作原子、trials = runs」有一個可觀測的差：中間態 `trials=1, runs=0`。回放 adapter 以 `run=True`（同步執行）呼叫，使每步結束時兩者相等；中間態列為模型外行為。
- **執行 `_execute(sid)`**：先 append `run_started` 事件（`NoDoubleRun` 的依據：已有 `run_started` 就不再跑，失敗也不重跑——要重跑就是新提交＝新 trial），再呼叫 `backtest`，完成後追加 ledger「**完成行**」：同 `trial_id`、`delta_n` 仍為 1、`approval` 原樣照抄、補上 `p_value`＋`p_value_source`、`state:"complete"`。完成行不得改 `delta_n`，由 §1.3 的稽核（去重取最後一筆＋不得下降）保證不被重複計。
- `runner.py` 的接法：`run_strategy_from_config` 新增可選 `n_trials` 參數，queue 呼叫時傳 ledger 的 selection 數、來源標 `"ledger"`（`resolve_n_trials` 目前只認 config/env/default）。直接以 CLI 跑 `strategies/_submitted/**` 路徑時，runner 檢查該 id 在 ledger 已核准且 `run_started` 尚未存在，否則拒絕；其他路徑的既有行為**不變**（研究用回測屬 diagnostic/screening，不在佇列範圍）。

### 2.5 提交的守衛（`submit`）

依序：`bundle_dir` 三個檔都在、大小上限、不是符號連結 → `prereg_id` 對應檔存在於 **git HEAD**（不是工作目錄，避免剛寫還沒 commit 的檔案過關）且 `validate()==[]` 且 `manifest.yaml` 的 `prereg:<id>` tag 與參數一致 → 靜態檢查（**第二次**驗證，genesis-deck §6 要求桌面版與代理各一次；規則清單 genesis-deck 尚未定義，MVP 只放「禁止 import socket/subprocess/動態 import」這類 AST 黑名單，並在 §7 標未完成）。通過 → 複製 bundle 到 `strategies/_submitted/<sid>/`，append `submitted` 事件，回 `QUEUED`；已存在 → `NOOP`；任一守衛不過 → `REJECTED`＋reason，不寫任何東西。

### 2.6 測試計畫

| 層 | 內容 |
|---|---|
| 單元 | 六個不變量各一組性質測試（直接對 `SubmissionQueue` 跑隨機動作序列，每步檢查）；`submit` 不 import runner（`sys.modules` 斷言）；冪等（重送、重複核准）；非核准者被拒且不寫檔；prereg 缺／未 commit／`validate` 不過被拒；`resolve` 解不出處方被拒 |
| 稽核 | `audit_ledger` 去重、同 id `delta_n` 下降被抓、`count_existing_selection_trials` 與 `stat_tree` 一致（PR1） |
| 負控制 | 比照 genesis-deck 的 7 個壞實作（subclass 覆寫 seam，§3），每個必須被對應不變量抓到，否則測試本身空洞 |
| 並發 | 兩個行程同時 `approve` 同一 id，只能一個 `APPROVED`、ledger 只多一行（用 `multiprocessing`，flock 的正確性靠這條驗，不靠推理） |
| crash | 在 ledger append 前後各注入例外：前 → 仍 PENDING；後 → APPROVED、`trials=1/runs=0`，再 `approve` 回 `NOOP` 且不重跑 |
| CLI | `submit → list → approve → show` 走一遍；`--as` 不在名單被拒 |
| 不測 | 真的 zipline 回測（需 `TEJAPI_KEY`，CI 已知跳過，見 `ci.yml` 開頭說明）；`backtest` 一律注入計數樁 |

### 2.7 PR 切分與工量（粗估，未實測）

| PR | 內容 | 估計 |
|---|---|---|
| **PR1 ledger 硬化**（前置） | §1.3 的 1、2；`check_append_only` 加 `strategies/_submitted/`、`log/submissions.jsonl`；CI 加 append-only 步驟；對應測試 | 0.5 天 |
| **PR2 佇列本體** | `submission/` 套件、CLI、`approvers.yaml`、runner 的 `n_trials` 參數與 `_submitted` 守門、§2.6 的單元／並發／crash／負控制測試 | 1.5 天 |
| **PR3 回放銜接**（gs-strategy 端＋genesis-deck 端各一個小 PR） | §3 的 adapter、釘版的 trace、manifest 更新 | 1 天 |

PR1 可與 PR2 併成一個，省一輪審查但審查面變大；我建議分開，因為 PR1 單獨就修了一個現存的潛在計數 bug。**最快的 release milestone = PR1+PR2**（內部使用：Kevin/admin 經 CLI 核准）；PR3 是形式驗證的接點，不擋內部使用，但擋「模型對真程式」這個宣稱。

## 3. 回放銜接

genesis-deck `tests/formal/test_strategy_queue_conformance.py` 的 `replay(trace, cls=StrategyQueue)` 只依賴以下**鴨子型別**介面（讀自該檔，未改動它）：

| adapter 必須提供 | 內容 |
|---|---|
| 方法 | `add_prereg(sid)`、`submit(sid)`、`approve(sid, who)`、`retire(sid)`，回傳 `"ok"/"queued"/"noop"/"approved"/"rejected"/"retired"` |
| 狀態屬性 | `prereg_ids: set`、`status: dict`（只含已提交者，值為 pending/approved/retired）、`runs: dict`、`trials: dict`、`approved_by: dict`、`signed: set`、`pool: set` |
| 建構子 | 無參數可建構（`cls()`），核准者預設含 `kevin`、`admin` |

對真實作的 adapter（`tests/formal/queue_adapter.py`，放 gs-strategy）要靠 `SubmissionQueue` 開出這些 hook，**所以 §2.2 的介面刻意留了這些縫**：

1. **可注入 `root`**：每個測試一個 `tmp_path` 內的暫時 repo（`git init`），不碰真的 `log/`。
2. **可注入 `backtest`**：計數樁，`runs[sid]` 取自樁的**觀測值**，不從 ledger 讀——否則 `TrialLedger`（trials = runs）會變成同義反覆。
3. **`snapshot()` 唯讀快照**：`trials` 由 ledger 重摺（`audit_ledger` 的去重加總）算出，`signed`/`pool` 由 `approval` 區塊與 retired 事件算出，`prereg_ids` 由 git HEAD 的登記檔算出。
4. **sid 映射**：TLC 的 `s1`、`s2` 只是名字；adapter 對每個名字產生固定內容的 bundle，維護 `名字 → submission_id` 表，`status`/`runs` 等 dict 再用名字回報。
5. **`add_prereg(sid)`**：在暫時 repo 寫一份通過 `validate()` 的登記檔並 `git commit`（因為 `submit` 驗的是 HEAD）。
6. **負控制 seam**：`SubmissionQueue` 把「寫 trial」「檢查 prereg」「執行回測」「簽核」拆成可覆寫的私有方法，adapter 的 7 個壞子類才有地方動手腳（`SkipTrial`、`TrialDec`、`NoPrereg`、`SubmitRuns`、`DupApprove`、`Resubmit`、`NoSign`）。這 7 個子類與「回放對應反例必須失敗」的斷言可以從 genesis-deck 測試原樣搬。

**放哪裡跑**：建議回放測試放 **gs-strategy**，trace（`tests/formal/traces/queue-*.json`，9 個）從 genesis-deck 釘 commit 複製進來，旁附 `traces.sha256`＋來源 commit。理由：genesis-deck CI 不該為了跑這一個測試去 checkout 另一個 private repo 並管 token。genesis-deck 端的小 PR：manifest 的 `models_spec_not_code` 改 `false`、`actions[].file:line` 改指向 gs-strategy 的函式（釘 commit）、`strategy-queue.md` 的「未對真實程式」那一條改寫為實際涵蓋範圍；`strategy_queue_ref.py` 保留當 spec oracle，並可加一條**差分測試**（同一軌跡丟兩個實作，結果必須一致）——這比單純「換掉」更能看出 adapter 與參考實作之間的偏離。

**回放涵蓋不到的**（照實寫）：並發、crash、`trials=1/runs=0` 中間態、ledger 的 git 層行為（hook、`--no-verify`）、真實回測。這些由 §2.6 的單元測試覆蓋，不在形式模型內。

## 4. 風險與未確認項

**阻擋外網交易員上線的**

- **核准 = 在主機上執行外部提交的程式碼。** 靜態檢查規則 genesis-deck §7 自己寫「尚未定義」，本設計的 AST 黑名單只是佔位，不是沙箱。僅供內部（Kevin/admin 自己讀過程式再核准）可以先上；對外開放前必須有真的隔離（無網路、唯讀 bundle、資源上限），這件事**沒有設計**。
- **核准者身分。** CLI 的 `--as` 只是字串；名單檔加 `git config user.email` 比對，擋得住手誤擋不住惡意。要有強度需要 GPG 簽章 commit＋CI 驗簽，或沿用 genesis-deck 的 EdDSA 斷言；本設計不選，列為待決。

**規格 §7 的未確認項**

- **「核准者即簽核者」**：本設計記成 `approved_by` 與 `signed_by` **兩個欄位、值相同**，之後要拆（例如簽核須由另一人）只改守衛，不改 schema。尚未與 `auto-research-funnel.md` L1「經人簽核」對照——該處的簽核對象是 **L1 假說產出**，與此處「核准回測」是否同一個人、同一個動作，沒確認。
- **prereg 晉升路徑**：genesis-deck 的草稿在 vault，gs-strategy 的登記檔要 commit 且凍結、檔名 `NNN-<slug>.yaml`（NNN 遞增不重用）。誰把草稿搬進來、NNN 誰分配、草稿格式與 `preregistration/1` schema 是否相容——**都未驗證**。MVP 假設由人工 PR 晉升。
- **簽核不能寫進登記檔**（凍結），所以只能在 ledger；README 的 GPG tag 存證沒有任何程式讀取，本設計不接。

**實作風險**

- **ledger 是 git 追蹤檔，多處並發會 merge 衝突。** flock 只擋同一台機器。gs-strategy 正在搬到 gsinvest（Forgejo；見 PR 44、48）。假設：**核准只在單一主機執行**。這個假設沒有機制強制。
- **flock 僅在 WSL/Linux 檔案系統可靠**；本設計不支援從 Windows 端經 UNC 路徑執行 `approve`。
- **CI 沒有跑 append-only 檢查**（§1.1），PR1 補上之前，ledger 的保護只剩本機 hook。
- **`--no-verify` 與直接編輯檔案**：任何有 shell 的人都能繞過；設計目標是「繞過會留痕」，不是「不可能繞過」，與現有 hook 的立場一致。
- **harness 的 `collect()`** 是否也是「同 id 取最後一筆」，我只讀到註解（`stat_tree.py:155`、`check_append_only.py` 文件字串）這樣宣稱，沒有讀 harness 程式本身。

**模型要回頭補的**

- 沒有 `Reject`（待審被拒）：實務上待審佇列需要它，且它不改 trial（`runs`、`trials` 不變），加進 `Queue.tla` 應不影響六個不變量，但需重跑 TLC。MVP 不做，所以佇列裡的壞提交只能留在 PENDING。
- 「改版重跑」「prereg 被刪」「crash 後重放」「並發核准」模型都沒有，本設計用「改版 = 新 id」迴避第一項，其餘由測試而非形式驗證覆蓋。
- 每個 prereg 的 `planned_trials` 上限（`TEMPLATE.yaml` 有此欄）沒有進入模型也沒有進入 MVP 的守衛；多次提交同一 prereg 目前不受上限約束。

**統計有效性不在範圍**：TLA+ 與本佇列只保證「trial 有被如實計數、簽核有留痕」；計數之後 DSR／ADDIS 的判斷是否正確，是 `validation/` 的事。

## 5. 驗證狀態

沒有執行任何程式或測試。本文的事實性陳述來源：讀取 genesis-deck `origin/main` 與 gs-strategy `origin/main`（`deed371`）的檔案內容、對 `log/trials.jsonl` 做行數／`trial_id`／`delta_n` 統計、對 `strategies/_common/validation/*.py` 與 `ci.yml` 做 grep。§2、§3 是設計，沒有原型。工量與 PR 切分是估計。
