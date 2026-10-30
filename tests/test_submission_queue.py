"""提交佇列：Queue.tla 六個不變量、冪等、並發、crash、守門與 CLI。

回測一律注入計數樁——不跑 zipline（CI 沒有 TEJAPI_KEY）。``runs`` 取自樁的觀測值，
不從 ledger 讀，否則 TrialLedger（trials = runs）會變成同義反覆。
"""
from __future__ import annotations

import json
import multiprocessing
import random
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import check_append_only as cao                        # noqa: E402
from strategies._common.submission import (BacktestRun, Outcome, Status,  # noqa: E402
                                           SubmissionQueue)
from strategies._common.submission import cli                       # noqa: E402
from strategies._common.submission.guard import require_approved_for_submitted  # noqa: E402
from strategies._common.validation.decision import audit_ledger     # noqa: E402
from tests.test_append_only_and_prereg import _prereg               # noqa: E402

APPROVERS = ("kevin", "admin")
STRATEGY = "def initialize(context):\n    pass\n\n\ndef handle_data(context, data):\n    pass\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def root(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    pd = tmp_path / "experiments" / "preregistration"
    pd.mkdir(parents=True)
    (pd / "001-x.yaml").write_text(yaml.safe_dump(_prereg(), allow_unicode=True), encoding="utf-8")
    (tmp_path / "log").mkdir()
    (tmp_path / "log" / "trials.jsonl").write_text(
        json.dumps({"trial_id": "t-seed", "stat_decision": {"delta_n": 0, "no_statistical_claim": True,
                                                             "basis": "seed"}}) + "\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def make_bundle(base: Path, name: str, *, variant: str = "a", prereg: str = "001-x",
                strategy: str | None = None) -> Path:
    d = base / "incoming" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "strategy.py").write_text(strategy or STRATEGY + f"# {variant}\n", encoding="utf-8")
    (d / "config.yaml").write_text("start: '2020-01-01'\nend: '2021-01-01'\n", encoding="utf-8")
    (d / "manifest.yaml").write_text(yaml.safe_dump({"name": name, "prereg_id": prereg}), encoding="utf-8")
    return d


class Counter:
    """回測樁：runs 是這裡的觀測值。"""
    def __init__(self, p=0.01, boom=False):
        self.calls, self.p, self.boom = [], p, boom

    def __call__(self, sub):
        self.calls.append(sub.sid)
        if self.boom:
            raise RuntimeError("backtest exploded")
        return BacktestRun(p_value=self.p, p_value_source="PSR 單尾 p（測試）")


def new_queue(root, stub=None, **kw):
    stub = stub or Counter()
    return SubmissionQueue(root, approvers=APPROVERS, backtest=stub, **kw), stub


def ledger(root):
    return [json.loads(x) for x in (root / "log" / "trials.jsonl").read_text(encoding="utf-8").splitlines() if x]


def events(root):
    p = root / "log" / "submissions.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x] if p.is_file() else []


def check_invariants(q, stub, floor):
    """與 Queue.tla 的六個不變量一一對應。floor 是上一步的 trials。"""
    snap = q.snapshot()
    runs = {s: stub.calls.count(s) for s in set(stub.calls)}
    for s in set(snap.status) | set(snap.trials) | set(floor) | set(runs):
        t = snap.trials.get(s, 0)
        assert t >= floor.get(s, 0), f"TrialMonotone：{s}"
        assert t == runs.get(s, 0), f"TrialLedger：{s} trials={t} runs={runs.get(s, 0)}"
        assert runs.get(s, 0) <= 1, f"NoDoubleRun：{s}"
        if runs.get(s, 0) > 0:
            assert snap.approved_by.get(s), f"RunNeedsApproval：{s}"
        if s in snap.status:
            assert snap.prereg_ids, f"PreregBeforeSubmit：{s}"
    assert snap.pool <= snap.signed, "SignedBeforePool"
    assert snap.runs == runs or all(snap.runs.get(s, 0) == runs.get(s, 0) for s in runs)
    return dict(snap.trials)


# ───────────────────────── 提交 ─────────────────────────

def test_submit_queues_without_running_or_counting(root):
    q, stub = new_queue(root)
    res = q.submit(make_bundle(root, "a"), "001-x", "alice")
    assert res.outcome is Outcome.QUEUED and q.status(res.submission_id) is Status.PENDING
    assert stub.calls == [] and q.snapshot().trials == {}
    assert len(ledger(root)) == 1                                  # ledger 沒多一行
    assert (root / "strategies" / "_submitted" / res.submission_id / "strategy.py").is_file()


def test_submit_module_does_not_import_runner_or_zipline():
    code = ("import sys; import strategies._common.submission.service; "
            "bad=[m for m in ('zipline','strategies._common.runner') if m in sys.modules]; "
            "sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-c", code], cwd=REPO).returncode == 0


def test_resubmit_same_content_is_noop_and_changed_content_is_new(root):
    q, _ = new_queue(root)
    a = q.submit(make_bundle(root, "a"), "001-x", "alice")
    assert q.submit(make_bundle(root, "a"), "001-x", "alice").outcome is Outcome.NOOP
    b = q.submit(make_bundle(root, "b", variant="b"), "001-x", "alice")
    assert b.outcome is Outcome.QUEUED and b.submission_id != a.submission_id
    assert len(events(root)) == 2


@pytest.mark.parametrize("prereg,why", [
    ("009-nope", "找不到登記檔"), ("../etc", "格式不合"), ("", "格式不合")])
def test_submit_without_a_valid_prereg_is_rejected(root, prereg, why):
    q, _ = new_queue(root)
    res = q.submit(make_bundle(root, "a", prereg=prereg or "001-x"), prereg, "alice")
    assert res.outcome is Outcome.REJECTED and why in res.reason
    assert events(root) == [] and not (root / "strategies" / "_submitted").exists()


def test_uncommitted_or_modified_prereg_is_rejected(root):
    q, _ = new_queue(root)
    (root / "experiments" / "preregistration" / "002-new.yaml").write_text(
        yaml.safe_dump(_prereg(id="002-new")), encoding="utf-8")
    res = q.submit(make_bundle(root, "a", prereg="002-new"), "002-new", "alice")
    assert res.outcome is Outcome.REJECTED and "尚未 commit" in res.reason
    p = root / "experiments" / "preregistration" / "001-x.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "# 改過\n", encoding="utf-8")
    res = q.submit(make_bundle(root, "a"), "001-x", "alice")
    assert res.outcome is Outcome.REJECTED and "與 git HEAD 不一致" in res.reason


def test_manifest_must_record_the_prereg_tag(root):
    q, _ = new_queue(root)
    res = q.submit(make_bundle(root, "a", prereg="999-other"), "001-x", "alice")
    assert res.outcome is Outcome.REJECTED and "prereg:001-x" in res.reason


@pytest.mark.parametrize("code", ["import socket\n", "import subprocess\n", "x = eval('1')\n",
                                  "from urllib import request\n"])
def test_static_check_rejects_banned_constructs(root, code):
    q, _ = new_queue(root)
    res = q.submit(make_bundle(root, "a", strategy=STRATEGY + code), "001-x", "alice")
    assert res.outcome is Outcome.REJECTED


def test_missing_file_or_symlink_in_bundle_is_rejected(root):
    q, _ = new_queue(root)
    d = make_bundle(root, "a")
    (d / "config.yaml").unlink()
    assert q.submit(d, "001-x", "alice").outcome is Outcome.REJECTED
    (d / "config.yaml").symlink_to(d / "manifest.yaml")
    assert q.submit(d, "001-x", "alice").outcome is Outcome.REJECTED


# ───────────────────────── 核准 ─────────────────────────

def test_approve_records_trial_signs_and_runs_once(root):
    q, stub = new_queue(root)
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id
    before = len(ledger(root))
    res = q.approve(sid, "kevin")
    assert res.outcome is Outcome.APPROVED and stub.calls == [sid]
    rows = [r for r in ledger(root) if r["trial_id"] == f"t-sub-{sid}"]
    assert len(ledger(root)) == before + 2                         # 核准行＋完成行
    approved, done = rows
    assert approved["stat_decision"]["delta_n"] == 1 and approved["state"] == "approved"
    assert approved["approval"]["approved_by"] == approved["approval"]["signed_by"] == "kevin"
    assert approved["approval"]["signed"] is True and len(approved["approval"]["prereg_sha256"]) == 64
    assert done["state"] == "complete" and done["stat_decision"]["delta_n"] == 1
    assert done["stat_decision"]["p_value"] == 0.01 and done["approval"] == approved["approval"]
    snap = q.snapshot()
    assert snap.trials == {sid: 1} and snap.runs == {sid: 1} and snap.pool == {sid} == snap.signed
    audit = audit_ledger(ledger(root))
    assert audit["auditable"], audit["problems"]
    assert audit["n_selection_recomputed"] == 1                    # 完成行不讓 N 加倍


def test_approve_is_idempotent_and_never_reruns(root):
    q, stub = new_queue(root)
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id
    q.approve(sid, "kevin")
    n = len(ledger(root))
    assert q.approve(sid, "admin").outcome is Outcome.NOOP
    assert q.submit(make_bundle(root, "a"), "001-x", "alice").outcome is Outcome.NOOP
    assert stub.calls == [sid] and len(ledger(root)) == n
    assert q.snapshot().approved_by == {sid: "kevin"}


def test_non_approver_and_unknown_submission_are_rejected_without_writes(root):
    q, stub = new_queue(root)
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id
    n = len(ledger(root))
    assert q.approve(sid, "intruder").outcome is Outcome.REJECTED
    assert q.approve("s-doesnotexist", "kevin").outcome is Outcome.REJECTED
    assert len(ledger(root)) == n and stub.calls == [] and q.status(sid) is Status.PENDING


def test_tampered_bundle_is_not_approved(root):
    q, stub = new_queue(root)
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id
    (root / "strategies" / "_submitted" / sid / "strategy.py").write_text("x = 1\n", encoding="utf-8")
    res = q.approve(sid, "kevin")
    assert res.outcome is Outcome.REJECTED and "bundle" in res.reason and stub.calls == []


def test_prereg_is_rechecked_at_approval(root):
    q, stub = new_queue(root)
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id
    p = root / "experiments" / "preregistration" / "001-x.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "# 事後改過\n", encoding="utf-8")
    assert q.approve(sid, "kevin").outcome is Outcome.REJECTED and stub.calls == []


def test_trial_n_comes_from_the_ledger_and_counts_other_writers(root):
    """核准當下的 N = ledger 既有 selection 數 + 1（含 graph SelectionLedger 寫的）。"""
    other = {"trial_id": "t-graph-x", "stat_decision": {"delta_n": 1}}
    with (root / "log" / "trials.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(other) + "\n")
    q, _ = new_queue(root)
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id
    q.approve(sid, "kevin", run=False)
    row = [r for r in ledger(root) if r["trial_id"] == f"t-sub-{sid}"][0]
    assert row["stat_decision"]["decision_path"]["n_trials"] == 2


def test_failed_backtest_still_counts_and_is_not_rerun(root):
    q, stub = new_queue(root, Counter(boom=True))
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id
    assert q.approve(sid, "kevin").outcome is Outcome.APPROVED
    last = ledger(root)[-1]
    assert last["state"] == "failed" and last["stat_decision"]["delta_n"] == 1
    assert q.approve(sid, "kevin").outcome is Outcome.NOOP and len(stub.calls) == 1
    q._execute(sid)                                                # 直接再叫也不會重跑
    assert len(stub.calls) == 1


def test_crash_after_ledger_append_keeps_trial_and_never_double_runs(root, monkeypatch):
    """append 之後、回測之前 crash：trial 已計（保守方向）、runs=0；再核准是 noop 且之後只會跑一次。"""
    q, stub = new_queue(root)
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id

    def boom(self, s):
        raise KeyboardInterrupt
    monkeypatch.setattr(SubmissionQueue, "_execute", boom)
    with pytest.raises(KeyboardInterrupt):
        q.approve(sid, "kevin")
    assert q.status(sid) is Status.APPROVED and q.snapshot().trials == {sid: 1} and stub.calls == []
    monkeypatch.undo()
    assert q.approve(sid, "kevin", run=False).outcome is Outcome.NOOP
    q._execute(sid)
    q._execute(sid)
    assert stub.calls == [sid]


def test_crash_before_ledger_append_leaves_it_pending(root, monkeypatch):
    q, stub = new_queue(root)
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id

    def boom(self, record):
        raise OSError("disk full")
    monkeypatch.setattr(SubmissionQueue, "_append_trial", boom)
    with pytest.raises(OSError):
        q.approve(sid, "kevin")
    assert q.status(sid) is Status.PENDING and stub.calls == [] and q.snapshot().trials == {}


# ───────────────────────── 退出 ─────────────────────────

def test_retire_removes_from_pool_but_keeps_trial(root):
    q, stub = new_queue(root)
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id
    assert q.retire(sid, "kevin").outcome is Outcome.REJECTED     # 未核准不能退
    q.approve(sid, "kevin")
    assert q.retire(sid, "admin").outcome is Outcome.RETIRED
    snap = q.snapshot()
    assert snap.status[sid] == "retired" and snap.pool == frozenset() and snap.trials == {sid: 1}
    assert q.retire(sid, "admin").outcome is Outcome.REJECTED
    assert q.approve(sid, "kevin").outcome is Outcome.REJECTED     # 退出後不可重新核准
    assert stub.calls == [sid]


# ───────────────────────── 隨機動作序列 ─────────────────────────

@pytest.mark.parametrize("seed", range(8))
def test_random_action_sequences_keep_all_six_invariants(root, seed):
    rng = random.Random(seed)
    q, stub = new_queue(root)
    bundles = [make_bundle(root, n, variant=n) for n in ("a", "b")]
    sids = [None, None]
    floor: dict = {}
    for _ in range(40):
        op, i = rng.choice(["submit", "approve", "retire", "resubmit", "reapprove"]), rng.randrange(2)
        who = rng.choice(["kevin", "admin", "intruder"])
        if op in ("submit", "resubmit"):
            res = q.submit(bundles[i], "001-x", "alice")
            sids[i] = res.submission_id
        elif sids[i]:
            res = q.approve(sids[i], who) if op in ("approve", "reapprove") else q.retire(sids[i], who)
        floor = check_invariants(q, stub, floor)


# ───────────────────────── 並發 ─────────────────────────

def _worker(root, sid, who, marker):
    def stub(sub):
        with open(marker, "a", encoding="utf-8") as fh:
            fh.write(sub.sid + "\n")
        return BacktestRun(p_value=0.5, p_value_source="t")
    q = SubmissionQueue(root, approvers=APPROVERS, backtest=stub)
    print(q.approve(sid, who).outcome.value)


def test_concurrent_approvals_in_separate_processes_run_and_count_once(root):
    q, _ = new_queue(root)
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id
    marker = root / "ran.txt"
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_worker, args=(str(root), sid, w, str(marker)))
             for w in ("kevin", "admin", "kevin")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0
    rows = [r for r in ledger(root) if r["trial_id"] == f"t-sub-{sid}" and r["state"] == "approved"]
    assert len(rows) == 1
    assert marker.read_text(encoding="utf-8").split() == [sid]
    assert sum(1 for e in events(root) if e["event"] == "run_started") == 1
    assert audit_ledger(ledger(root))["n_selection_recomputed"] == 1


# ───────────────────────── 守門與保護路徑 ─────────────────────────

def test_runner_guard_blocks_unapproved_submitted_strategies(root):
    q, _ = new_queue(root)
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id
    path = root / "strategies" / "_submitted" / sid / "strategy.py"
    with pytest.raises(PermissionError):
        require_approved_for_submitted(path, None)
    with pytest.raises(PermissionError):
        require_approved_for_submitted(path, f"t-sub-{sid}")       # pending：ledger 無核准行
    q.approve(sid, "kevin", run=False)
    require_approved_for_submitted(path, f"t-sub-{sid}")
    with pytest.raises(PermissionError):
        require_approved_for_submitted(path, "t-sub-other")
    require_approved_for_submitted(root / "strategies" / "tsmom" / "strategy.py", None)  # 其他路徑不受影響


def test_queue_writes_pass_the_append_only_gate(root, monkeypatch):
    """佇列自己產生的變更必須符合 check_append_only，否則第一次 commit 就被 hook 擋。"""
    q, _ = new_queue(root)
    sid = q.submit(make_bundle(root, "a"), "001-x", "alice").submission_id
    q.approve(sid, "kevin")
    monkeypatch.chdir(root)
    _git(root, "add", "-A")
    assert cao.check() == []


# ───────────────────────── CLI ─────────────────────────

def test_cli_end_to_end(root, capsys):
    (root / "config").mkdir()
    (root / "config" / "approvers.yaml").write_text("approvers: [kevin]\n", encoding="utf-8")
    stub = Counter()
    b = str(make_bundle(root, "a"))
    assert cli.main(["--root", str(root), "submit", b, "--prereg", "001-x", "--submitter", "alice"],
                    backtest=stub) == 0
    sid = capsys.readouterr().out.split("\t")[1].strip()
    assert cli.main(["--root", str(root), "list"], backtest=stub) == 0
    assert f"{sid}\tpending" in capsys.readouterr().out
    assert cli.main(["--root", str(root), "approve", sid, "--as", "mallory"], backtest=stub) == 1
    assert stub.calls == []
    assert cli.main(["--root", str(root), "approve", sid, "--as", "kevin"], backtest=stub) == 0
    assert cli.main(["--root", str(root), "list"], backtest=stub) == 0
    assert f"{sid}\tapproved\truns=1\ttrial=1" in capsys.readouterr().out
