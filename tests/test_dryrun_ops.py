"""dry run 營運工具：健康檢查（告警）與帳本備份。"""
import datetime as dt
import sqlite3

import pytest

from strategies._common.dryrun import ops
from strategies._common.dryrun.ledger import DryLedger


def _run(led, sid="vgrsi_tx"):
    return led.start_run(strategy_id=sid, lineage_id="L", branch="b", strategy_sha256="s" * 8,
                         framework_version="f", cost_model_version="L1-1", cost_params={},
                         promoted_data_snapshot="x", start_date="2026-10-26", capital_base=5e6,
                         benchmark_status="D2_UNAVAILABLE", benchmark_pack="", ts="t")


def _ok(led, rid, session):
    d = {"equity": 5e6, "daily_pnl": 0.0, "position_json": "{}", "cum_spread": 0.0,
         "cum_impact": 0.0, "cum_commission": 0.0, "flags": []}
    led.append_day(rid, session, {s: d for s in ("optimistic", "base", "pessimistic")}, [],
                   data_snapshot="2026-10-29", strategy_sha256="s" * 8, ts="t")


def _miss(led, rid, session):
    led.append_missing(rid, session, data_snapshot="2026-10-29", strategy_sha256="s" * 8,
                       reason="SNAPSHOT_STALE", ts="t")


TODAY = dt.date(2026, 10, 30)


def _levels(path):
    return [lv for lv, _ in ops.check_health(path, today=TODAY)]


def test_missing_ledger_is_critical(tmp_path):
    assert _levels(tmp_path / "none.sqlite") == [ops.CRIT]


def test_empty_ledger_warns(tmp_path):
    p = tmp_path / "dry_ledger.sqlite"
    DryLedger(p).close()
    assert _levels(p) == [ops.WARN]


def test_healthy(tmp_path):
    p = tmp_path / "dry_ledger.sqlite"
    led = DryLedger(p)
    rid = _run(led)
    _ok(led, rid, "2026-10-28")
    _ok(led, rid, "2026-10-29")
    led.close()
    assert _levels(p) == [ops.OK]


def test_one_missing_warns_three_is_critical(tmp_path):
    p = tmp_path / "dry_ledger.sqlite"
    led = DryLedger(p)
    rid = _run(led)
    _ok(led, rid, "2026-10-27")
    _miss(led, rid, "2026-10-28")
    led.close()
    assert max(_levels(p)) == ops.WARN
    led = DryLedger(p)
    _miss(led, rid, "2026-10-29")
    led.close()
    assert max(_levels(p)) == ops.WARN
    led = DryLedger(p)
    _miss(led, rid, "2026-10-30")
    led.close()
    assert max(_levels(p)) == ops.CRIT


def test_paused_is_critical_and_resume_clears(tmp_path):
    p = tmp_path / "dry_ledger.sqlite"
    led = DryLedger(p)
    rid = _run(led)
    for s in ("2026-10-27", "2026-10-28", "2026-10-29"):
        _miss(led, rid, s)
    led.append_event(rid, "t", "PAUSED", "連續 3 個交易日缺漏")
    led.close()
    assert max(_levels(p)) == ops.CRIT
    led = DryLedger(p)
    led.append_event(rid, "t", "RESUMED", "watermark=2026-10-29; by=x; note")
    led.close()
    assert max(_levels(p)) < ops.CRIT


def test_stale_ledger_is_critical(tmp_path):
    p = tmp_path / "dry_ledger.sqlite"
    led = DryLedger(p)
    rid = _run(led)
    _ok(led, rid, "2026-10-20")
    led.close()
    assert ops.CRIT in _levels(p)


def test_health_cli_writes_alert_log_and_exit_code(tmp_path, monkeypatch):
    p = tmp_path / "dry_ledger.sqlite"
    led = DryLedger(p)
    rid = _run(led)
    _ok(led, rid, "2026-10-20")
    led.close()
    monkeypatch.delenv("DRYRUN_ALERT_CMD", raising=False)
    log = tmp_path / "alerts" / "alerts.log"
    rc = ops.main(["health", "--ledger", str(p), "--alert-log", str(log)])
    assert rc == ops.CRIT
    assert "vgrsi_tx" in log.read_text(encoding="utf-8")


def test_backup_is_consistent_rotates_and_leaves_source_alone(tmp_path):
    p = tmp_path / "dry_ledger.sqlite"
    led = DryLedger(p)
    rid = _run(led)
    _ok(led, rid, "2026-10-29")
    led.close()
    before = p.read_bytes()
    dest = tmp_path / "bk"
    dest.mkdir()
    (dest / "keep_me.txt").write_text("x")
    made = []
    for i in range(4):
        made.append(ops.backup_ledger(p, dest, keep=2, now=dt.datetime(2026, 10, 30, 8, 0, i)))
    left = sorted(f.name for f in dest.glob("dry_ledger-*.sqlite"))
    assert left == [made[2].name, made[3].name]
    assert (dest / "keep_me.txt").exists()          # 輪替只動符合命名的備份檔
    assert p.read_bytes() == before                  # 來源帳本沒被改
    c = sqlite3.connect(made[3])
    assert c.execute("select count(*) from dry_daily").fetchone()[0] == 3
    c.close()
    assert not list(dest.glob("*.partial"))


def test_backup_missing_source_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        ops.backup_ledger(tmp_path / "nope.sqlite", tmp_path / "bk")
    assert ops.main(["backup", "--ledger", str(tmp_path / "nope.sqlite"), "--dest", str(tmp_path / "bk")]) == ops.CRIT


def test_alert_cmd_receives_message_on_stdin(tmp_path, monkeypatch):
    import sys
    p = tmp_path / "dry_ledger.sqlite"
    led = DryLedger(p)
    rid = _run(led)
    _ok(led, rid, "2026-10-20")
    led.close()
    out = tmp_path / "got.txt"
    script = tmp_path / "sink.py"
    script.write_text("import sys,pathlib;pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(),encoding='utf-8')",
                      encoding="utf-8")
    monkeypatch.setenv("DRYRUN_ALERT_CMD", f'"{sys.executable}" "{script.as_posix()}" "{out.as_posix()}"')
    assert ops.main(["health", "--ledger", str(p)]) == ops.CRIT
    got = out.read_text(encoding="utf-8")
    assert "vgrsi_tx" in got and got.endswith(chr(10))


def test_alert_cmd_failure_does_not_crash_health(tmp_path, monkeypatch):
    p = tmp_path / "dry_ledger.sqlite"
    DryLedger(p).close()
    monkeypatch.setenv("DRYRUN_ALERT_CMD", "no-such-command-xyz")
    assert ops.main(["health", "--ledger", str(p)]) == ops.WARN
