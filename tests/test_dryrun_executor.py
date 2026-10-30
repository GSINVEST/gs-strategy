"""D3 執行器：凍結、單向追加帳本、缺漏處理、決定性（規格 §6.4、§9.3、§15）。

用假的 SimRunner，不需 zipline／bundle。
"""
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from strategies._common.dryrun import executor as ex
from strategies._common.dryrun.benchmark import NoBenchmarkPack
from strategies._common.dryrun.ledger import DryLedger
from strategies._common.dryrun.types import (
    DailyRow, SimResult, SimulatedFill, SimulatedOrder,
)
from strategies._common.promotion.registry import _append, strategy_sha256

REPO = Path(__file__).resolve().parents[1]
SESSIONS = [f"2026-03-{d:02d}" for d in (2, 3, 4, 5, 6, 9, 10, 11)]
PENALTY = {"optimistic": 1.0, "base": 3.0, "pessimistic": 6.0}


class FakeRunner:
    def __init__(self, snapshot="2026-03-11", fw="zl-fake-1", history_shift=0.0):
        self.snapshot, self.fw, self.history_shift = snapshot, fw, history_shift
        self.calls = []

    def framework_version(self):
        return self.fw

    def latest_snapshot(self):
        return self.snapshot

    def sessions(self, start, end):
        return [s for s in SESSIONS if start <= s <= end]

    def simulate(self, strategy_dir, *, start, end, capital_base, params, scenario):
        self.calls.append((scenario, start, end))
        days = self.sessions(start, end)
        daily, fills = [], []
        for i, s in enumerate(days):
            eq = capital_base + 100.0 * i - PENALTY[scenario] * (i + 1) + self.history_shift * (i == 0)
            daily.append(DailyRow(session=s, equity=eq, position={"TX201903": 1}))
            fills.append(SimulatedFill(
                order=SimulatedOrder(strategy_id="vgrsi_tx", session=s, symbol="TX201903", side=1, qty=1),
                root_symbol="TX", multiplier=200.0, filled_qty=1, ref_price=20000.0,
                fill_price=20000.0 + params.spread_points / 2, spread_points=params.spread_points / 2,
                impact_points=0.0, commission_ntd=200.0 * params.commission_mult, reason=None, flags=()))
        return SimResult(daily=daily, fills=fills)


@pytest.fixture
def world(tmp_path):
    strat_root = tmp_path / "strategies"
    sdir = strat_root / "vgrsi_tx"
    sdir.mkdir(parents=True)
    shutil.copy(REPO / "strategies/vgrsi_tx/strategy.py", sdir / "strategy.py")
    shutil.copy(REPO / "strategies/vgrsi_tx/config.yaml", sdir / "config.yaml")
    reg = tmp_path / "promotions.jsonl"
    sha = strategy_sha256(sdir)
    _append(reg, {"event": "promoted", "strategy_id": "vgrsi_tx", "lineage_id": "L", "branch": "b",
                  "strategy_sha256": sha, "framework_version": "zl-fake-1",
                  "data_snapshot": "2026-02-27", "cost_model_version": "L1-1", "tags": [],
                  "by": "admin", "note": "", "verdict": "ok", "ts": "2026-02-28T00:00:00+00:00"})
    run_dir = tmp_path / "dryrun"
    run_dir.mkdir()
    return dict(strat_root=strat_root, sdir=sdir, reg=reg, sha=sha, run_dir=run_dir,
                ledger_path=run_dir / "dry_ledger.sqlite", tmp=tmp_path)


def make(world, runner=None, **kw):
    runner = runner or FakeRunner()
    ledger = DryLedger(world["ledger_path"])
    e = ex.DryRunExecutor(ledger=ledger, runner=runner, registry=world["reg"],
                          strategies_root=world["strat_root"], selfcheck=lambda: None,
                          now=lambda: "2026-03-12T00:00:00+00:00", **kw)
    return e, ledger, runner


def start(e, start_date="2026-03-02"):
    return e.start_run("vgrsi_tx", start_date=start_date, capital_base=1_000_000.0,
                       benchmark=NoBenchmarkPack())


def rows(ledger, table):
    return [dict(r) for r in ledger._conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


# --- 啟動條件 ---------------------------------------------------------------
def test_start_requires_promotion(world):
    world["reg"].write_text("")
    e, _, _ = make(world)
    with pytest.raises(ex.NotPromoted):
        start(e)


def test_stock_futures_strategy_out_of_scope(world):
    e, _, _ = make(world)
    with pytest.raises(ex.OutOfScope):
        e.start_run("xsmom_stkfut_rmt", start_date="2026-03-02", capital_base=1.0,
                    benchmark=NoBenchmarkPack())


def test_start_freezes_hash_and_marks_d2_stub(world):
    e, ledger, _ = make(world)
    run_id = start(e)
    run = rows(ledger, "dry_runs")[0]
    assert run["strategy_sha256"] == world["sha"] and run["mode"] == "DRY"
    assert run["benchmark_status"] == "D2_UNAVAILABLE" and run["benchmark_pack"] is None
    assert run["cost_model_version"] == "L1-1" and run["framework_version"] == "zl-fake-1"
    assert json.loads(run["cost_params_json"])["optimistic"]["spread_points"] == 2.0
    assert start(e) == run_id            # 同一凍結版本重複啟動不會產生第二個 run


# --- 凍結：雜湊不符拒跑 -------------------------------------------------------
def test_hash_mismatch_refuses_and_pauses(world):
    e, ledger, runner = make(world)
    start(e)
    (world["sdir"] / "strategy.py").write_text("# edited\n", encoding="utf-8")
    with pytest.raises(ex.FrozenHashMismatch):
        e.run_day("vgrsi_tx", "2026-03-02")
    assert runner.calls == []                                   # 沒有跑
    assert rows(ledger, "dry_daily") == []                      # 沒有寫績效
    assert rows(ledger, "dry_events")[-1]["kind"] == "PAUSED"
    with pytest.raises(ex.Paused):                              # 還原內容也不會自動恢復
        (world["sdir"] / "strategy.py").write_bytes(
            (REPO / "strategies/vgrsi_tx/strategy.py").read_bytes())
        e.run_day("vgrsi_tx", "2026-03-02")


def test_config_change_also_counts_as_changed(world):
    e, _, _ = make(world)
    start(e)
    p = world["sdir"] / "config.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "\n# tweak\n", encoding="utf-8")
    with pytest.raises(ex.FrozenHashMismatch):
        e.run_day("vgrsi_tx", "2026-03-02")


def test_framework_version_mismatch_refuses(world):
    e, ledger, _ = make(world, FakeRunner(fw="zl-fake-2"))
    with pytest.raises(ex.FrameworkMismatch):       # 啟動時就對不上晉升記錄
        start(e)
    e.runner = FakeRunner()
    start(e)
    e.runner = FakeRunner(fw="zl-fake-2")           # 之後升級了框架：拒跑並暫停
    with pytest.raises(ex.FrameworkMismatch):
        e.run_day("vgrsi_tx", "2026-03-02")
    assert rows(ledger, "dry_events")[-1]["kind"] == "PAUSED"


# --- 正常追加 -----------------------------------------------------------------
def test_run_day_appends_three_scenarios_with_provenance(world):
    e, ledger, _ = make(world)
    start(e)
    out = e.run_day("vgrsi_tx", "2026-03-02")
    assert out.status == "OK"
    d = rows(ledger, "dry_daily")
    assert [r["scenario"] for r in d] == ["optimistic", "base", "pessimistic"]
    assert {r["data_snapshot"] for r in d} == {"2026-03-11"}
    assert {r["strategy_sha256"] for r in d} == {world["sha"]}
    assert {r["mode"] for r in d} == {"DRY"}
    f = rows(ledger, "dry_fills")
    assert len(f) == 3 and {x["session"] for x in f} == {"2026-03-02"}
    base = next(x for x in f if x["scenario"] == "base")
    assert base["spread_cost_ntd"] == pytest.approx(3.0 * 1 * 200.0)   # 每邊 3 點


def test_scenarios_monotonic_and_flag_when_violated(world):
    e, ledger, _ = make(world)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-02")
    eq = {r["scenario"]: r["equity"] for r in rows(ledger, "dry_daily")}
    assert eq["pessimistic"] <= eq["base"] <= eq["optimistic"]
    assert not any(r["flags"] for r in rows(ledger, "dry_daily"))

    class Bad(FakeRunner):
        def simulate(self, *a, scenario, **k):
            r = super().simulate(*a, scenario=scenario, **k)
            if scenario == "pessimistic":
                r.daily[-1] = DailyRow(session=r.daily[-1].session, equity=9e9, position={})
            return r
    w2 = dict(world, ledger_path=world["run_dir"] / "dry_ledger2.sqlite")
    e2, l2, _ = make(w2, Bad())
    start(e2)
    e2.run_day("vgrsi_tx", "2026-03-02")
    assert all("SCENARIO_NON_MONOTONIC" in r["flags"] for r in rows(l2, "dry_daily"))


def test_second_day_replays_from_start_but_appends_only_new_day(world):
    e, ledger, runner = make(world)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-02")
    e.run_day("vgrsi_tx", "2026-03-03")
    assert {c[1] for c in runner.calls} == {"2026-03-02"}          # 狀態重建永遠從啟動日起
    d = rows(ledger, "dry_daily")
    assert [(r["session"], r["scenario"]) for r in d if r["scenario"] == "base"] == [
        ("2026-03-02", "base"), ("2026-03-03", "base")]
    assert len(rows(ledger, "dry_fills")) == 6                   # 第二天不重複寫第一天的成交


def test_rerun_same_session_is_noop(world):
    e, ledger, _ = make(world)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-02")
    before = rows(ledger, "dry_daily")
    assert e.run_day("vgrsi_tx", "2026-03-02").status == "ALREADY_RECORDED"
    assert rows(ledger, "dry_daily") == before


def test_session_before_start_or_not_a_session_refused(world):
    e, _, _ = make(world)
    start(e, "2026-03-04")
    with pytest.raises(ex.DryRunError):
        e.run_day("vgrsi_tx", "2026-03-03")
    with pytest.raises(ex.DryRunError):
        e.run_day("vgrsi_tx", "2026-03-07")                      # 週六


# --- 缺漏：標記，不補算 ---------------------------------------------------------
def test_stale_snapshot_is_marked_missing_not_computed(world):
    e, ledger, runner = make(world, FakeRunner(snapshot="2026-03-02"))
    start(e)
    out = e.run_day("vgrsi_tx", "2026-03-03")
    assert out.status == "MISSING"
    d = rows(ledger, "dry_daily")
    assert {r["status"] for r in d} >= {"MISSING"}
    assert all(r["equity"] is None for r in d if r["session"] == "2026-03-03")
    assert "SNAPSHOT_STALE" in d[-1]["reason"]
    assert runner.calls == []                                    # 沒有用舊資料硬算
    assert rows(ledger, "dry_fills") == []


def test_skipped_days_are_marked_missing_not_backfilled(world):
    e, ledger, _ = make(world)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-02")
    e.run_day("vgrsi_tx", "2026-03-05")              # 03-03、03-04 沒跑
    base = [r for r in rows(ledger, "dry_daily") if r["scenario"] == "base"]
    assert [(r["session"], r["status"]) for r in base] == [
        ("2026-03-02", "OK"), ("2026-03-03", "MISSING"), ("2026-03-04", "MISSING"),
        ("2026-03-05", "OK")]
    assert base[-1]["flags"] and "AFTER_GAP" in base[-1]["flags"]
    assert not [f for f in rows(ledger, "dry_fills") if f["session"] in ("2026-03-03", "2026-03-04")]


def test_three_consecutive_missing_pauses(world):
    e, ledger, _ = make(world, max_consecutive_missing=3)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-02")
    with pytest.raises(ex.Paused):
        e.run_day("vgrsi_tx", "2026-03-06")           # 缺 03-03、03-04、03-05
    assert rows(ledger, "dry_events")[-1]["kind"] == "PAUSED"
    assert rows(ledger, "dry_daily")                  # 帳本保留
    with pytest.raises(ex.Paused):
        e.run_day("vgrsi_tx", "2026-03-09")
    e.resume("vgrsi_tx", by="admin", note="data restored")
    assert rows(ledger, "dry_events")[-1]["kind"] == "RESUMED"


# --- 重放對帳：資料被改寫時不得悄悄蓋掉已記錄的績效 ---------------------------------
def test_replay_mismatch_pauses_and_appends_nothing(world):
    e, ledger, _ = make(world)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-02")
    n = len(rows(ledger, "dry_daily"))
    e.runner = FakeRunner(history_shift=777.0)         # 同日重算出不同權益
    with pytest.raises(ex.ReplayMismatch):
        e.run_day("vgrsi_tx", "2026-03-03")
    assert len(rows(ledger, "dry_daily")) == n
    assert rows(ledger, "dry_events")[-1]["kind"] == "PAUSED"


# --- 決定性 ---------------------------------------------------------------------
def test_same_inputs_give_bitwise_identical_ledger(world, tmp_path):
    def run(path):
        w = dict(world, ledger_path=path)
        e, ledger, _ = make(w)
        start(e)
        for s in ("2026-03-02", "2026-03-03", "2026-03-04"):
            e.run_day("vgrsi_tx", s)
        return [rows(ledger, t) for t in ("dry_runs", "dry_daily", "dry_fills", "dry_events")]
    a = run(world["run_dir"] / "dry_a.sqlite")
    b = run(world["run_dir"] / "dry_b.sqlite")
    assert a == b


# --- 帳本單向追加 ----------------------------------------------------------------
@pytest.mark.parametrize("table", ["dry_runs", "dry_daily", "dry_fills", "dry_events"])
def test_ledger_is_append_only(world, table):
    e, ledger, _ = make(world)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-02")
    ledger.append_event(1, "t", "NOTE", "x")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        ledger._conn.execute(f"DELETE FROM {table}")
    col = {"dry_runs": "capital_base", "dry_daily": "equity", "dry_fills": "filled_qty",
           "dry_events": "detail"}[table]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        ledger._conn.execute(f"UPDATE {table} SET {col} = {col}")


def test_ledger_rejects_duplicate_session_scenario(world):
    e, ledger, _ = make(world)
    start(e)
    e.run_day("vgrsi_tx", "2026-03-02")
    with pytest.raises(sqlite3.IntegrityError):
        ledger._conn.execute(
            "INSERT INTO dry_daily(run_id,session,scenario,mode,status,data_snapshot,strategy_sha256,ts)"
            " VALUES(1,'2026-03-02','base','DRY','OK','x','y','t')")


# --- 啟動自檢會被呼叫 --------------------------------------------------------------
def test_selfcheck_failure_blocks_everything(world):
    from strategies._common.dryrun.guards import SelfCheckError

    def boom():
        raise SelfCheckError("credential found")
    e, ledger, runner = make(world)
    e.selfcheck = boom
    with pytest.raises(SelfCheckError):
        start(e)
    assert rows(ledger, "dry_runs") == [] and runner.calls == []
