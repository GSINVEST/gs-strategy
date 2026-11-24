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

def _write_named(folder, name, runs, alerts=None):
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text(json.dumps({"schema": snapshot.SCHEMA_V3,
                               "generated_at": "2026-11-26T13:20:05+08:00",
                               "runs": runs, "alerts": alerts or []}), encoding="utf-8")
    return path


def _named(sid):
    return _mut(lambda r: r.update(strategy_id=sid))


def test_directory_isolates_corrupt_file_and_keeps_eight_gdr_runs(tmp_path, capsys):
    led, sdir = _setup(tmp_path)
    folder = tmp_path / "extra.d"
    _write_named(folder, "gdr.json", [_named(f"gdr__{i}") for i in range(8)])
    (folder / "ext-b.json").write_text("broken", encoding="utf-8")
    out = tmp_path / "out.json"
    assert _main(led, sdir, out, "--extra-runs-dir", str(folder)) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert [r["strategy_id"] for r in result["runs"]] == ["vgrsi_tx"] + [f"gdr__{i}" for i in range(8)]
    assert capsys.readouterr().err.count("[WARN]") == 1


def test_directory_alert_passes_through_unchanged(tmp_path):
    led, sdir = _setup(tmp_path)
    folder = tmp_path / "extra.d"
    alert = {"strategy_id": "ext_b__1", "level": "CRIT", "message": "外部來源告警（合成）"}
    _write_named(folder, "ext-b.json", [_named("ext_b__1")], [alert])
    out = tmp_path / "out.json"
    assert _main(led, sdir, out, "--extra-runs-dir", str(folder)) == 0
    assert alert in json.loads(out.read_text(encoding="utf-8"))["alerts"]


@pytest.mark.parametrize("alert", [
    {"strategy_id": "missing", "level": "CRIT", "message": "合成"},
    {"strategy_id": "ext_demo__case_a", "level": "INFO", "message": "合成"},
    {"strategy_id": "ext_demo__case_a", "level": "CRIT", "message": "合" * 301},
    {"strategy_id": "ext_demo__case_a", "level": "CRIT", "message": "合成", "extra": 1},
])
def test_bad_alert_skips_whole_file(tmp_path, capsys, alert):
    folder = tmp_path / "extra.d"
    _write_named(folder, "a.json", [EXTRA], [alert])
    _write_named(folder, "b.json", [_named("safe")])
    runs, alerts = snapshot.load_extra_runs_dir(folder, [])
    assert [r["strategy_id"] for r in runs] == ["safe"] and alerts == []
    assert "[WARN]" in capsys.readouterr().err


def test_duplicate_later_file_is_atomic_and_does_not_reserve_ids(tmp_path, capsys):
    folder = tmp_path / "extra.d"
    _write_named(folder, "a.json", [_named("first")])
    _write_named(folder, "b.json", [_named("new"), _named("first")])
    _write_named(folder, "c.json", [_named("new")])
    runs, _ = snapshot.load_extra_runs_dir(folder, [])
    assert [r["strategy_id"] for r in runs] == ["first", "new"]
    assert capsys.readouterr().err.count("[WARN]") == 1


def test_all_invalid_directory_still_produces_ledger_snapshot(tmp_path, capsys):
    led, sdir = _setup(tmp_path)
    folder = tmp_path / "extra.d"
    _write_named(folder, "bad.json", [])
    out = tmp_path / "out.json"
    assert _main(led, sdir, out, "--extra-runs-dir", str(folder)) == 0
    assert [r["strategy_id"] for r in json.loads(out.read_text(encoding="utf-8"))["runs"]] == ["vgrsi_tx"]
    assert "[WARN]" in capsys.readouterr().err


def test_directory_precedes_legacy_file(tmp_path, capsys):
    led, sdir = _setup(tmp_path)
    folder = tmp_path / "extra.d"
    _write_named(folder, "a.json", [_named("directory")])
    legacy = _extra_file(tmp_path, [_named("legacy")])
    out = tmp_path / "out.json"
    assert _main(led, sdir, out, "--extra-runs-dir", str(folder), "--extra-runs", str(legacy)) == 0
    assert [r["strategy_id"] for r in json.loads(out.read_text(encoding="utf-8"))["runs"]] == ["vgrsi_tx", "directory"]
    assert capsys.readouterr().err.count("[WARN]") == 1


def test_alert_cap_preserves_internal_alerts_and_first_external(tmp_path, capsys):
    internal = {"strategy_id": "internal", "level": "WARN", "message": "帳本"}
    ext = {"strategy_id": "ext_demo__case_a", "level": "CRIT", "message": "外部"}
    snap = {"schema": snapshot.SCHEMA_V2, "runs": [], "alerts": [internal] * 199}
    snapshot.append_extra_snapshot(snap, [EXTRA], [ext] * 3)
    assert snap["alerts"] == [internal] * 199 + [ext]
    assert "200" in capsys.readouterr().err


@pytest.mark.parametrize("bad", [
    _mut(lambda r: r.update(status="unknown")),
    _mut(lambda r: r.update(label="字" * 101)),
    _mut(lambda r: r.update(d1=None)),
    _mut(lambda r: r["d1"].update(rules_version="x" * 33)),
    _mut(lambda r: r["equity"].update(base=[["2026-11-02", float("nan")]])),
    _mut(lambda r: r.update(classification=[{}])),
    _mut(lambda r: r.update(history={"bad": 1})),
])
def test_nested_invalid_run_does_not_poison_other_files(tmp_path, capsys, bad):
    folder = tmp_path / "extra.d"
    _write_named(folder, "a.json", [bad])
    _write_named(folder, "b.json", [_named("safe")])
    runs, _ = snapshot.load_extra_runs_dir(folder, [])
    assert [r["strategy_id"] for r in runs] == ["safe"]
    assert "[WARN]" in capsys.readouterr().err


def test_directory_run_capacity_skips_later_file(tmp_path, capsys):
    folder = tmp_path / "extra.d"
    _write_named(folder, "a.json", [_named(f"ext_{i}") for i in range(99)])
    _write_named(folder, "b.json", [_named("overflow")])
    runs, _ = snapshot.load_extra_runs_dir(folder, ["ledger"])
    assert len(runs) == 99
    assert "[WARN]" in capsys.readouterr().err


def test_warning_does_not_echo_external_names_or_values(tmp_path, capsys):
    folder = tmp_path / "extra.d"
    _write_named(folder, "untrusted-input.json", [_named("ledger")])
    snapshot.load_extra_runs_dir(folder, ["ledger"])
    err = capsys.readouterr().err
    assert "untrusted-input" not in err and "ledger" not in err


def test_daily_script_prefers_directory_before_legacy():
    script = (Path(__file__).resolve().parents[1] / "scripts/dryrun_run_daily.sh").read_text(encoding="utf-8")
    assert 'EXTRA_ARGS=(--extra-runs-dir "$DRYRUN_EXTRA_RUNS_DIR")' in script
    assert 'elif [ -n "${DRYRUN_EXTRA_RUNS:-}" ]' in script
    assert "資料夾優先、忽略單檔" in script

def test_legacy_single_file_also_includes_alerts(tmp_path):
    led, sdir = _setup(tmp_path)
    alert = {"strategy_id": EXTRA["strategy_id"], "level": "WARN", "message": ""}
    path = _write_named(tmp_path, "legacy.json", [EXTRA], [alert])
    out = tmp_path / "out.json"
    assert _main(led, sdir, out, "--extra-runs", str(path)) == 0
    assert alert in json.loads(out.read_text(encoding="utf-8"))["alerts"]


def test_directory_combined_byte_limit_keeps_prior_file(tmp_path, capsys):
    folder = tmp_path / "extra.d"
    large = _named("large")
    large["equity"] = {s: [["2026-11-02", 10000000.0] for _ in range(15000)] for s in SCEN}
    _write_named(folder, "a.json", [large])
    second = copy.deepcopy(large)
    second["strategy_id"] = "second"
    _write_named(folder, "b.json", [second])
    _write_named(folder, "c.json", [_named("small")])
    runs, _ = snapshot.load_extra_runs_dir(folder, [])
    assert [r["strategy_id"] for r in runs] == ["large", "small"]
    assert capsys.readouterr().err.count("[WARN]") == 1


def test_invalid_encoding_is_isolated(tmp_path, capsys):
    folder = tmp_path / "extra.d"
    _write_named(folder, "b.json", [EXTRA])
    (folder / "a.json").write_bytes(b"\xff\xfe")
    runs, _ = snapshot.load_extra_runs_dir(folder, [])
    assert runs == [EXTRA]
    assert "[WARN]" in capsys.readouterr().err


def test_missing_directory_preserves_ledger(tmp_path, capsys):
    led, sdir = _setup(tmp_path)
    out = tmp_path / "out.json"
    assert _main(led, sdir, out, "--extra-runs-dir", str(tmp_path / "missing")) == 0
    assert len(json.loads(out.read_text(encoding="utf-8"))["runs"]) == 1
    assert "[WARN]" in capsys.readouterr().err

def test_huge_integer_is_isolated(tmp_path, capsys):
    folder = tmp_path / "extra.d"
    bad = _mut(lambda r: r["cost_cum"].update(impact=10 ** 400))
    _write_named(folder, "a.json", [bad])
    _write_named(folder, "b.json", [_named("safe")])
    runs, _ = snapshot.load_extra_runs_dir(folder, [])
    assert [r["strategy_id"] for r in runs] == ["safe"]
    assert "[WARN]" in capsys.readouterr().err


def test_internal_alert_overflow_is_capped_when_appending_external(tmp_path, capsys):
    internal = {"strategy_id": "internal", "level": "WARN", "message": "帳本"}
    ext = {"strategy_id": "ext_demo__case_a", "level": "CRIT", "message": "外部"}
    snap = {"schema": snapshot.SCHEMA_V2, "runs": [], "alerts": [internal] * 201}
    snapshot.append_extra_snapshot(snap, [EXTRA], [ext])
    assert snap["alerts"] == [internal] * 200
    assert "200" in capsys.readouterr().err
