# gs-strategy 搬遷到 gsinvest 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 StratyUI、每日爬蟲、LLM 自動寫策略在 gsinvest 上被 joker／kevin／jason 安全共用，統計紀律（全域 N、事前登記、帳本只能追加）由伺服器強制。

**Architecture:** 程式碼唯讀、資料放 docker named volume（單寫者）；StratyUI 改為每人一個 session、共用帳本；帳本加雜湊鏈與登記／配額欄位；Forgejo 承載權威 repo 與 pre-receive；備份到 jsonhuang、每日 seal 推 GitHub。規格：`docs/spec/host-migration-gsinvest.md`（以下簡稱「規格」，章節以 §n 引用）。

**Tech Stack:** Python 3.12、pytest、stdlib `http.server`（現有）、PyJWT＋cryptography（JWT 驗證，新增 pin 版本）、docker／compose、Forgejo、rsync＋ssh、age、sqlite3。

## Global Constraints

- 只動 gs-strategy；gs-zipline-tej／FORGE／MINT 只當介面，不修改。
- 不得 push 到 main／master；一律 `dev/*` 分支＋PR。本計畫在 `dev/host-migration-spec` 起頭，實作請各開 `dev/host-<主題>`。
- commit 訊息主體用繁體中文，prefix 與識別符保留原文；結尾附 `Co-Authored-By` 行。
- TEJ 金鑰不得出現在輸出、日誌、映像層、測試 fixture（測試用執行期組成的假金鑰）。
- 所有 `ssh`／運維步驟（Part B）**在使用者審閱規格後才執行**；本計畫寫作階段不動正式環境、不動 gsinvest。
- gsinvest 無免密 sudo：只用 docker、`systemctl --user`、使用者目錄。
- 每項預設行為不變：未設 `GS_DATA_ROOT`、`GS_ROLES_FILE` 等環境變數時，Kevin PC 的現有流程與全部既有測試必須照舊通過（回退要靠這一點）。
- 測試指令（WSL，repo 根）：`.venv/bin/python -m pytest -q`。

## File Structure

新增：
- `strategies/_common/paths.py` — 唯一的可寫路徑解析（`GS_DATA_ROOT`）。
- `strategies/_common/graph/chain.py` — 雜湊鏈：`line_hash`、`append_chained`、`verify_chain`。
- `scripts/verify_ledger_chain.py` — CLI：驗鏈，非零退出並指出第一個壞行。
- `scripts/seal_ledger.py` — 產生 `seals/YYYY-MM-DD.json`。
- `strategies/_common/graph/roles.py` — 讀 `roles.yaml`，`Roles.is_executor/is_signer/is_admin`。
- `strategies/_common/graph/identity.py` — `resolve_actor(headers, peer_ip, cfg)`；含 CF Access JWT 驗證。
- `strategies/_common/graph/prereg.py` — 讀登記＋signoff＋closure，回傳 `PreregState`（active／used／remaining）。
- `strategies/_common/graph/gate.py` — `check_run_gate(...)`：執行閘門的全部規則（§4.4）。
- `strategies/_common/graph/sessions.py` — `SessionRegistry` 與回測 semaphore。
- `scripts/pre_receive.py` — 伺服器端 pre-receive 邏輯（純函式＋薄 CLI）。
- `scripts/check_signoff.py` — signoff 檔驗證。
- `quant_crawler/redact.py` — 日誌遮蔽過濾器。
- `deploy/gsinvest/` — `Dockerfile`、`compose.yaml`、`hooks/pre-receive`、`backup/*.sh`、`runbooks/*.md`。
- 測試：`tests/test_paths.py`、`test_ledger_chain.py`、`test_roles_identity.py`、`test_prereg_state.py`、`test_run_gate.py`、`test_sessions.py`、`test_pre_receive.py`、`test_redact.py`。

修改：`strategies/_common/graph/{ledger,service,api,__main__}.py`、`strategies/_common/validation/report.py`、`strategies/_common/results.py`、`quant_crawler/webui/stat_tree.py`、`scripts/{check_preregistration,check_append_only,daily_refresh.sh}`、`experiments/preregistration/{README.md,TEMPLATE.yaml}`。

---

# Part A：gs-strategy 程式變更（可在 WSL／CI 驗證，不碰正式環境）

### Task 1: 可寫路徑單一來源 `GS_DATA_ROOT`（規格 §3 F1）

**Files:**
- Create: `strategies/_common/paths.py`, `tests/test_paths.py`
- Modify: `strategies/_common/graph/service.py:234-238`、`strategies/_common/results.py:59`、`quant_crawler/webui/stat_tree.py:31-35`、`scripts/triage_generated.py:59`、`scripts/check_preregistration.py`（`PREREG_DIR`）、`scripts/daily_refresh.sh`

**Interfaces:**
- Produces: `data_root() -> Path`（`GS_DATA_ROOT` 或 repo 根）、`ledger_path()`、`results_db_path()`、`cache_dir()`、`graph_runs_dir()`、`prereg_dir()`、`generated_dir()`、`llm_generated_dir()`、`overlay_dir()`。全部回傳 `Path`，不建立目錄。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_paths.py
from pathlib import Path
import importlib

def _paths(monkeypatch, root=None):
    if root is None:
        monkeypatch.delenv("GS_DATA_ROOT", raising=False)
    else:
        monkeypatch.setenv("GS_DATA_ROOT", str(root))
    import strategies._common.paths as p
    return importlib.reload(p)

def test_default_is_repo_root(monkeypatch):
    p = _paths(monkeypatch)
    repo = Path(__file__).resolve().parents[1]
    assert p.ledger_path() == repo / "log" / "trials.jsonl"
    assert p.prereg_dir() == repo / "experiments" / "preregistration"

def test_env_overrides_everything(monkeypatch, tmp_path):
    p = _paths(monkeypatch, tmp_path)
    assert p.ledger_path() == tmp_path / "log" / "trials.jsonl"
    assert p.results_db_path() == tmp_path / "data" / "backtests.sqlite"
    assert p.cache_dir() == tmp_path / ".cache" / "live-strategy-graph"
    assert p.graph_runs_dir() == tmp_path / ".graph-runs"
    assert p.overlay_dir() == tmp_path / "manifest-overlay"
```

- [ ] **Step 2: 執行確認失敗**
Run: `.venv/bin/python -m pytest tests/test_paths.py -q` → Expected: FAIL（`ModuleNotFoundError: strategies._common.paths`）

- [ ] **Step 3: 實作**

```python
# strategies/_common/paths.py
"""所有「會被寫入」的路徑都從這裡解析；程式碼本身可以是唯讀 checkout。"""
from __future__ import annotations
import os
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

def data_root() -> Path:
    v = os.environ.get("GS_DATA_ROOT")
    return Path(v).resolve() if v else _REPO

def ledger_path() -> Path: return data_root() / "log" / "trials.jsonl"
def results_db_path() -> Path: return data_root() / "data" / "backtests.sqlite"
def cache_dir() -> Path: return data_root() / ".cache" / "live-strategy-graph"
def graph_runs_dir() -> Path: return data_root() / ".graph-runs"
def prereg_dir() -> Path: return data_root() / "experiments" / "preregistration"
def generated_dir() -> Path: return data_root() / "strategies" / "_generated"
def llm_generated_dir() -> Path: return data_root() / "strategies" / "_llm_generated"
def overlay_dir() -> Path: return data_root() / "manifest-overlay"
```

- [ ] **Step 4: 把上列修改檔的硬編碼路徑換成 `paths.*`**（`GraphService.__init__` 的預設 `cache_dir`／`ledger_path`、`results.py` 的 `db_path`、`stat_tree.py` 的 `LEDGER`／`PREREG_DIR`／`GENERATED`、`triage_generated.py` 的 `LEDGER`、`check_preregistration.py` 的 `PREREG_DIR`；`daily_refresh.sh` 以 `DATA_ROOT="${GS_DATA_ROOT:-$ROOT_DIR}"` 取代 `data/` 前綴）。`.graph-runs` 的寫入點（`service.py` 內 `self.root / '.graph-runs'`）改 `paths.graph_runs_dir()`，`ledger._config_ref` 的相對路徑語意不變。

- [ ] **Step 5: 全套回歸，兩種環境**
Run: `.venv/bin/python -m pytest -q` 與 `GS_DATA_ROOT=$(mktemp -d) .venv/bin/python -m pytest -q`
Expected: 兩次皆 PASS（第二次需 conftest 為 tmp 根建立必要子目錄；若有測試假設 repo 根可寫，改用 fixture 建立）。

- [ ] **Step 6: Commit**
```bash
git add strategies/_common/paths.py tests/test_paths.py strategies scripts quant_crawler
git commit -m "feat: 新增 GS_DATA_ROOT，可寫路徑集中解析（程式碼可唯讀）"
```

### Task 2: manifest 回寫改走 overlay（規格 §3 F2）

**Files:**
- Modify: `strategies/_common/validation/report.py`（`apply_to_manifest`，約 :348）、`strategies/_common/runner.py:126-133`
- Test: `tests/test_manifest_overlay.py`

**Interfaces:**
- Consumes: Task 1 `paths.overlay_dir()`。
- Produces: `GS_READONLY_CODE=1` 時 `apply_to_manifest(manifest_path, report)` 寫到 `overlay_dir()/<strategy_dir_name>.yaml` 並回傳該路徑；`load_manifest(path)` 先讀 overlay 再讀原檔。未設時行為不變。

- [ ] **Step 1: 失敗測試**：建立 tmp 策略目錄含 `manifest.yaml`，`chmod -w`，設 `GS_READONLY_CODE=1`、`GS_DATA_ROOT=tmp`；呼叫 `apply_to_manifest`；斷言：不拋例外、原檔 `sha256` 不變、`overlay_dir()/<name>.yaml` 存在且含 validation 區塊；再斷言未設 `GS_READONLY_CODE` 時原檔被改（回歸）。
- [ ] **Step 2: 跑測試確認失敗** `pytest tests/test_manifest_overlay.py -q` → FAIL。
- [ ] **Step 3: 實作**：在 `apply_to_manifest` 開頭 `if os.environ.get("GS_READONLY_CODE") == "1": target = overlay_dir()/f"{manifest_path.parent.name}.yaml"; target.parent.mkdir(parents=True, exist_ok=True); 以原 manifest 內容（若 overlay 已有則以 overlay 為底）套用同一段合併邏輯後寫入 target`；其餘讀取 manifest 的呼叫（`runner.py` 的 `manifest if manifest.exists()`）改為經 `resolve_manifest(path)`：overlay 存在則回 overlay。寫檔用「寫暫存檔＋`os.replace`」避免多人同時回測同策略時撕裂。
- [ ] **Step 4: 通過** → PASS；另跑 `pytest -q` 全套。
- [ ] **Step 5: Commit** `fix: GS_READONLY_CODE 下 manifest 驗證區塊回寫到 overlay，不再改程式碼 checkout`

### Task 3: 帳本雜湊鏈與驗證工具（規格 §4.3 F5）

**Files:**
- Create: `strategies/_common/graph/chain.py`、`scripts/verify_ledger_chain.py`、`tests/test_ledger_chain.py`
- Modify: `strategies/_common/graph/ledger.py`（`record_success` 的寫入段）、`scripts/triage_generated.py`（寫 screening 的 append 路徑改走 `append_chained`）

**Interfaces:**
- Produces:
  - `line_hash(line_bytes: bytes) -> str`（SHA-256 hex，輸入**不含**結尾 LF）
  - `append_chained(path: Path, record: dict, *, canonical) -> dict`：在呼叫端已持有 `_exclusive` 鎖的前提下，讀尾行、補 `prev_hash` 與 `seq`、fsync 追加；回傳最終 record。若檔內尚無 `chain_anchor`，**先**追加一筆 `{"schema":"research-trial/v2","kind":"chain_anchor","legacy_lines":N,"legacy_sha256":sha256(舊檔全文位元組),"prev_hash":sha256(舊檔全文位元組),"seq":0}` 再追加本筆。
  - `verify_chain(path: Path) -> ChainReport(ok: bool, lines: int, head_hash: str, first_bad_line: int | None, reason: str | None)`

- [ ] **Step 1: 失敗測試**

```python
# tests/test_ledger_chain.py
import json
from pathlib import Path
from strategies._common.graph.chain import append_chained, verify_chain, line_hash
from strategies._common.graph.core import canonical

LEGACY = '{"schema":"research-trial/v1","trial_id":"a"}\n{"schema":"research-trial/v1","trial_id":"b"}\n'

def _ledger(tmp_path, legacy=LEGACY):
    p = tmp_path / "trials.jsonl"
    p.write_text(legacy, encoding="utf-8")
    return p

def test_first_append_inserts_anchor(tmp_path):
    p = _ledger(tmp_path)
    append_chained(p, {"schema": "research-trial/v2", "trial_id": "c"}, canonical=canonical)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    anchor = json.loads(lines[2])
    assert anchor["kind"] == "chain_anchor" and anchor["legacy_lines"] == 2
    assert json.loads(lines[3])["prev_hash"] == line_hash(lines[2].encode())
    assert verify_chain(p).ok

def test_tamper_legacy_detected(tmp_path):
    p = _ledger(tmp_path)
    append_chained(p, {"schema": "research-trial/v2", "trial_id": "c"}, canonical=canonical)
    p.write_text(p.read_text(encoding="utf-8").replace('"a"', '"x"', 1), encoding="utf-8")
    r = verify_chain(p)
    assert not r.ok and r.first_bad_line == 3 and "legacy" in r.reason

def test_tamper_chained_line_detected(tmp_path):
    p = _ledger(tmp_path)
    for t in "cde":
        append_chained(p, {"schema": "research-trial/v2", "trial_id": t}, canonical=canonical)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[3] = lines[3].replace('"c"', '"z"')
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = verify_chain(p)
    assert not r.ok and r.first_bad_line == 5

def test_deleted_line_detected(tmp_path):
    p = _ledger(tmp_path)
    for t in "cde":
        append_chained(p, {"schema": "research-trial/v2", "trial_id": t}, canonical=canonical)
    lines = p.read_text(encoding="utf-8").splitlines()
    del lines[3]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert not verify_chain(p).ok

def test_cli_exit_code(tmp_path):
    import subprocess, sys
    p = _ledger(tmp_path)
    append_chained(p, {"schema": "research-trial/v2", "trial_id": "c"}, canonical=canonical)
    ok = subprocess.run([sys.executable, "scripts/verify_ledger_chain.py", str(p)])
    assert ok.returncode == 0
    p.write_text(p.read_text(encoding="utf-8").replace('"b"', '"y"', 1), encoding="utf-8")
    bad = subprocess.run([sys.executable, "scripts/verify_ledger_chain.py", str(p)])
    assert bad.returncode != 0
```

- [ ] **Step 2: 確認失敗** `pytest tests/test_ledger_chain.py -q` → FAIL（模組不存在）。
- [ ] **Step 3: 實作 `chain.py`**

```python
# strategies/_common/graph/chain.py
from __future__ import annotations
import hashlib, json, os
from dataclasses import dataclass
from pathlib import Path

def line_hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

@dataclass
class ChainReport:
    ok: bool
    lines: int
    head_hash: str
    first_bad_line: int | None = None
    reason: str | None = None

def _split(path: Path):
    raw = path.read_bytes() if path.exists() else b""
    return raw, [l for l in raw.split(b"\n") if l != b""]

def _anchor_index(lines):
    for i, l in enumerate(lines):
        try:
            if json.loads(l).get("kind") == "chain_anchor":
                return i
        except ValueError:
            pass
    return None

def append_chained(path: Path, record: dict, *, canonical) -> dict:
    raw, lines = _split(path)
    idx = _anchor_index(lines)
    if idx is None:
        legacy = raw if (not raw or raw.endswith(b"\n")) else raw + b"\n"
        anchor = {"schema": "research-trial/v2", "kind": "chain_anchor",
                  "legacy_lines": len(lines), "legacy_sha256": hashlib.sha256(legacy).hexdigest(),
                  "prev_hash": hashlib.sha256(legacy).hexdigest(), "seq": 0}
        _write(path, legacy, canonical(anchor))
        raw, lines = _split(path)
    last = lines[-1]
    last_seq = json.loads(last).get("seq", 0)
    rec = {**record, "prev_hash": line_hash(last), "seq": last_seq + 1}
    _write(path, raw if raw.endswith(b"\n") else raw + b"\n", canonical(rec))
    return rec

def _write(path: Path, existing: bytes, line: str):
    with path.open("ab") as h:
        if path.stat().st_size and not existing.endswith(b"\n"):
            h.write(b"\n")
        h.write(line.encode("utf-8") + b"\n")
        h.flush(); os.fsync(h.fileno())

def verify_chain(path: Path) -> ChainReport:
    raw, lines = _split(path)
    idx = _anchor_index(lines)
    if idx is None:
        return ChainReport(True, len(lines), line_hash(lines[-1]) if lines else "", None, "no anchor (legacy only)")
    anchor = json.loads(lines[idx])
    legacy = b"".join(l + b"\n" for l in lines[:idx])
    if hashlib.sha256(legacy).hexdigest() != anchor["legacy_sha256"] or idx != anchor["legacy_lines"]:
        return ChainReport(False, len(lines), "", idx + 1, "legacy section differs from anchor")
    prev = line_hash(lines[idx]); seq = anchor["seq"]
    for i in range(idx + 1, len(lines)):
        rec = json.loads(lines[i])
        if rec.get("prev_hash") != prev:
            return ChainReport(False, len(lines), "", i + 1, "prev_hash mismatch")
        if rec.get("seq") != seq + 1:
            return ChainReport(False, len(lines), "", i + 1, "seq gap")
        prev, seq = line_hash(lines[i]), rec["seq"]
    return ChainReport(True, len(lines), prev)
```

`scripts/verify_ledger_chain.py`：`argparse` 取路徑（預設 `paths.ledger_path()`），呼叫 `verify_chain`，印中文結果與 `head_hash`；`ok` 回 0 否則 1。
- [ ] **Step 4: 通過** `pytest tests/test_ledger_chain.py -q` → PASS。
- [ ] **Step 5: 接線**：`ledger.py::record_success` 的手動 `handle.write(prefix + data)` 區塊換成 `append_chained(self.path, record, canonical=canonical)`（鎖已持有）；既有測試 `tests/test_live_graph_ledger.py` 的期望若逐位元組比對，改為忽略 `prev_hash`／`seq`。`triage_generated.py --apply` 的 append 同樣改用。跑 `pytest tests/test_live_graph_ledger.py tests/test_triage_generated.py -q` → PASS。
- [ ] **Step 6: 並行寫入測試**：新增 `test_concurrent_writers_keep_chain`——16 個 `multiprocessing` 程序各以 `_exclusive` 鎖追加 5 筆**不同**記錄，結束後 `verify_chain(p).ok` 且行數＝anchor＋80（補足 bench 未測的「16 個不同設定同時寫」）。
- [ ] **Step 7: Commit** `feat: 帳本加上雜湊鏈與驗證工具（research-trial/v2 起帶 prev_hash／seq）`

### Task 4: 角色名單與身分解析（規格 §4.1 F3）

**Files:**
- Create: `strategies/_common/graph/roles.py`、`strategies/_common/graph/identity.py`、`tests/test_roles_identity.py`
- Modify: `strategies/_common/graph/api.py:57-62`、`strategies/_common/graph/__main__.py`（新參數 `--roles`、`--trusted-proxy`、`--cf-team-domain`、`--cf-aud`）、`requirements.txt`（pin `PyJWT[crypto]`）

**Interfaces:**
- Produces:
  - `Roles.load(path|None) -> Roles`（None＝未啟用：所有人視為 executor，維持現況）；`roles.is_executor(email)`、`is_signer`、`is_admin`（email 比對小寫）。
  - `resolve_actor(headers, peer_ip, cfg) -> str | None`：`cfg` 含 `trusted_proxies: set[str]`、`cf: CfAccess | None`。規則：有 `Cf-Access-Jwt-Assertion` 且 `cf` 設定 → 驗簽章／`aud`／`iss`／`exp`，成功回 `claims["email"].lower()`，失敗回 None（**不**降級採信其他標頭）；否則 `peer_ip in trusted_proxies` 才採信 `X-Auth-Request-Email`；其他回 None。

- [ ] **Step 1: 失敗測試**（重點案例，JWT 以測試內產生的 RSA 金鑰簽、注入 JWKS 取得函式）

```python
# tests/test_roles_identity.py（節錄，完整案例照列）
def test_untrusted_peer_header_ignored():
    cfg = IdCfg(trusted_proxies={"192.168.1.5"}, cf=None)
    assert resolve_actor({"X-Auth-Request-Email": "evil@x"}, "10.0.0.9", cfg) is None

def test_trusted_proxy_header_accepted_and_lowercased():
    cfg = IdCfg(trusted_proxies={"192.168.1.5"}, cf=None)
    assert resolve_actor({"X-Auth-Request-Email": "Kevin@X.com"}, "192.168.1.5", cfg) == "kevin@x.com"

def test_cf_jwt_valid(cf_env):          # cf_env: 產生金鑰、簽 {email,aud,iss,exp}
    assert resolve_actor({"Cf-Access-Jwt-Assertion": cf_env.token()}, "1.2.3.4", cf_env.cfg) == "jason@x.com"

def test_cf_jwt_wrong_aud_expired_badsig_all_none(cf_env):
    for bad in (cf_env.token(aud="other"), cf_env.token(exp=-10), cf_env.token(sign_with_other_key=True)):
        assert resolve_actor({"Cf-Access-Jwt-Assertion": bad, "X-Auth-Request-Email": "kevin@x.com"},
                             "192.168.1.5", cf_env.cfg) is None

def test_roles_file(tmp_path):
    f = tmp_path / "roles.yaml"
    f.write_text("executors: [A@x.com]\nsigners: [a@x.com]\nadmins: []\n")
    r = Roles.load(f)
    assert r.is_executor("a@x.com") and r.is_signer("A@X.COM") and not r.is_admin("a@x.com")

def test_roles_disabled_means_open():
    assert Roles.load(None).is_executor("anyone@x.com")
```

- [ ] **Step 2: 確認失敗**。
- [ ] **Step 3: 實作** `roles.py`（`yaml.safe_load`，小寫正規化，檔案缺欄位視為空名單）與 `identity.py`（`jwt.decode(token, key, algorithms=["RS256"], audience=cfg.cf.aud, issuer=f"https://{cfg.cf.team_domain}")`；JWKS 以 `https://<team>/cdn-cgi/access/certs` 取得，快取 1 小時，取得函式可注入供測試）。`api.py::dispatch` 以 `resolve_actor(self.headers, self.client_address[0], self.server.identity_cfg)` 取代原本的 `actor = ... if public else None`；`GraphHTTPServer.__init__` 增加 `roles`、`identity_cfg` 參數（預設 None＝現況行為）。
- [ ] **Step 4: 通過** `pytest tests/test_roles_identity.py tests/test_live_graph_api.py tests/test_intranet_host.py -q` → PASS（後兩個為回歸）。
- [ ] **Step 5: Commit** `feat: 角色名單與身分解析（受信代理來源檢查、Cloudflare Access JWT 驗證）`

### Task 5: 登記狀態、簽核驗證與 planned_trials 上限（規格 §4.4、§5.3）

**Files:**
- Create: `strategies/_common/graph/prereg.py`、`scripts/check_signoff.py`、`tests/test_prereg_state.py`
- Modify: `scripts/check_preregistration.py`（`validate` 加規則）、`experiments/preregistration/{TEMPLATE.yaml,README.md}`（說明 signoff／closure 與上限）

**Interfaces:**
- Produces:
  - `load_registrations(prereg_dir: Path, ledger_records: list[dict], roles: Roles) -> dict[str, PreregState]`，`PreregState(id, author, family, planned_trials, signed_by: str|None, closed: bool, used: int, status: "pending_signoff"|"active"|"exhausted"|"closed")`；`used`＝帳本中 `prereg_id==id` 且 `purpose=="selection"` 的記錄數；`signed_by` 僅當 signoff 的 `prereg_sha256` 與登記檔實際雜湊相符、signer 在 `roles.signers`、signer≠author 時才有值，否則視為未簽核。
  - `check_signoff.validate(signoff_path, registration_path, roles, pusher_email=None) -> list[str]`（回傳問題清單，空＝通過）。
  - `check_preregistration.validate` 新增：`family != "open_mining"` 且 `thresholds.planned_trials` 為 int 且 `> 10` → 問題「planned_trials 超過 10 須改走 open_mining」；`planned_trials` 缺或非正整數 → 問題。

- [ ] **Step 1: 失敗測試**（以 tmp 目錄建登記／signoff 檔，涵蓋）：未簽核＝`pending_signoff`；有效 signoff＝`active`；signoff 的 sha256 不符（登記被改）＝`pending_signoff`；signer＝author＝無效；signer 不在名單＝無效；`used==planned_trials`＝`exhausted`；有 closure＝`closed`；`planned_trials=11` 且 `family=single_preregistered` 的 `validate` 回傳問題，同值 `open_mining` 不回傳。
- [ ] **Step 2: 確認失敗** → FAIL。
- [ ] **Step 3: 實作**（`yaml.safe_load`；登記檔雜湊＝檔案位元組 SHA-256；檔名慣例 `NNN-slug.yaml`／`.signoff.yaml`／`.closure.yaml`，TEMPLATE 與 README 排除，沿用 `PREREG_NON_REGISTRATION`）。
- [ ] **Step 4: 通過** `pytest tests/test_prereg_state.py tests/test_append_only_and_prereg.py -q` → PASS。
- [ ] **Step 5: Commit** `feat: 事前登記狀態機（簽核／配額／關閉）與 planned_trials 上限驗證`

### Task 6: 執行閘門與帳本新欄位（規格 §4.4）

**Files:**
- Create: `strategies/_common/graph/gate.py`、`tests/test_run_gate.py`
- Modify: `strategies/_common/graph/service.py`（`start()` 呼叫閘門；`services` 加 `prereg_id`）、`strategies/_common/graph/ledger.py`（記錄加 `actor`、`prereg_id`、`prereg_commit`、`family`、`prereg_trial_index`、`schema: research-trial/v2`）、`strategies/_common/graph/api.py`（把 `GateError` 對應 403/409）

**Interfaces:**
- Consumes: Task 3 `append_chained`；Task 4 `Roles`；Task 5 `load_registrations`。
- Produces: `check_run_gate(*, actor, roles, regs, prereg_id, selection_n, approvals_ok, purpose) -> GateResult(allowed: bool, status: 403|409|200, code: str, message_zh: str, warning: str|None)`。`code ∈ {not_executor, no_prereg, prereg_not_active, exhausted, author_limit, n_block60, service_selection_forbidden, ok}`。

- [ ] **Step 1: 失敗測試**（每條規則一個）：

```python
def test_not_executor_403(): ...        # actor 不在 executors → status 403, code not_executor
def test_no_prereg_409(): ...           # prereg_id None 且 purpose selection → 409 no_prereg
def test_pending_signoff_409(): ...     # status pending_signoff → prereg_not_active
def test_exhausted_409(): ...           # used == planned_trials (family != open_mining)
def test_open_mining_ignores_planned_trials(): ...
def test_n_29_ok_30_warn_60_block():    # 29 → ok；30、59 → allowed 且 warning 非空；60 且 approvals_ok False → 409 n_block60
def test_service_actor_cannot_select(): # actor == "service:cron" and purpose == "selection" → 403 service_selection_forbidden
def test_screening_purpose_never_goes_through_run(): ...
```
並在 `tests/test_live_graph_ledger.py` 補：記錄含 `actor`、`prereg_id`、`schema=="research-trial/v2"`、`prereg_trial_index == used+1`，且相同 `backtest_key` 重播仍不增加 N。

- [ ] **Step 2: 確認失敗**。
- [ ] **Step 3: 實作** `gate.py` 為純函式（無 I/O，便於測試）；`service.start()` 在 `ledger.estimate` 同一把鎖內讀帳本記錄→`load_registrations`→`check_run_gate`；未啟用 roles（`Roles` 為 None）時完全略過閘門以維持現況（Kevin PC 回退路徑）。`GateError` 由 `api.py` 轉成 `reply(status, {'error': message_zh, 'code': code})`。閘門失敗**不得**寫帳本、不得啟動 job。
- [ ] **Step 4: 通過** `pytest tests/test_run_gate.py tests/test_live_graph_ledger.py tests/test_live_graph_api.py -q` → PASS。
- [ ] **Step 5: Commit** `feat: /api/run 執行閘門（名單、已簽核登記、配額、N 超線）與帳本記錄 actor／prereg_id`

### Task 7: N 提示線、run-estimate 擴充與超線核准（規格 §4.5）

**Files:**
- Modify: `strategies/_common/graph/service.py::run_estimate`、`strategies/_common/graph/api.py`、`strategies/_common/graph/ledger.py`（新增 `append_event(kind, actor, payload)`、`approvals_state()`）
- Test: `tests/test_n_lines.py`
- UI：`strategies/_common/graph/ui/`（確認視窗顯示「再 +1 會讓運氣門檻從 a 變成 b」；沿用現有前端結構，先讀 `ui/` 再改）

**Interfaces:**
- Produces: `run_estimate()` 回傳新增 `threshold_now`、`threshold_next`、`line`；`POST /api/n-approval`（signer 發起）、`POST /api/n-approval/<id>/approve`（另一位 signer）；帳本 `kind ∈ {n_approval_request, n_approval_grant}`；`approvals_state(n) -> bool`：存在一筆 grant 且 `from_n <= n < from_n + 10`。

- [ ] **Step 1: 失敗測試**：`line` 在 N=29/30/59/60 分別為 `ok/warn30/warn30/block60`；`threshold_next > threshold_now` 且值等於 `expected_max_sharpe(n)`；發起者自己核准 → 403；第二位 signer 核准後 `approvals_state(60)` 為真、`approvals_state(70)` 為假；非 signer 發起 → 403；兩筆事件在帳本中皆帶合法 `prev_hash`。
- [ ] **Step 2: 失敗確認 → Step 3: 實作 → Step 4: 通過**（`pytest tests/test_n_lines.py -q`）。
- [ ] **Step 5: 前端**：確認視窗文案與 `warn30`（黃）／`block60`（紅、顯示「需 Joker 與 Kevin 共同核准」）；以 `tests/e2e` 現有 Playwright 流程補一個斷言。
- [ ] **Step 6: Commit** `feat: N 提示線、運氣門檻預告與超線雙簽核准`

### Task 8: 每人一個 session 與回測 semaphore（規格 §4.2 F4）

**Files:**
- Create: `strategies/_common/graph/sessions.py`、`tests/test_sessions.py`
- Modify: `strategies/_common/graph/api.py`（`GraphHTTPServer.service` → `sessions.get(actor)`）、`strategies/_common/graph/service.py`（`GraphService` 接受共用 `ledger`／`results` 與 `backtest_slot` 回呼）

**Interfaces:**
- Produces: `SessionRegistry(root, ledger, max_backtests=8, idle_minutes=60)`，`.get(actor: str|None) -> GraphService`（None 回唯讀共用 session）、`.set_limit(n)`、`.load() -> {"running": int, "queued": int}`；`BacktestSlots.acquire(timeout=None)`／`release()`（`threading.BoundedSemaphore` 包一層可調上限）。

- [ ] **Step 1: 失敗測試**：A、B 兩個 actor 各自 `select_strategy` 不同策略，`revision`／`graph` 互不影響；兩者共用同一個 `SelectionLedger` 物件；第 9 個同時回測排隊（`load()["queued"]==1`）；`set_limit(2)` 立即生效於新請求；閒置超過 `idle_minutes` 的 session 被卸載、下次 `get` 重建；匿名 actor 的 `/api/run` 被拒（由 Task 6 閘門）。
- [ ] **Step 2–4:** 失敗→實作→通過（`pytest tests/test_sessions.py tests/test_live_graph_conflicts.py -q`；後者為回歸，單一使用者行為不變）。
- [ ] **Step 5: e2e**：`tests/e2e` 新增雙分頁雙身分情境（用不同 `X-Auth-Request-Email`＋信任來源 127.0.0.1）。
- [ ] **Step 6: Commit** `feat: StratyUI 每個 actor 獨立 session，共用帳本與全域回測上限`

### Task 9: 自動化帳號限制與「未審」策略規則（規格 §10）

**Files:**
- Modify: `strategies/_common/graph/gate.py`、`strategies/_common/graph/service.py`、`quant_crawler/strategy_gen/*`（標 `review_status`，先讀現有標記再沿用）
- Test: `tests/test_unreviewed_and_service_actor.py`

- [ ] **Step 1: 失敗測試**：`service:cron` 呼叫 `record_success`（selection）→ 拒絕；`purpose=screening` 且 `delta_n=0` 允許；`review_status: unreviewed` 的策略 jason 載入／執行 → 403；joker 可；signer 以 `POST /api/strategies/<id>/review` 標 reviewed 後 jason 可執行，且帳本多一筆 `kind:"strategy_review"`（含 actor）。
- [ ] **Step 2–4:** 失敗→實作→通過。
- [ ] **Step 5: Commit** `feat: 自動化帳號禁止 selection；未審策略僅簽核人可執行`

### Task 10: 唯讀 API 與 IdeaFlow 介面骨架（規格 §4.6、§9）

**Files:**
- Modify: `strategies/_common/graph/api.py`
- Test: `tests/test_readonly_api.py`

- [ ] **Step 1: 失敗測試**：匿名／唯讀者 `GET /api/ledger/summary`、`/api/ledger/tail?n=5`（回應不含 `graph_ref.snapshot`）、`/api/ledger/verify`、`/api/preregistrations`、`/api/load` 皆 200；`POST /api/import/ideaflow-draft` 非 executor → 403、executor → 501 且 body 含 `{"schema":"ideaflow-bridge/1","implemented":false}`（骨架）；`GET /api/export/ideaflow-reconcile` 回空陣列與 schema 版本。
- [ ] **Step 2–4:** 失敗→實作→通過。
- [ ] **Step 5: Commit** `feat: 唯讀帳本／登記 API 與 IdeaFlow 橋接路由骨架（schema ideaflow-bridge/1）`

### Task 11: 日誌遮蔽與 TEJ 金鑰不外洩（規格 §6）

**Files:**
- Create: `quant_crawler/redact.py`、`tests/test_redact.py`
- Modify: `strategies/_common/graph/__main__.py`、`quant_crawler/cli.py`（啟動時安裝過濾器）

- [ ] **Step 1: 失敗測試**：以**執行期組成**的假金鑰（如 `"TEJ" + "FAKE" * 6`）設為環境變數；透過 `logging` 與 `print`（包 `sys.stdout`）輸出含該值的字串，斷言輸出被遮蔽為 `***`；例外 traceback 含該值亦被遮蔽；不含金鑰的訊息逐字不變。
- [ ] **Step 2–4:** 失敗→實作（`logging.Filter`＋包裝 `sys.stdout/stderr`，遮蔽 `TEJAPI_KEY`、`*_KEY`、`*_TOKEN` 的環境變數值，長度 ≥ 8 才遮）→通過。
- [ ] **Step 5: Commit** `feat: 日誌遮蔽環境中的金鑰值，並以假金鑰測試斷言不外洩`

### Task 12: 伺服器端 pre-receive 邏輯（規格 §5.2、§5.3）

**Files:**
- Create: `scripts/pre_receive.py`、`tests/test_pre_receive.py`、`deploy/gsinvest/hooks/pre-receive`
- Modify: `scripts/check_append_only.py`（暴露 `check_range(repo, old, new) -> list[str]`，保留 CLI）

**Interfaces:**
- Produces: `check_push(repo: Path, updates: list[tuple[old, new, ref]], *, pusher_email: str, roles: Roles) -> list[str]`（問題清單，空＝放行）。規則依規格 §5.2 第 1–5 條與 §5.3；`pusher_email` 由 hook 薄包裝從 `GITEA_PUSHER_EMAIL` 取得。

- [ ] **Step 1: 失敗測試**（用 `tmp_path` 內真實 `git init` 建 repo、產生 commits；每個案例 `assert problems` 非空且訊息為中文）：
  1. 改既有登記 → 擋；2. 刪／改名登記 → 擋；3. 新增 `planned_trials=11` 的 `single_preregistered` 登記 → 擋；4. 登記 `author` ≠ 推送者 → 擋；5. author 已有 3 份進行中（含未簽核）再推第 4 份 → 擋；6. 自己簽自己的登記 → 擋；7. 非 signer 推 signoff → 擋；8. signoff 的 `prereg_sha256` 不符 → 擋；9. 第二份 signoff → 擋；10. force push（`old` 不是 `new` 的祖先）到 `refs/heads/main` → 擋；11. 刪除 `refs/heads/main` → 擋；12. 合法流程（jason 登記→kevin signoff→jason 推 closure）→ 全部放行；13. 修改 `TEMPLATE.yaml`／`README.md` → 放行。
- [ ] **Step 2: 確認失敗。**
- [ ] **Step 3: 實作** `check_push`：以 `git diff --name-status -M old new` 取變更、`git show new:<path>` 讀內容；`yaml.safe_load`；重用 `check_preregistration.validate`（它吃 `Path`，先把內容寫入暫存檔）與 Task 5 `check_signoff.validate`。hook 薄包裝（bash）逐行讀 stdin `old new ref`，呼叫 `python -I /opt/gs-hooks/scripts/pre_receive.py`，任一問題以 stderr 印出並 `exit 1`；**不得印環境變數**。
- [ ] **Step 4: 通過** `pytest tests/test_pre_receive.py tests/test_append_only_and_prereg.py -q` → PASS。
- [ ] **Step 5: Commit** `feat: 伺服器端 pre-receive（登記不可改、作者／簽核人檢查、配額、禁止 force push）`

### Task 13: 封存（seal）與備份腳本（規格 §7）

**Files:**
- Create: `scripts/seal_ledger.py`、`tests/test_seal_ledger.py`、`deploy/gsinvest/backup/{sync_to_jsonhuang.sh,daily_snapshot.sh,weekly_offline.sh,seal_and_push.sh}`

**Interfaces:**
- Produces: `seal(ledger: Path, out_dir: Path, date: str) -> Path` 寫出 `seals/<date>.json`：`{"date","lines","seq","head_hash","file_sha256","legacy_lines","chain_ok"}`；鏈驗證失敗時**不產生 seal**並以非零退出。

- [ ] **Step 1: 失敗測試**：正常帳本 → seal 檔內容與手算相符；鏈被竄改 → 非零且無檔；同日重跑覆寫同值（冪等）。
- [ ] **Step 2–4:** 失敗→實作→通過。
- [ ] **Step 5: 撰寫 shell 腳本**（`set -euo pipefail`；`sync_to_jsonhuang.sh`：`rsync -a --append-verify -e "ssh -i /run/secrets/backup_key -o StrictHostKeyChecking=yes"`；比對本地與遠端 `seq`／`head_hash`，不一致推播並建立 `/data/READONLY` 旗標，app 見旗標即拒絕 `/api/run`；`daily_snapshot.sh`：`sqlite3 backtests.sqlite ".backup '<dst>'"` 後同步；`seal_and_push.sh`：產生 seal→commit 到孤立分支 `ledger-seals`→`git tag -s`（SSH 簽章）`seal/<date>`→push 該分支與 tag；`weekly_offline.sh`：`tar | age -r <pubkey> -o …` 再 `rsync` 到 F 槽備份機）。腳本以 `shellcheck` 通過；備份清單明確排除 `.env`、`secrets/`、`.zipline`（測試：`tests/test_backup_excludes.py` 解析 rsync filter 檔斷言這三項在排除清單）。
- [ ] **Step 6: Commit** `feat: 帳本 seal 與備份腳本（15 分鐘同步、每日快照、每日簽章 tag、每週離線）`

### Task 14: 容器映像與 compose（規格 §3、§8.1）

**Files:**
- Create: `deploy/gsinvest/{Dockerfile,compose.yaml,env.example,README.md}`、`tests/test_deploy_files.py`

- [ ] **Step 1: 失敗測試**（靜態檢查，不需 docker）：解析 `compose.yaml` 斷言：app／cron 掛 `/app` 為 `:ro`、`/zipline` 為 `:ro`、`gs-data` 僅 app／cron 為 rw、backup 為 `:ro`；secrets 為 file secret 而非 `environment`；三個執行緒變數皆為 `1`；`cpus: 16`、`mem_limit: 64g`、`cpu_shares: 128`；app 僅發布 `127.0.0.1:9112`；Dockerfile 以 git SHA 當 `ARG`、`pip install -r requirements.txt`（固定版本，99 個套件清單沿用 bench）；映像內無 `.env`。
- [ ] **Step 2–4:** 失敗→撰寫檔案→通過。env 預設：`GS_DATA_ROOT=/data`、`GS_READONLY_CODE=1`、`GS_MAX_CONCURRENT_BACKTESTS=8`、`OMP/OPENBLAS/MKL_NUM_THREADS=1`、`GS_ROLES_FILE=/data/config/roles.yaml`。
- [ ] **Step 5: Commit** `feat: gsinvest 部署用 Dockerfile 與 compose（唯讀程式碼、資源硬上限、file secret）`

### Task 15: Part A 收尾與規格同步

- [ ] **Step 1:** `.venv/bin/python -m pytest -q`（預設環境）與 `GS_DATA_ROOT=<tmp> GS_READONLY_CODE=1 pytest -q` 各一次，全綠。
- [ ] **Step 2:** 更新 `docs/spec/statistical-decision-tree.md` §7 與 `auto-research-funnel.md` §4，加一行指向本規格（伺服器端強制、帳本雜湊鏈）；`docs/spec/live-strategy-graph.md` 補「多 session」。
- [ ] **Step 3:** 對全部新增 Python 跑 `ruff`（若 repo 已有設定）；`shellcheck deploy/gsinvest/**/*.sh`。
- [ ] **Step 4: Commit、推送分支（`git push -u origin <branch>`，不推 main）、開 PR。**

---

# Part B：gsinvest 運維步驟（Runbook；**使用者審閱後才執行**）

每一步都先寫在 `deploy/gsinvest/runbooks/<n>-<name>.md`（含指令、預期輸出、回退），由人或受授權的 agent 照單執行。**本計畫階段只寫 runbook、不執行。**

### Task 16: 階段 A 準備（不影響現況）

- [ ] 在 gsinvest 建 `~/gs-strat/{zipline,secrets,compose}`；從 Kevin PC 複製 `.zipline`（82MB，`tquant_future`）；**TEJ 金鑰另行取得使用者同意後**以 `install -m 600` 放入 `secrets/tej_env`，驗證 `docker inspect`／`docker logs` 皆無明文（對輸出以雜湊前綴比對）。
- [ ] `docker build` 映像（tag＝git SHA）；`docker volume create gs-data`；以一次性容器寫入 `config/roles.yaml`（email 由使用者提供，Q6）。
- [ ] 起 Forgejo 容器（資料另一個 volume）；建組織 `gs`、repo `gs-strategy`、三位使用者與其 SSH 公鑰；以 `docker exec` 安裝 `/opt/gs-hooks/`（固定版本的 `pre_receive.py`＋依賴）並啟用 repo 的 pre-receive；分支保護：main 禁止 force／刪除。
- [ ] 以 Task 12 的 13 個案例**對真實 Forgejo 實例**手動 `git push` 驗證，貼錯誤訊息到 runbook。
- [ ] 建 jsonhuang 的備份帳號與目錄（forced-command 的 write-only key；需要 jsonhuang 上的操作權限，向使用者確認）；啟動 `gs-backup` 容器；驗證 15 分鐘同步與分歧告警（故意改影子帳本一行，應告警並進入唯讀）。
- [ ] GitHub：`ledger-seals` 孤立分支與 deploy key（寫入權限限此 repo）；首次 `seal_and_push.sh` 成功並可在 GitHub 看到 tag。
- [ ] Cloudflare：**從 dgx-a4d7** 建 tunnel 與 Access application（名單三個 email＋OTP）；gsinvest 只放 connector token；app 設 `--cf-team-domain`、`--cf-aud`、`--public-host`（內網與外網兩個）。
- [ ] gs-linux 轉埠 unit（沿用 gsinvest-ytkb-dash 的做法）預備好但**不切換**；使用者處理 `strategy.gsinvest.com` 的 UniFi DNS（切換日才指向）。
- [ ] 資源驗證：`docker stats` 確認 16 核／64GB 上限；壓測 8／16／32 並行回測期間 vLLM `/health` 仍 ≤ 數毫秒；補量「16 個不同設定同時寫帳本」與「StratyUI 多人同時操作」並更新決策文件。
- [ ] Commit runbook：`docs: gsinvest 階段 A 運維手冊`

### Task 17: 階段 B 影子期（1–2 週）

**Files:** `deploy/gsinvest/runbooks/20-shadow.md`、`scripts/shadow_compare.py`、`tests/test_shadow_compare.py`

- [ ] **Step 1:** 寫 `scripts/shadow_compare.py`（輸入：兩邊 `backtests.sqlite`／result pickle 路徑與同一份圖；輸出：`backtest_key` 是否相同、夏普差、年化報酬差、行數差）；TDD：相同輸入 → 通過；夏普差 1e-3 → 非零。容差預設夏普絕對差 ≤ 1e-6（Q7）。
- [ ] **Step 2:** gsinvest 以 `GS_LEDGER_MODE=shadow` 啟動（寫 `log/trials.shadow.jsonl`；頁首橫幅「影子環境」；shadow 模式不得推 seal、不同步為正式帳本）。實作旗標在 `paths.ledger_path()` 與 UI 橫幅（含測試）。
- [ ] **Step 3:** 每日自動對照：同一組輸入（四支策略各一）在 Kevin PC 與 gsinvest 跑，`shadow_compare` 結果寫入 `docs/shadow-log.md`。
- [ ] **Step 4:** 每日記錄 `journalctl -k` 的 Xid／AER 計數與 PCIe 寬度（負載下）；任一次異常 → 計數歸零。
- [ ] **Step 5:** 影子期驗證清單（規格 §12 的 2、3、4、5、6、7、8、9）逐項勾選並貼證據；含還原演練（另起空 volume，從 jsonhuang 還原後 `verify_ledger_chain.py` 全綠）與 TTFT 告警演練。
- [ ] **Step 6:** G1 煙霧與 cron 容器實跑一次爬蟲＋codegen，確認 `ΔN=0`、輸出落 `/data`、TEJ 併發無被限流；取回 24h 負載取樣補進決策文件（PID 與收取方式見記憶檔 `gs-strategy-host-migration-eval`，只停那三個 PID）。
- [ ] **Step 7:** 通過條件：連續 ≥14 天無 Xid／AER、對照全過、驗收 1–9 全過 → 向使用者提交「切換決策」報告（不自行切換）。

### Task 18: 階段 C 切換日與階段 D 回退期

**Files:** `deploy/gsinvest/runbooks/30-cutover.md`、`31-rollback.md`、`scripts/import_ledger.py`、`tests/test_import_ledger.py`

- [ ] **Step 1: 匯入腳本 TDD**（`import_ledger.py <src> <dst>`）：複製後 `sha256` 與來源相同、行數相同、自動插 `chain_anchor`、`verify_chain` 通過、`count_existing_selection_trials` 與來源一致；目的檔已存在且非空時拒絕（防誤覆蓋）。
- [ ] **Step 2: 切換日 runbook**（規格 §11 階段 C 六步，逐步附指令、預期、中止條件；T-24h 公告文案草稿）：公告 → 凍結 Kevin PC（唯讀旗標、停 timer、pre-push 擋）→ 匯入 → 比對 N／行數／sha256 → 轉埠與 DNS（使用者處理）→ 三人煙霧（各一次 selection、N+1、備份看到新 `seq`）。
- [ ] **Step 3: 回退 runbook**（規格 §11 階段 D）：觸發條件、`seq > anchor` 的帳本行回灌 Kevin PC（`scripts/export_ledger_tail.py <seq>`，TDD：回灌後 Kevin PC 帳本鏈完整）、解除凍結、轉埠指回；Kevin PC 回退實例必設 `OMP/OPENBLAS/MKL_NUM_THREADS=1`。**回退演練在影子期做過一次才算準備完成。**
- [ ] **Step 4: 回退期 2 週**：每日 `verify_ledger_chain.py`、備份同步比對；結束後使用者決定 Kevin PC 退役（保留冷備份）。
- [ ] **Step 5: 收尾**：用量顧問 backlog 若有對應項目，依 CLAUDE.md 規則 4 補跑 `Complete-Task.ps1`；更新記憶檔 `gs-strategy-host-migration-eval`；通知 genesis-deck 主線 session：IdeaFlow 匯入／對帳改用 §9 的 HTTP 端點（另案）。

---

## Self-Review（對照規格）

| 規格章節 | 對應任務 |
|---|---|
| §3 路徑／manifest（F1、F2） | 1、2、14 |
| §4.1 名單與身分（F3） | 4 |
| §4.2 每人 session、semaphore（F4） | 8 |
| §4.3 雜湊鏈（F5） | 3、13 |
| §4.4 閘門與配額 | 5、6 |
| §4.5 N 提示線、超線核准 | 7 |
| §4.6、§9 唯讀與 IdeaFlow 介面 | 10 |
| §5 Forgejo＋pre-receive、簽核 | 12、16 |
| §6 TEJ 金鑰 | 11、14、16 |
| §7 備份、seal、離線 | 13、16 |
| §8 資源、對外存取 | 14、16 |
| §10 爬蟲、自動寫策略、未審 | 9、16、17 |
| §11 三階段切換與回退 | 17、18 |
| §12 驗收 | 15、17 |

已知限制：Task 7 前端與 Task 9 的 `quant_crawler/strategy_gen` 標記位置，需實作者先讀現有程式再落地（計畫已標註「先讀」），因為本次未逐檔檢視前端與 codegen 輸出格式。Part B 的具體 email、DNS、CF 設定值取決於規格 §13 的 Q1–Q7 拍板。
