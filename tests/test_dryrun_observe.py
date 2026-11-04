"""觀察層（observing）：與 promoted 完全分離、不能晉升、快照 tier、D1 原因、歷史回補。

用假的 SimRunner（沿用 test_dryrun_executor 的 FakeRunner），不需 zipline／bundle。
"""
import datetime as dt
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from strategies._common.dryrun import executor as ex
from strategies._common.dryrun import observe, snapshot
from strategies._common.dryrun.benchmark import NoBenchmarkPack
from strategies._common.dryrun.d1view import LABEL, d1_summary
from strategies._common.dryrun.ledger import DryLedger
from strategies._common.dryrun.types import (
    LIFECYCLE_STATES, OBSERVATION_STATES, OBSERVATION_TRANSITIONS,
)
from strategies._common.promotion import registry as R
from strategies._common.promotion.schema import CheckResult, GateReport, verdict_of
from tests.test_dryrun_executor import REPO, FakeRunner

NOW = dt.datetime(2026, 11, 2, 8, 0, tzinfo=dt.timezone.utc)


def _report(verdict_checks, sid="vgrsi_tx"):
    checks = tuple(CheckResult(n, st, v, thr, "") for n, st, v, thr in verdict_checks)
    return GateReport("promotion-report/1", "1", sid, 2, "ledger", checks, verdict_of(checks))


BLOCKED_CHECKS = [("sample", "ok", 1533.0, "n>=250 ok; n>=50 provisional"),
                  ("dsr", "blocked", 0.9416, "DSR>0.95"),
                  ("pbo", "unavailable", None, "PBO<0.5")]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv(R.PROMOTERS_ENV, "admin")
    root = tmp_path / "strategies"
    sdir = root / "vgrsi_tx"
    sdir.mkdir(parents=True)
    for f in ("strategy.py", "config.yaml"):
        shutil.copy(REPO / "strategies/vgrsi_tx" / f, sdir / f)
    shutil.copy(REPO / "strategies/vgrsi_tx/manifest.yaml", sdir / "manifest.yaml")
    reg = tmp_path / "promotions.jsonl"
    return dict(root=root, sdir=sdir, reg=reg, ledger=tmp_path / "dry_ledger.sqlite", tmp=tmp_path)


def screen(world, checks=BLOCKED_CHECKS):
    return R.record_screening(
        _report(checks), strategy_dir=world["sdir"], lineage_id="L", branch="b",
        framework_version="zl-fake-1", data_snapshot="2026-02-27", cost_model_version="L1-1",
        registry=world["reg"])


def make(world, cls=observe.ObservationExecutor, runner=None):
    runner = runner or FakeRunner()
    ledger = DryLedger(world["ledger"])
    e = cls(ledger=ledger, runner=runner, registry=world["reg"], strategies_root=world["root"],
            selfcheck=lambda: None, now=lambda: "2026-03-12T00:00:00+00:00")
    return e, ledger, runner


def start(e, start_date="2026-03-05"):
    return e.start_run("vgrsi_tx", start_date=start_date, capital_base=1_000_000.0,
                       benchmark=NoBenchmarkPack())


# --- observing 不能被 promote、不能通過 promoted 檢查 ---------------------------------
def test_observing_never_writes_registry_and_cannot_promote(world):
    screen(world)
    before = world["reg"].read_bytes()
    e, ledger, _ = make(world)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-05")
    e.backfill("vgrsi_tx", hist_start="2026-03-02")
    assert world["reg"].read_bytes() == before                      # 觀察全程沒動 registry
    assert not R.is_promoted("vgrsi_tx", R.strategy_sha256(world["sdir"]), world["reg"])
    with pytest.raises(R.PromotionError, match="blocked"):          # 門檻語意不變：blocked 仍不能晉升
        R.promote("vgrsi_tx", strategy_dir=world["sdir"], by="admin", note="觀察期績效不錯",
                  registry=world["reg"])
    assert world["reg"].read_bytes() == before


def test_promoted_executor_rejects_strategy_that_is_only_observing(world):
    screen(world)
    e, ledger, runner = make(world)
    start(e)
    pe, _, _ = make(world, cls=ex.DryRunExecutor, runner=runner)
    with pytest.raises(ex.NotPromoted):
        pe.start_run("vgrsi_tx", start_date="2026-03-05", capital_base=1.0, benchmark=NoBenchmarkPack())
    with pytest.raises(ex.DryRunError, match="尚未啟動"):           # promoted 路徑看不到 observing 的 run
        pe.run_day("vgrsi_tx", "2026-03-05")
    assert ledger.find_run("vgrsi_tx") is None                      # find_run 預設只看 promoted
    assert ledger.find_run("vgrsi_tx", tier="observing") is not None


def test_observing_refuses_promoted_and_eligible(world):
    rec = screen(world, [("sample", "ok", 1533.0, "n>=250 ok"),
                         ("pbo", "unavailable", None, "PBO<0.5")])       # 有檢查未完成 → conditional
    e, _, _ = make(world)
    # conditional 可以觀察
    start(e)
    # 改成 promoted：觀察層拒絕，且既有 observing run 暫停
    R._append(world["reg"], {"event": "promoted", "strategy_id": "vgrsi_tx", "lineage_id": "L",
                             "branch": "b", "strategy_sha256": rec["strategy_sha256"],
                             "framework_version": "zl-fake-1", "data_snapshot": "x",
                             "cost_model_version": "L1-1", "tags": [], "by": "admin", "note": "n",
                             "verdict": "conditional", "ts": "2026-03-06T00:00:00+00:00"})
    with pytest.raises(observe.NotObservable):
        e.run_day("vgrsi_tx", "2026-03-05")
    run = e.ledger.find_run("vgrsi_tx", tier="observing")
    assert e.ledger.is_paused(run["run_id"])
    with pytest.raises(observe.NotObservable, match="promoted"):
        start(make(world)[0])                                          # 新啟動也被拒


def test_observing_requires_screened_blocked_or_conditional(world):
    e, _, _ = make(world)
    with pytest.raises(observe.NotObservable, match="沒有記錄"):
        start(e)
    screen(world, [("sample", "ok", 1533.0, "n>=250 ok")])             # 只有 sample ok → eligible
    # eligible：該走晉升，不進觀察
    assert json.loads(world["reg"].read_text().splitlines()[-1])["report"]["verdict"] == "eligible"
    with pytest.raises(observe.NotObservable, match="eligible"):
        start(e)


def test_observing_out_of_scope(world):
    e, _, _ = make(world)
    with pytest.raises(ex.OutOfScope):
        e.start_run("xsmom_stkfut_rmt", start_date="2026-03-02", capital_base=1.0,
                    benchmark=NoBenchmarkPack())


def test_lifecycle_has_no_edge_from_observing_to_promoted_or_live():
    assert not (set(OBSERVATION_STATES) & set(LIFECYCLE_STATES) - {"paused", "retired"})
    for a, b in OBSERVATION_TRANSITIONS:
        assert a in OBSERVATION_STATES and b in OBSERVATION_STATES
        assert b not in ("promoted", "dryrun_active") and "live" not in (a, b)


# --- blocked 重篩不影響 ----------------------------------------------------------------
def test_rescreen_blocked_same_content_keeps_observing_and_refreshes_reason(world):
    screen(world)
    e, ledger, _ = make(world)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-05")
    n_daily = ledger._conn.execute("SELECT count(*) FROM dry_daily").fetchone()[0]
    screen(world, [("sample", "ok", 1533.0, "n>=250 ok"), ("dsr", "blocked", 0.9301, "DSR>0.95")])
    out = e.run_day("vgrsi_tx", "2026-03-06")                           # 同內容重篩仍 blocked → 繼續
    assert out.status == "OK" and not ledger.is_paused(ledger.find_run("vgrsi_tx", tier="observing")["run_id"])
    assert ledger._conn.execute("SELECT count(*) FROM dry_daily").fetchone()[0] == n_daily + 3
    assert not R.is_promoted("vgrsi_tx", R.strategy_sha256(world["sdir"]), world["reg"])
    snap = snapshot.build_snapshot(world["ledger"], world["root"], now=NOW, include_observing=True,
                                   registry=world["reg"])
    assert snap["runs"][0]["d1"]["reasons"] == ["DSR 0.930 < 0.95"]      # 失敗原因跟著最新重篩


def test_changed_strategy_content_pauses_observing(world):
    screen(world)
    e, ledger, _ = make(world)
    start(e)
    cfg = world["sdir"] / "config.yaml"
    cfg.write_bytes(cfg.read_bytes() + b"\n# tweak\n")
    with pytest.raises(ex.DryRunError):
        e.run_day("vgrsi_tx", "2026-03-05")
    assert ledger.is_paused(ledger.find_run("vgrsi_tx", tier="observing")["run_id"])


# --- 快照 tier ---------------------------------------------------------------------------
def test_d1_summary_reasons():
    rec = {"ts": "t", "report": _report(BLOCKED_CHECKS).to_dict()}
    s = d1_summary(rec)
    assert s["verdict"] == "blocked" and s["reasons"] == ["DSR 0.941 < 0.95"]
    assert s["pending"] == ["pbo"]                                      # 尚未完成的不算失敗原因


def test_reason_never_rounds_up_to_the_bound():
    from strategies._common.dryrun.d1view import _reason
    assert _reason({"name": "dsr", "value": 0.9496, "threshold": "DSR>0.95"}) == "DSR 0.949 < 0.95"
    assert _reason({"name": "dsr", "value": 0.94999, "threshold": "DSR>0.95"}) == "DSR 0.949 < 0.95"
    assert _reason({"name": "pbo", "value": 0.5004, "threshold": "PBO<0.5"}) == "PBO 0.500 ≥ 0.5"


def test_snapshot_tier_and_label(world):
    screen(world)
    e, ledger, _ = make(world)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-05")
    e.backfill("vgrsi_tx", hist_start="2026-03-02")
    snap = snapshot.build_snapshot(world["ledger"], world["root"], now=NOW, include_observing=True,
                                   registry=world["reg"])
    assert snap["schema"] == "strategy-live-snapshot/2"
    r = snap["runs"][0]
    assert r["tier"] == "observing" and r["label"] == LABEL == "未通過 D1／僅供觀察"
    assert r["d1"]["verdict"] == "blocked" and r["d1"]["reasons"] == ["DSR 0.941 < 0.95"]
    assert r["classification"] == ["open"]
    # 前向與歷史分開：equity 只有前向（起算日起），history 另有標示
    assert [p[0] for p in r["equity"]["base"]] == ["2026-03-05"]
    h = r["history"]
    assert h["basis"] == "in_sample_backfill" and h["label"] == "樣本內回補（非前向）"
    assert h["end"] < r["start_date"] and h["start"] == "2026-03-02"
    assert not {p[0] for p in h["equity"]["base"]} & {p[0] for p in r["equity"]["base"]}


def test_snapshot_default_is_v1_and_excludes_observing(world):
    """向後相容：不加旗標時，輸出與觀察層出現前的 /1 完全相同（gateway 嚴格驗證只收 /1）。"""
    screen(world)
    e, ledger, _ = make(world)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-05")
    snap = snapshot.build_snapshot(world["ledger"], world["root"], now=NOW)
    assert snap["schema"] == "strategy-live-snapshot/1" and snap["runs"] == []
    # promoted 的 run 在 /1 下欄位集合不變
    from tests.test_dryrun_ops import _ok, _run
    L = DryLedger(world["tmp"] / "p.sqlite")
    _ok(L, _run(L), "2026-10-29")
    L.close()
    r = snapshot.build_snapshot(world["tmp"] / "p.sqlite", world["root"], now=NOW)["runs"][0]
    assert set(r) == {"strategy_id", "lineage_id", "branch", "sha8", "classification", "start_date",
                      "status", "benchmark_status", "last_session", "trailing_missing", "days",
                      "equity", "cost_cum"}
    r2 = snapshot.build_snapshot(world["tmp"] / "p.sqlite", world["root"], now=NOW,
                                 include_observing=True)["runs"][0]
    assert r2["tier"] == "promoted" and r2["label"] is None and r2["d1"] is None and r2["history"] is None
    assert {k: v for k, v in r2.items() if k not in ("tier", "label", "d1", "history")} == r  # /2 是 /1 的超集


def test_v2_snapshot_fixture_matches_generator():
    """契約範例必須就是產生器現在的輸出（genesis-deck gateway 測試會複製同一份）。"""
    fx = Path(__file__).parent / "fixtures" / "strategy-live-snapshot-v2.example.json"
    assert json.loads(fx.read_text(encoding="utf-8")) == _v2_example()


def _v2_example():
    import tempfile
    from tests.test_dryrun_ops import _ok, _run
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        L = DryLedger(td / "dry_ledger.sqlite")
        rid = _run(L, "vgrsi_tx")
        _ok(L, rid, "2026-10-28")
        _ok(L, rid, "2026-10-29")
        orid = L.start_run(
            strategy_id="tsmom_tx_mtx", lineage_id="L", branch="b", strategy_sha256="o" * 8,
            framework_version="f", cost_model_version="L1-1", cost_params={},
            promoted_data_snapshot="x", start_date="2026-10-26", capital_base=5e6,
            benchmark_status="D2_UNAVAILABLE", benchmark_pack="", ts="t", tier="observing",
            d1={"verdict": "blocked", "reasons": ["DSR 0.92 < 0.95"], "pending": ["pbo", "spa"],
                "screened_at": "2026-10-30T08:04:36+00:00", "rules_version": "1"})
        _ok(L, orid, "2026-10-29")
        L.append_history(orid, {sc: [("2026-10-22", 5e6), ("2026-10-23", 5.01e6)]
                                for sc in ("optimistic", "base", "pessimistic")},
                         cost_model_version="L1-1", data_snapshot="2026-10-29", ts="t")
        L.close()
        for sid in ("vgrsi_tx", "tsmom_tx_mtx"):
            d = td / "s" / sid
            d.mkdir(parents=True)
            (d / "manifest.yaml").write_text(f"id: {sid}\ntags:\n- paper\n", encoding="utf-8")
        return snapshot.build_snapshot(td / "dry_ledger.sqlite", td / "s", now=NOW, include_observing=True)


# --- 歷史回補 ---------------------------------------------------------------------------
def test_backfill_is_separate_append_only_and_once(world):
    screen(world)
    e, ledger, runner = make(world)
    run_id = start(e, "2026-03-06")
    n = e.backfill("vgrsi_tx", hist_start="2026-03-02")
    assert n == 3 * 4                                                  # 3 情境 × 03-02..03-05
    assert e.backfill("vgrsi_tx", hist_start="2026-03-02") == 0        # 重跑不改帳本
    assert ledger._conn.execute("SELECT count(*) FROM dry_daily").fetchone()[0] == 0   # 不進前向表
    rows = ledger._conn.execute("SELECT DISTINCT basis, mode FROM dry_history").fetchall()
    assert [tuple(r) for r in rows] == [("IN_SAMPLE_BACKFILL", "DRY")]
    with pytest.raises(sqlite3.IntegrityError):
        ledger._conn.execute("UPDATE dry_history SET equity=0")
    with pytest.raises(sqlite3.IntegrityError):
        ledger._conn.execute("DELETE FROM dry_history")
    # 回補只用 L1 三情境成本（同一個模擬器路徑）
    assert {c[0] for c in runner.calls} == {"optimistic", "base", "pessimistic"}


def test_backfill_needs_observing_run_and_prior_sessions(world):
    screen(world)
    e, _, _ = make(world)
    with pytest.raises(ex.DryRunError, match="尚未啟動"):
        e.backfill("vgrsi_tx", hist_start="2026-03-02")
    start(e, "2026-03-03")
    with pytest.raises(ex.DryRunError, match="沒有足夠"):
        e.backfill("vgrsi_tx", hist_start="2026-03-02")


# --- 帳本遷移與隔離 -------------------------------------------------------------------------
def test_legacy_ledger_gets_tier_column_default_promoted(tmp_path):
    p = tmp_path / "dry_ledger.sqlite"
    c = sqlite3.connect(p)
    c.executescript("""CREATE TABLE dry_runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT,
      mode TEXT NOT NULL DEFAULT 'DRY' CHECK (mode='DRY'), strategy_id TEXT NOT NULL, lineage_id TEXT,
      branch TEXT, strategy_sha256 TEXT NOT NULL, framework_version TEXT, cost_model_version TEXT,
      cost_params_json TEXT, promoted_data_snapshot TEXT, start_date TEXT NOT NULL, capital_base REAL,
      benchmark_status TEXT NOT NULL, benchmark_pack TEXT, created_ts TEXT NOT NULL,
      UNIQUE (strategy_id, strategy_sha256));
      INSERT INTO dry_runs(strategy_id,strategy_sha256,start_date,benchmark_status,created_ts)
      VALUES('vgrsi_tx','abc','2026-10-26','D2_UNAVAILABLE','t');""")
    c.commit()
    c.close()
    L = DryLedger(p)
    assert L.find_run("vgrsi_tx")["tier"] == "promoted"
    assert L.find_run("vgrsi_tx", tier="observing") is None
    with pytest.raises(sqlite3.IntegrityError):
        L._conn.execute("UPDATE dry_runs SET tier='observing'")        # append-only trigger 仍在
    with pytest.raises(sqlite3.IntegrityError, match="另一個 tier|unique|UNIQUE"):
        L.start_run(strategy_id="vgrsi_tx", lineage_id=None, branch=None, strategy_sha256="abc",
                    framework_version="f", cost_model_version="c", cost_params={},
                    promoted_data_snapshot="x", start_date="2026-11-02", capital_base=1.0,
                    benchmark_status="D2_UNAVAILABLE", benchmark_pack="", ts="t", tier="observing")
    # 舊帳本快照照常產生（沒有 tier／dry_history 欄表也不爆）
    s = snapshot.build_snapshot(p, tmp_path, now=NOW, include_observing=True)
    assert s["runs"][0]["tier"] == "promoted"


def test_tier_check_constraint(tmp_path):
    L = DryLedger(tmp_path / "dry_ledger.sqlite")
    with pytest.raises(ValueError):
        L.find_run("x", tier="live")
    with pytest.raises(sqlite3.IntegrityError):
        L._conn.execute("INSERT INTO dry_runs(strategy_id,strategy_sha256,start_date,benchmark_status,"
                        "created_ts,tier) VALUES('a','b','c','d','e','live')")


def test_snapshot_v3_adds_regime_slices_only_when_labels_given(world):
    screen(world)
    e, ledger, _ = make(world)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-05")
    e.backfill("vgrsi_tx", hist_start="2026-03-02")
    base = snapshot.build_snapshot(world["ledger"], world["root"], now=NOW, include_observing=True, registry=world["reg"])
    assert base["schema"] == "strategy-live-snapshot/2" and "regime_slices" not in base["runs"][0]["history"]
    hist_days = [p[0] for p in base["runs"][0]["history"]["equity"]["base"]]
    labels = {d: {"trend": "bull", "vol": "mid"} for d in hist_days}
    snap = snapshot.build_snapshot(world["ledger"], world["root"], now=NOW, include_observing=True,
                                   registry=world["reg"], regime_labels=labels)
    assert snap["schema"] == "strategy-live-snapshot/3"
    rs = snap["runs"][0]["history"]["regime_slices"]
    assert rs["rules_version"] == "regime-1" and rs["basis"] == "in_sample_backfill"
    assert set(rs["dims"]) == {"trend", "vol"}
    assert rs["monthly"] and all(x["trend"] == "bull" and x["vol"] == "mid" for x in rs["monthly"][1:])
    assert snap["runs"][0]["history"]["equity"] == base["runs"][0]["history"]["equity"]   # 原有欄位不變


def test_snapshot_regime_labels_require_observing(world):
    import pytest
    with pytest.raises(ValueError):
        snapshot.build_snapshot(world["ledger"], world["root"], now=NOW, regime_labels={"2026-03-02": {}})
