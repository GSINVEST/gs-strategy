"""策略提交佇列：待審 -> 核准 -> 退出。設計見 docs/spec/strategy-submission-queue.md。

三條紀律（對應 genesis-deck Queue.tla 的六個不變量）：

* **提交不跑回測。** ``submit`` 只驗證、複製 bundle、寫事件，不 import runner／zipline。
* **核准 = ledger 上的一次 append。** 那一行同時是 trial（delta_n=1）、核准、簽核與入池；
  記帳與簽核不可能只做一半。回測在鎖外、核准之後才跑，且 ``run_started`` 先於執行
  落檔，所以 crash 或失敗都不會讓同一個 submission 被再跑一次。
* **狀態不另存。** 由 ``log/submissions.jsonl`` 與 ``log/trials.jsonl`` 兩份 append-only
  檔摺出來，沒有第三份可變狀態可以與它們不一致。

私有方法 ``_check_prereg``、``_static_check``、``_build_trial``、``_append_trial``、
``_execute`` 是刻意留的縫，回放測試用子類覆寫它們來做負控制。
"""
from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from collections import Counter
from collections.abc import Callable, Collection
from datetime import datetime, timezone
from pathlib import Path

import yaml

from strategies._common.validation.decision import (
    DecisionError,
    Facts,
    latest_by_trial_id,
    resolve,
)

from .models import (BacktestRun, Outcome, QueueSnapshot, Result, Status,
                     Submission)
from .store import _exclusive, append_jsonl, read_jsonl

BUNDLE_FILES = ("strategy.py", "config.yaml", "manifest.yaml")
MAX_FILE_BYTES = 256 * 1024
PREREG_ID_RE = re.compile(r"^[0-9]{3}-[a-z0-9][a-z0-9-]*$")
#: 靜態檢查的佔位黑名單（genesis-deck §6 的完整規則尚未定義）。這不是沙箱。
BANNED_MODULES = frozenset({
    "socket", "subprocess", "requests", "urllib", "urllib3", "http", "ftplib",
    "smtplib", "ctypes", "multiprocessing", "importlib", "asyncio", "shutil",
})
BANNED_CALLS = frozenset({"__import__", "eval", "exec", "compile", "open"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class SubmissionQueue:
    def __init__(self, root: str | Path, *, approvers: Collection[str],
                 backtest: Callable[[Submission], BacktestRun],
                 clock: Callable[[], str] = _utc_now,
                 require_committed_prereg: bool = True):
        self.root = Path(root)
        self.approvers = frozenset(approvers)
        self.backtest = backtest
        self.clock = clock
        self.require_committed_prereg = require_committed_prereg
        self.events_path = self.root / "log" / "submissions.jsonl"
        self.trials_path = self.root / "log" / "trials.jsonl"
        self.bundles_dir = self.root / "strategies" / "_submitted"
        self.prereg_dir = self.root / "experiments" / "preregistration"

    # ------------------------------------------------------------------ 摺出狀態
    def _fold(self) -> dict:
        subs: dict[str, dict] = {}
        retired: set[str] = set()
        runs: Counter = Counter()
        for ev in read_jsonl(self.events_path):
            sid, kind = ev.get("sid"), ev.get("event")
            if not sid:
                continue
            if kind == "submitted":
                subs.setdefault(sid, ev)
            elif kind == "retired":
                retired.add(sid)
            elif kind == "run_started":
                runs[sid] += 1
        trials = {}
        for rec in latest_by_trial_id(read_jsonl(self.trials_path)):
            tid = rec.get("trial_id", "")
            if tid.startswith("t-sub-"):
                trials[tid[len("t-sub-"):]] = rec
        return {"subs": subs, "retired": retired, "runs": runs, "trials": trials}

    @staticmethod
    def _status(state: dict, sid: str) -> Status | None:
        if sid not in state["subs"]:
            return None
        if sid in state["retired"]:
            return Status.RETIRED
        if sid in state["trials"] and state["trials"][sid].get("approval"):
            return Status.APPROVED
        return Status.PENDING

    def status(self, sid: str) -> Status | None:
        return self._status(self._fold(), sid)

    def snapshot(self) -> QueueSnapshot:
        st = self._fold()
        status = {sid: self._status(st, sid).value for sid in st["subs"]}
        trials, approved_by, signed = {}, {}, set()
        for sid, rec in st["trials"].items():
            trials[sid] = int((rec.get("stat_decision") or {}).get("delta_n", 0))
            ap = rec.get("approval") or {}
            if ap.get("approved_by"):
                approved_by[sid] = ap["approved_by"]
            if ap.get("signed"):
                signed.add(sid)
        pool = {sid for sid, s in status.items() if s == Status.APPROVED.value and sid in signed}
        return QueueSnapshot(
            status=status, runs=dict(st["runs"]), trials=trials,
            approved_by=approved_by, signed=frozenset(signed),
            pool=frozenset(pool), prereg_ids=frozenset(self._committed_prereg_ids()),
        )

    # ------------------------------------------------------------------ 縫
    def _committed_prereg_ids(self) -> set[str]:
        if not self.prereg_dir.is_dir():
            return set()
        ids = {p.stem for p in self.prereg_dir.glob("*.yaml") if PREREG_ID_RE.match(p.stem)}
        if not self.require_committed_prereg:
            return ids
        return {i for i in ids if self._head_blob(f"experiments/preregistration/{i}.yaml") is not None}

    def _head_blob(self, rel: str) -> bytes | None:
        r = subprocess.run(["git", "-C", str(self.root), "show", f"HEAD:{rel}"],
                           capture_output=True, check=False)
        return r.stdout if r.returncode == 0 else None

    def _check_prereg(self, prereg_id: str) -> tuple[dict | None, str | None]:
        """回傳 (info, 拒絕理由)。要求：已 commit 且與工作目錄一致、validate 通過、purpose=selection。"""
        if not PREREG_ID_RE.match(prereg_id or ""):
            return None, f"prereg_id 格式不合（應為 NNN-slug）：{prereg_id!r}"
        path = self.prereg_dir / f"{prereg_id}.yaml"
        if not path.is_file():
            return None, f"找不到登記檔 {prereg_id}"
        raw = path.read_bytes()
        if self.require_committed_prereg:
            head = self._head_blob(f"experiments/preregistration/{prereg_id}.yaml")
            if head is None:
                return None, f"登記檔 {prereg_id} 尚未 commit（事前登記必須先凍結）"
            if head != raw:
                return None, f"登記檔 {prereg_id} 與 git HEAD 不一致"
        from scripts.check_preregistration import validate
        problems = validate(path)
        if problems:
            return None, f"登記檔 {prereg_id} 未通過驗證：{problems[0]}"
        doc = yaml.safe_load(raw.decode("utf-8")) or {}
        if (doc.get("stat_plan") or {}).get("purpose") != "selection":
            return None, f"登記檔 {prereg_id} 的 stat_plan.purpose 不是 selection"
        return {"doc": doc, "sha256": hashlib.sha256(raw).hexdigest()}, None

    def _static_check(self, bundle_dir: Path) -> str | None:
        """佔位版 AST 黑名單；不是沙箱，對外開放前必須換成真的隔離。"""
        try:
            tree = ast.parse((bundle_dir / "strategy.py").read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as e:
            return f"strategy.py 無法解析：{e}"
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                if m.split(".")[0] in BANNED_MODULES:
                    return f"禁止 import {m}"
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in BANNED_CALLS):
                return f"禁止呼叫 {node.func.id}()"
        return None

    def _build_trial(self, sub: Submission, who: str, info: dict, bundle_sha: str,
                     prescription, facts: Facts) -> dict:
        now = self.clock()
        doc = info["doc"]
        stat = {**prescription.to_record(), "purpose": "selection",
                "decision_path": facts.as_path(),
                "preregistration_id": sub.prereg_id}
        return {
            "schema": "research-trial/v1", "trial_id": sub.trial_id,
            "parent_trial_id": None, "repo": "gs-strategy", "level": "trial",
            "state": "approved", "started_at": now, "finished_at": None,
            "purpose": "selection",
            "hypothesis": str(doc.get("hypothesis", ""))[:200],
            "acceptance_criterion": str((doc.get("thresholds") or {}).get("pass_condition", "")),
            "config_ref": f"strategies/_submitted/{sub.sid}/manifest.yaml",
            "config_sha256": bundle_sha,
            "preregistration_id": sub.prereg_id,
            "stat_decision": stat,
            # 簽核放這裡而不是登記檔：登記檔 commit 後凍結，寫不進去。
            "approval": {"approved_by": who, "signed_by": who, "signed": True,
                         "signed_at": now, "prereg_id": sub.prereg_id,
                         "prereg_sha256": info["sha256"], "bundle_sha256": bundle_sha},
        }

    def _append_trial(self, record: dict) -> None:
        append_jsonl(self.trials_path, record)

    # ------------------------------------------------------------------ bundle
    def _read_bundle(self, bundle_dir: Path) -> tuple[dict[str, bytes] | None, str | None]:
        files: dict[str, bytes] = {}
        for name in BUNDLE_FILES:
            p = bundle_dir / name
            if p.is_symlink() or not p.is_file():
                return None, f"bundle 缺 {name}（或為符號連結）"
            if p.stat().st_size > MAX_FILE_BYTES:
                return None, f"{name} 超過 {MAX_FILE_BYTES} bytes"
            files[name] = p.read_bytes()
        return files, None

    @staticmethod
    def _sid(files: dict[str, bytes], prereg_id: str) -> tuple[str, str]:
        h = hashlib.sha256()
        for name in BUNDLE_FILES:
            h.update(name.encode() + b"\0" + files[name] + b"\0")
        bundle_sha = h.hexdigest()
        sid = "s-" + hashlib.sha256((bundle_sha + prereg_id).encode()).hexdigest()[:12]
        return sid, bundle_sha

    def _bundle_sha_on_disk(self, sid: str) -> str | None:
        files, err = self._read_bundle(self.bundles_dir / sid)
        return None if err else self._sid(files, "")[1]

    # ------------------------------------------------------------------ 動作
    def submit(self, bundle_dir: str | Path, prereg_id: str, submitter: str) -> Result:
        """驗證、複製 bundle、寫事件。不跑回測。"""
        files, err = self._read_bundle(Path(bundle_dir))
        if err:
            return Result(Outcome.REJECTED, "", err)
        sid, bundle_sha = self._sid(files, prereg_id)
        with _exclusive(self.events_path):
            if sid in self._fold()["subs"]:
                return Result(Outcome.NOOP, sid)
            info, err = self._check_prereg(prereg_id)
            if err:
                return Result(Outcome.REJECTED, sid, err)
            manifest = yaml.safe_load(files["manifest.yaml"].decode("utf-8")) or {}
            if not (manifest.get("prereg_id") == prereg_id
                    or f"prereg:{prereg_id}" in (manifest.get("tags") or [])):
                return Result(Outcome.REJECTED, sid,
                              f"manifest 必須記錄 prereg:{prereg_id}（tags 或 prereg_id）")
            err = self._static_check(Path(bundle_dir))
            if err:
                return Result(Outcome.REJECTED, sid, err)
            dest = self.bundles_dir / sid
            if dest.exists():
                if self._bundle_sha_on_disk(sid) != self._sid(files, "")[1]:
                    return Result(Outcome.REJECTED, sid, "既有 bundle 內容與提交不符")
            else:
                dest.mkdir(parents=True)
                for name, data in files.items():
                    (dest / name).write_bytes(data)
            append_jsonl(self.events_path, {
                "event": "submitted", "sid": sid, "prereg_id": prereg_id,
                "submitter": submitter, "bundle_sha256": bundle_sha, "at": self.clock()})
            return Result(Outcome.QUEUED, sid)

    def approve(self, sid: str, who: str, *, run: bool = True) -> Result:
        with _exclusive(self.events_path):
            state = self._fold()
            if who not in self.approvers:
                return Result(Outcome.REJECTED, sid, f"{who!r} 不在核准者名單")
            status = self._status(state, sid)
            if status is Status.APPROVED:
                return Result(Outcome.NOOP, sid)
            if status is not Status.PENDING:
                return Result(Outcome.REJECTED, sid, f"狀態 {status.value if status else '不存在'} 不可核准")
            ev = state["subs"][sid]
            prereg_id = ev["prereg_id"]
            info, err = self._check_prereg(prereg_id)
            if err:
                return Result(Outcome.REJECTED, sid, err)
            bundle_sha = ev["bundle_sha256"]
            if self._bundle_sha_on_disk(sid) != bundle_sha:
                return Result(Outcome.REJECTED, sid, "bundle 遺失或與提交時不符")
            from scripts.triage_generated import count_existing_selection_trials
            n_after = count_existing_selection_trials(self.trials_path) + 1
            plan = dict(info["doc"].get("stat_plan") or {})
            known = set(Facts.__dataclass_fields__)
            kw = {k: v for k, v in plan.items() if k in known and v is not None}
            kw["n_trials"] = n_after
            kw.setdefault("n_eff", 1)       # 登記時未知，與 check_preregistration 同做法
            kw.setdefault("autocorr", "unknown")
            try:
                facts = Facts(**kw)
                prescription = resolve(facts)
            except (DecisionError, TypeError) as e:
                return Result(Outcome.REJECTED, sid, f"stat_plan 推導不出處方：{e}")
            if prescription.delta_n != 1:
                return Result(Outcome.REJECTED, sid, "處方的 delta_n 不是 1，不是 selection")
            sub = Submission(sid, f"t-sub-{sid}", self.bundles_dir / sid, prereg_id, n_after)
            record = self._build_trial(sub, who, info, bundle_sha, prescription, facts)
            self._append_trial(record)          # <- 提交點：唯一一次寫入
        if run:
            self._execute(sid)
        return Result(Outcome.APPROVED, sid)

    def _execute(self, sid: str) -> None:
        """先落 run_started（NoDoubleRun 的依據），再在鎖外跑回測，最後追加完成行。"""
        with _exclusive(self.events_path):
            state = self._fold()
            if self._status(state, sid) is not Status.APPROVED or state["runs"][sid] > 0:
                return
            trial = state["trials"][sid]
            n = int((trial.get("stat_decision") or {}).get("decision_path", {}).get("n_trials", 1))
            sub = Submission(sid, trial["trial_id"], self.bundles_dir / sid,
                             trial["preregistration_id"], n)
            append_jsonl(self.events_path, {"event": "run_started", "sid": sid, "at": self.clock()})
        result, error = None, None
        try:
            result = self.backtest(sub)
        except Exception as e:                  # noqa: BLE001  失敗也要留下記錄
            error = repr(e)
        with _exclusive(self.events_path):
            stat = dict(trial["stat_decision"])      # delta_n 必須維持 1
            if result is not None and result.p_value is not None:
                stat["p_value"] = float(result.p_value)
                stat["p_value_source"] = result.p_value_source or ""
            self._append_trial({
                **trial, "state": "failed" if error else "complete",
                "finished_at": self.clock(), "stat_decision": stat,
                "outcome_manual": error or (result.summary if result else None),
            })

    def retire(self, sid: str, who: str) -> Result:
        with _exclusive(self.events_path):
            if who not in self.approvers:
                return Result(Outcome.REJECTED, sid, f"{who!r} 不在核准者名單")
            if self._status(self._fold(), sid) is not Status.APPROVED:
                return Result(Outcome.REJECTED, sid, "只有已核准的可以退出")
            append_jsonl(self.events_path, {"event": "retired", "sid": sid, "by": who, "at": self.clock()})
            return Result(Outcome.RETIRED, sid)
