"""genesis-deck `specs/tla/strategy-queue/Queue.tla` 的軌跡回放，對象是**真正的 SubmissionQueue**。

traces/ 與 vendor/ 是從 genesis-deck 複製來的（來源 commit 與雜湊見 SOURCES.sha256 與 SOURCES.md）：
改動它們會讓 test_vendored_files_match_recorded_hashes 失敗。replay／check_invariants 逐字沿用
genesis-deck tests/formal/test_strategy_queue_conformance.py 的邏輯（動作名稱與斷言訊息一致）。

這個回放證明：TLC 產生的反例軌跡與手寫軌跡，在真實作上不違反六個不變量，且與參考實作逐步回傳
相同結果。它**不**涵蓋並發、crash 與 ledger 的 git 層行為（見 adapter 檔頭與 tests/test_submission_queue.py）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "vendor"))

from strategy_queue_ref import StrategyQueue                       # noqa: E402  (genesis-deck 參考實作)
from strategies._common.submission import Outcome, Submission, SubmissionQueue  # noqa: E402
from tests.formal.queue_adapter import RealQueue                    # noqa: E402
from tests.test_append_only_and_prereg import _prereg               # noqa: E402

TRACES = HERE / "traces"
QUEUE_TRACES = sorted(TRACES.glob("queue-*.json"))


def violations(q, floor):
    """回傳 [(不變量名, 訊息)]；同一步可能同時違反多個，不只回報第一個。"""
    out = []
    for s in set(q.status) | set(q.runs) | set(q.trials) | set(floor):
        if q.trials.get(s, 0) < floor.get(s, 0):
            out.append(("TrialMonotone", f"TrialMonotone：{s} 的 trial 減少"))
        if q.trials.get(s, 0) != q.runs.get(s, 0):
            out.append(("TrialLedger", f"TrialLedger：{s} 的 trial 與實際回測次數不符"))
        if q.runs.get(s, 0) > 1:
            out.append(("NoDoubleRun", f"NoDoubleRun：{s} 被執行 {q.runs.get(s)} 次"))
        if q.runs.get(s, 0) > 0 and not q.approved_by.get(s):
            out.append(("RunNeedsApproval", f"RunNeedsApproval：{s} 沒有人核准就跑了"))
        if s in q.status and s not in q.prereg_ids:
            out.append(("PreregBeforeSubmit", f"PreregBeforeSubmit：{s} 沒有 prereg 就提交"))
    if not q.pool <= q.signed:
        out.append(("SignedBeforePool", "SignedBeforePool：未簽核就入池"))
    return out


def check_invariants(q, floor):
    v = violations(q, floor)
    assert not v, v[0][1]


def replay(trace, factory=RealQueue, collect=None):
    """collect 是 list 時，把每步的所有違反都收進去（負控制用）；否則第一個違反就丟 AssertionError。"""
    q, results = factory(), []
    for step in trace["steps"]:
        floor = dict(q.trials)
        op, sid = step["op"], step["id"]
        if op == "prereg":
            q.add_prereg(sid)
            results.append("ok")
        elif op == "submit":
            results.append(q.submit(sid))
        elif op == "approve":
            results.append(q.approve(sid, step["tenant"]))
        elif op == "retire":
            results.append(q.retire(sid))
        else:
            raise AssertionError(f"未知動作 {op}")
        if collect is not None:
            collect.extend(n for n, _ in violations(q, floor))
        else:
            check_invariants(q, floor)
    return q, results


def load(name):
    return json.loads((TRACES / name).read_text(encoding="utf-8"))


def test_vendored_files_match_recorded_hashes():
    for line in (HERE / "SOURCES.sha256").read_text(encoding="utf-8").splitlines():
        digest, _, rel = line.partition(" *")
        assert hashlib.sha256((HERE / rel).read_bytes()).hexdigest() == digest, rel


def test_all_nine_genesis_deck_traces_are_present():
    assert len(QUEUE_TRACES) == 9


@pytest.mark.parametrize("path", QUEUE_TRACES, ids=lambda p: p.stem)
def test_real_queue_keeps_the_six_invariants_on_every_trace(path):
    replay(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", QUEUE_TRACES, ids=lambda p: p.stem)
def test_real_queue_agrees_with_the_reference_step_by_step(path):
    """差分測試：同一軌跡丟兩個實作，每一步的回傳值與最終狀態必須一致。"""
    trace = json.loads(path.read_text(encoding="utf-8"))
    real, r_real = replay(trace, RealQueue)
    ref, r_ref = replay(trace, StrategyQueue)
    assert r_real == r_ref
    assert real.status == ref.status
    assert real.runs == ref.runs and real.trials == ref.trials
    assert real.approved_by == ref.approved_by and real.signed == ref.signed and real.pool == ref.pool


def test_happy_path_one_run_one_trial_and_trial_survives_retire():
    q, res = replay(load("queue-happy-path.json"))
    assert res == ["ok", "queued", "approved", "retired"]
    assert q.runs == {"s1": 1} and q.trials == {"s1": 1} and q.pool == set() and q.signed == {"s1"}


def test_submit_does_not_run_a_backtest_and_missing_prereg_is_rejected():
    q, res = replay(load("queue-submitruns.json"))
    assert res == ["ok", "queued"] and q.runs == {} and q.trials == {}
    q, res = replay(load("queue-noprereg.json"))
    assert res == ["rejected"] and q.status == {}


def test_duplicate_approval_and_resubmit_are_idempotent():
    q, res = replay(load("queue-idempotent.json"))
    assert res == ["ok", "queued", "noop", "approved", "noop", "noop", "noop"]
    assert q.runs == {"s1": 1} and q.trials == {"s1": 1} and q.approved_by == {"s1": "kevin"}


# ───── 負控制：故意寫壞的真實作子類，回放對應反例必須失敗；否則這組回放是空洞的 ─────
def _fake_prereg():
    return {"doc": _prereg(), "sha256": "0" * 64}, None


class SkipTrial(SubmissionQueue):
    def _build_trial(self, *a, **kw):                       # 跑了回測但 trial 記 0
        rec = super()._build_trial(*a, **kw)
        rec["stat_decision"]["delta_n"] = 0
        return rec


class TrialDec(SubmissionQueue):
    def retire(self, sid, who):                             # 退出時把 trial 扣回去
        res = super().retire(sid, who)
        if res.outcome is Outcome.RETIRED:
            last = self._fold()["trials"][sid]
            self._append_trial({**last, "stat_decision": {**last["stat_decision"], "delta_n": 0}})
        return res


class NoPrereg(SubmissionQueue):
    def _check_prereg(self, prereg_id):                     # 略過 prereg 檢查
        return _fake_prereg()


class SubmitRuns(SubmissionQueue):
    def submit(self, bundle_dir, prereg_id, submitter):     # 提交就跑回測
        res = super().submit(bundle_dir, prereg_id, submitter)
        if res.outcome is Outcome.QUEUED:
            self.backtest(Submission(res.submission_id, f"t-sub-{res.submission_id}",
                                     self.bundles_dir / res.submission_id, prereg_id, 1))
        return res


class DupApprove(SubmissionQueue):
    def approve(self, sid, who, *, run=True):               # 已核准再核准一次又跑一次
        if self.status(sid) is not None and self.status(sid).value == "approved" and who in self.approvers:
            self.backtest(Submission(sid, f"t-sub-{sid}", self.bundles_dir / sid, "", 1))
            return super().approve(sid, who, run=False).__class__(Outcome.APPROVED, sid)
        return super().approve(sid, who, run=run)


class Resubmit(SubmissionQueue):
    _reopened: set = set()

    def submit(self, bundle_dir, prereg_id, submitter):     # 已核准的被重送回待審
        res = super().submit(bundle_dir, prereg_id, submitter)
        if res.outcome is Outcome.NOOP and self.status(res.submission_id).value == "approved":
            self._reopened = self._reopened | {res.submission_id}
            return res.__class__(Outcome.QUEUED, res.submission_id)
        return res

    def approve(self, sid, who, *, run=True):
        if sid in self._reopened and who in self.approvers:
            self._reopened = self._reopened - {sid}
            self.backtest(Submission(sid, f"t-sub-{sid}", self.bundles_dir / sid, "", 1))
            return super().approve(sid, who, run=False).__class__(Outcome.APPROVED, sid)
        return super().approve(sid, who, run=run)


class NoSign(SubmissionQueue):
    def _build_trial(self, *a, **kw):                       # 入池卻沒簽核
        rec = super()._build_trial(*a, **kw)
        rec["approval"]["signed"] = False
        return rec


def _factory(cls):
    class Broken(RealQueue):
        queue_cls = cls
    return Broken


@pytest.mark.parametrize("name,cls,expected", [
    ("skiptrial", SkipTrial, "TrialLedger"), ("trialdec", TrialDec, "TrialMonotone"),
    ("noprereg", NoPrereg, "PreregBeforeSubmit"), ("submitruns", SubmitRuns, "RunNeedsApproval"),
    ("dupapprove", DupApprove, "NoDoubleRun"), ("resubmit", Resubmit, "NoDoubleRun"),
    ("nosign", NoSign, "SignedBeforePool"),
])
def test_replay_catches_a_broken_real_queue(name, cls, expected):
    """壞實作回放對應反例，必須被**預期的那個**不變量抓到（不只是「某個」）。

    真實作的 trial 是 ledger 上 delta_n 的摺疊值，每個 submission 最多 1，所以多跑一次回測時
    NoDoubleRun 與 TrialLedger 會同時違反；因此逐一收集而不是只看第一個。
    """
    with pytest.raises(AssertionError):
        replay(load(f"queue-{name}.json"), _factory(cls))
    seen: list[str] = []
    replay(load(f"queue-{name}.json"), _factory(cls), collect=seen)
    assert expected in seen, f"{name}：預期 {expected}，實際抓到 {sorted(set(seen))}"
