"""genesis-deck `Queue.tla` 軌跡回放用的 adapter：把真正的 SubmissionQueue 包成參考實作的介面。

回放函式（見 test_queue_replay.py）只依賴鴨子型別：
方法 add_prereg / submit / approve / retire，回傳 "ok"/"queued"/"noop"/"approved"/"rejected"/"retired"；
屬性 prereg_ids / status / runs / trials / approved_by / signed / pool，鍵是 TLC 的策略名（s1、s2）。

誠實的邊界：
* ``runs`` 取自回測**樁的觀測值**，不是從 ledger 讀，否則 TrialLedger 會變同義反覆。
* ``pool`` 這裡定義為「狀態為 approved 者」，**刻意不**用 snapshot().pool（那個已經濾掉未簽核的，
  SignedBeforePool 會變成結構上恆真，負控制就抓不到）。
* 每個動作 run=True 同步執行回測，所以每步結束時 trials 與 runs 相等；ledger 已記帳但回測尚未
  開始的中間態（crash 窗口）不在回放範圍，由 tests/test_submission_queue.py 覆蓋。
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import yaml

from strategies._common.submission import BacktestRun, Outcome, SubmissionQueue
from tests.test_append_only_and_prereg import _prereg

STRATEGY = "def initialize(context):\n    pass\n\n\ndef handle_data(context, data):\n    pass\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class RealQueue:
    queue_cls = SubmissionQueue

    def __init__(self, approvers=("kevin", "admin"), root: Path | None = None):
        self.root = Path(root) if root else Path(tempfile.mkdtemp(prefix="queue-replay-"))
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "t")
        (self.root / "log").mkdir(exist_ok=True)
        (self.root / "log" / "trials.jsonl").write_text(
            json.dumps({"trial_id": "t-seed", "stat_decision": {
                "delta_n": 0, "no_statistical_claim": True, "basis": "seed"}}) + "\n", encoding="utf-8")
        (self.root / "experiments" / "preregistration").mkdir(parents=True)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")
        self.calls: list[str] = []                 # 回測樁的觀測值（sid）
        self.q = self.queue_cls(self.root, approvers=approvers, backtest=self._stub)
        self._prereg_of: dict[str, str] = {}       # 策略名 -> 已凍結的 prereg id
        self._sid_of: dict[str, str] = {}          # 策略名 -> submission_id
        self._n = 0

    def _stub(self, sub):
        self.calls.append(sub.sid)
        return BacktestRun(p_value=0.5, p_value_source="replay stub")

    # ------------------------------------------------------------ 動作
    def add_prereg(self, name: str) -> None:
        if name in self._prereg_of:
            return
        self._n += 1
        pid = f"{self._n:03d}-{name}"
        path = self.root / "experiments" / "preregistration" / f"{pid}.yaml"
        path.write_text(yaml.safe_dump(_prereg(id=pid), allow_unicode=True), encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", f"prereg {pid}")
        self._prereg_of[name] = pid

    def _bundle(self, name: str, pid: str) -> Path:
        d = self.root / "incoming" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "strategy.py").write_text(STRATEGY + f"# {name}\n", encoding="utf-8")
        (d / "config.yaml").write_text("start: '2020-01-01'\nend: '2021-01-01'\n", encoding="utf-8")
        (d / "manifest.yaml").write_text(yaml.safe_dump({"name": name, "prereg_id": pid}), encoding="utf-8")
        return d

    def submit(self, name: str) -> str:
        # 沒有 prereg 的策略仍然嘗試提交——指向一份不存在的登記檔，必須被真實作擋下。
        pid = self._prereg_of.get(name, f"900-{name}")
        res = self.q.submit(self._bundle(name, pid), pid, "replay")
        if res.outcome is not Outcome.REJECTED:
            self._sid_of[name] = res.submission_id
        return res.outcome.value

    def approve(self, name: str, who: str) -> str:
        sid = self._sid_of.get(name)
        return "rejected" if sid is None else self.q.approve(sid, who).outcome.value

    def retire(self, name: str) -> str:
        sid = self._sid_of.get(name)
        return "rejected" if sid is None else self.q.retire(sid, "kevin").outcome.value

    # ------------------------------------------------------------ 觀測（鍵為策略名）
    def _name(self, sid: str) -> str:
        return next(n for n, s in self._sid_of.items() if s == sid)

    @property
    def prereg_ids(self) -> set[str]:
        committed = self.q.snapshot().prereg_ids
        return {n for n, pid in self._prereg_of.items() if pid in committed}

    @property
    def status(self) -> dict[str, str]:
        return {self._name(s): v for s, v in self.q.snapshot().status.items()}

    @property
    def runs(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for sid in self.calls:
            out[self._name(sid)] = out.get(self._name(sid), 0) + 1
        return out

    @property
    def trials(self) -> dict[str, int]:
        return {self._name(s): n for s, n in self.q.snapshot().trials.items()}

    @property
    def approved_by(self) -> dict[str, str]:
        return {self._name(s): w for s, w in self.q.snapshot().approved_by.items()}

    @property
    def signed(self) -> set[str]:
        return {self._name(s) for s in self.q.snapshot().signed}

    @property
    def pool(self) -> set[str]:
        return {n for n, st in self.status.items() if st == "approved"}
