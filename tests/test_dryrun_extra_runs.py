"""--extra-runs：外部觀察層 run 附加在帳本 run 之後；不合格整份拒絕、不影響帳本 run。全部使用合成資料。"""
import copy
import json
from pathlib import Path

import pytest

from strategies._common.dryrun import snapshot
from strategies._common.dryrun.ledger import DryLedger
from tests.test_dryrun_ops import _ok, _run

SCEN = ("optimistic", "base", "pessimistic")
EXTRA = {
    "strategy_id": "ext_demo__case_a", "lineage_id": "ext/x/case_a", "branch": "r1", "sha8": "abcd1234",
    "classification": ["unknown"], "start_date": "2026-11-02", "status": "paused", "benchmark_status": "NOT_APPLICABLE",
    "last_session": "2026-11-02", "trailing_missing": 0, "days": 0, "equity": {s: [] for s in SCEN},
    "cost_cum": {"spread": 0, "impact": 0, "commission": 0}, "tier": "observing", "label": "觀察中",
    "d1": {"verdict": "blocked", "reasons": ["x"], "pending": ["y"], "screened_at": "n/a", "rules_version": "ext"},
    "history": {"basis": "in_sample_backfill", "label": "樣本內", "start": "2023-06-29", "end": "2026-11-02",
                "equity": {s: [["2023-06-29", 5000000.0], ["2026-11-02", 5100000.0]] for s in SCEN}},
}


def _setup(tmp_path):
    led = tmp_path / "dry_ledger.sqlite"
    L = DryLedger(led)
    rid = _run(L, "vgrsi_tx")
    _ok(L, rid, "2026-10-28")
    L.close()
    s = tmp_path / "s" / "vgrsi_tx"
    s.mkdir(parents=True)
    (s / "manifest.yaml").write_text("id: vgrsi_tx\ntags:\n- paper\n", encoding="utf-8")
    return led, tmp_path / "s"


def _extra_file(tmp_path, runs):
    p = tmp_path / "extra.json"
    p.write_text(json.dumps({"schema": "strategy-live-snapshot/3", "generated_at": "x", "runs": runs, "alerts": []}),
                 encoding="utf-8")
    return p


def _main(led, sdir, out, *args):
    return snapshot.main(["--ledger", str(led), "--strategies-dir", str(sdir), "--out", str(out), *args])


def test_extra_runs_appended_after_ledger_runs(tmp_path):
    led, sdir = _setup(tmp_path)
    out = tmp_path / "o.json"
    assert _main(led, sdir, out, "--extra-runs", str(_extra_file(tmp_path, [EXTRA]))) == 0
    snap = json.loads(out.read_text(encoding="utf-8"))
    assert snap["schema"] == "strategy-live-snapshot/2"                  # 隱含 observing
    assert [r["strategy_id"] for r in snap["runs"]] == ["vgrsi_tx", "ext_demo__case_a"]
    assert snap["runs"][1] == EXTRA


def test_without_flag_output_unchanged(tmp_path):
    led, sdir = _setup(tmp_path)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _main(led, sdir, a)
    _main(led, sdir, b)
    ja, jb = json.loads(a.read_text(encoding="utf-8")), json.loads(b.read_text(encoding="utf-8"))
    ja.pop("generated_at"), jb.pop("generated_at")
    assert ja == jb and ja["schema"] == "strategy-live-snapshot/1" and len(ja["runs"]) == 1


def _mut(f):
    r = copy.deepcopy(EXTRA)
    f(r)
    return r


@pytest.mark.parametrize("bad", [
    _mut(lambda r: r.update(classification=["open"])),
    _mut(lambda r: r.update(classification=["unknown", "open"])),
    _mut(lambda r: r.update(tier="promoted")),
    _mut(lambda r: r.update(extra_field=1)),
    _mut(lambda r: r.pop("history")),
    _mut(lambda r: r.update(strategy_id="vgrsi_tx")),                    # 與帳本衝突
    _mut(lambda r: r.update(strategy_id="../x")),
])
def test_bad_extra_rejected_and_nothing_written(tmp_path, bad):
    led, sdir = _setup(tmp_path)
    out = tmp_path / "o.json"
    assert _main(led, sdir, out, "--extra-runs", str(_extra_file(tmp_path, [bad]))) == 2
    assert not out.exists()


def test_duplicate_ids_inside_extra_rejected(tmp_path):
    led, sdir = _setup(tmp_path)
    out = tmp_path / "o.json"
    assert _main(led, sdir, out, "--extra-runs", str(_extra_file(tmp_path, [EXTRA, EXTRA]))) == 2


def test_missing_or_empty_file_rejected(tmp_path):
    led, sdir = _setup(tmp_path)
    assert _main(led, sdir, tmp_path / "o.json", "--extra-runs", str(tmp_path / "nope.json")) == 2
    assert _main(led, sdir, tmp_path / "o.json", "--extra-runs", str(_extra_file(tmp_path, []))) == 2


def test_daily_script_falls_back_without_extra():
    s = (Path(__file__).resolve().parents[1] / "scripts" / "dryrun_run_daily.sh").read_text(encoding="utf-8")
    assert "DRYRUN_EXTRA_RUNS" in s and "--extra-runs" in s
    assert "退回不帶外部 run" in s                                       # 失敗時退回原本的快照，不影響推送
