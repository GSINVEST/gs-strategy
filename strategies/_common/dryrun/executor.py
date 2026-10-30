"""dry run 執行器（D3，影子模式）：每交易日收盤後一次，只算、只寫 DRY 帳本。

狀態重建：同一份凍結的 strategy.py 每天從「啟動日」起重放到當日（模擬器沒有可序列化
的中間狀態），但帳本**只追加當日**那一列；啟動日之前沒有任何 dry run 績效。重放時已記錄
日期的權益若與帳本不符（資料被改寫、程式或框架有變）→ 暫停並不追加（ReplayMismatch）。

缺漏：沒跑的交易日、資料快照落後的交易日一律標 MISSING，**不補算**；連續缺 N 日自動暫停。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

import yaml

from ..promotion.registry import REGISTRY, latest, strategy_sha256
from . import guards
from .benchmark import BenchmarkPackProvider
from .costs import COST_MODEL_VERSION, SCENARIOS, L1Params, scenario_params
from .ledger import SCENARIO_ORDER, DryLedger
from .types import SimResult

# Q1：v1 只含台指期日頻。個股期貨（xsmom_stkfut_rmt）不納入。
ALLOWED_STRATEGIES = frozenset({"vgrsi_tx", "tsmom_tx_mtx", "cubic_momentum_tx"})
EQUITY_TOL = 0.01          # NTD；重放對帳容差。同程式同資料應完全相等。
DEFAULT_MAX_CONSECUTIVE_MISSING = 3     # §9.3 的 N，D3 定為 3


class DryRunError(RuntimeError):
    pass


class NotPromoted(DryRunError): ...
class OutOfScope(DryRunError): ...
class FrozenHashMismatch(DryRunError): ...
class FrameworkMismatch(DryRunError): ...
class ReplayMismatch(DryRunError): ...
class Paused(DryRunError): ...


class SimRunner(Protocol):
    def framework_version(self) -> str: ...
    def latest_snapshot(self) -> str: ...
    def sessions(self, start: str, end: str) -> list: ...
    def simulate(self, strategy_dir: Path, *, start: str, end: str, capital_base: float,
                 params: L1Params, scenario: str) -> SimResult: ...


@dataclass(frozen=True)
class DayOutcome:
    status: str            # OK / MISSING / ALREADY_RECORDED
    session: str
    detail: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DryRunExecutor:
    def __init__(self, *, ledger: DryLedger, runner: SimRunner,
                 registry: Path = REGISTRY, strategies_root: Optional[Path] = None,
                 selfcheck: Optional[Callable[[], None]] = None,
                 now: Callable[[], str] = _utc_now, base_spread: float = 6.0,
                 max_consecutive_missing: int = DEFAULT_MAX_CONSECUTIVE_MISSING):
        self.ledger, self.runner = ledger, runner
        self.registry = Path(registry)
        self.strategies_root = Path(strategies_root) if strategies_root else \
            Path(__file__).resolve().parents[2]
        self.selfcheck = selfcheck or guards.default_selfcheck(ledger.path.parent, ledger.path)
        self.now = now
        self.params = {s: scenario_params(s, base_spread) for s in SCENARIOS}
        self.max_missing = max_consecutive_missing

    # --- 共用檢查 --------------------------------------------------------
    def _scope(self, strategy_id: str) -> Path:
        if strategy_id not in ALLOWED_STRATEGIES:
            raise OutOfScope(f"{strategy_id}: D3 v1 只含 {sorted(ALLOWED_STRATEGIES)}（個股期貨不納入）")
        return self.strategies_root / strategy_id

    def _pause(self, run_id: int, reason: str) -> None:
        self.ledger.append_event(run_id, self.now(), "PAUSED", reason)

    # --- 啟動 -------------------------------------------------------------
    def start_run(self, strategy_id: str, *, start_date: str, capital_base: Optional[float],
                  benchmark: BenchmarkPackProvider) -> int:
        self.selfcheck()
        sdir = self._scope(strategy_id)
        promo = latest(strategy_id, self.registry)
        if promo is None or promo.get("event") != "promoted":
            raise NotPromoted(f"{strategy_id}: registry 最新記錄不是 promoted，不能啟動 dry run")
        sha = strategy_sha256(sdir)
        if sha != promo["strategy_sha256"]:
            raise FrozenHashMismatch(f"{strategy_id}: 目前內容與晉升時的雜湊不符，拒絕啟動")
        fw = self.runner.framework_version()
        if fw != promo["framework_version"]:
            raise FrameworkMismatch(f"{strategy_id}: 框架版本 {fw!r} ≠ 晉升時 {promo['framework_version']!r}")
        if capital_base is None:
            capital_base = float(yaml.safe_load((sdir / "config.yaml").read_text(encoding="utf-8"))
                                 .get("capital_base", 10_000_000))
        pack = benchmark.get_pack(framework_version=fw, data_snapshot=promo["data_snapshot"],
                                  cost_model_version=COST_MODEL_VERSION)
        return self.ledger.start_run(
            strategy_id=strategy_id, lineage_id=promo.get("lineage_id"), branch=promo.get("branch"),
            strategy_sha256=sha, framework_version=fw, cost_model_version=COST_MODEL_VERSION,
            cost_params={s: self.params[s].to_dict() for s in SCENARIOS},
            promoted_data_snapshot=promo["data_snapshot"], start_date=start_date,
            capital_base=float(capital_base),
            benchmark_status="ATTACHED" if pack else "D2_UNAVAILABLE", benchmark_pack=pack,
            ts=self.now())

    def resume(self, strategy_id: str, *, by: str, note: str) -> None:
        if not by.strip():
            raise DryRunError("resume 需要具名的人")
        run = self.ledger.find_run(strategy_id)
        if run is None:
            raise DryRunError(f"{strategy_id}: 沒有 dry run")
        self.ledger.append_event(run["run_id"], self.now(), "RESUMED", f"by={by}; {note}")

    # --- 每日 -------------------------------------------------------------
    def run_day(self, strategy_id: str, session: str) -> DayOutcome:
        self.selfcheck()
        sdir = self._scope(strategy_id)
        run = self.ledger.find_run(strategy_id)
        if run is None:
            raise DryRunError(f"{strategy_id}: 尚未啟動 dry run（先 start_run）")
        run_id, sha = run["run_id"], run["strategy_sha256"]
        if self.ledger.is_paused(run_id):
            raise Paused(f"{strategy_id}: 已暫停，須人工 resume")

        if strategy_sha256(sdir) != sha:
            self._pause(run_id, "FROZEN_HASH_MISMATCH")
            raise FrozenHashMismatch(f"{strategy_id}: 策略內容與凍結雜湊不符，拒跑並暫停")
        fw = self.runner.framework_version()
        if fw != run["framework_version"]:
            self._pause(run_id, f"FRAMEWORK_MISMATCH {fw!r}")
            raise FrameworkMismatch(f"{strategy_id}: 框架版本 {fw!r} ≠ 凍結的 {run['framework_version']!r}")

        start = run["start_date"]
        if session < start:
            raise DryRunError(f"{session} 早於啟動日 {start}：啟動日之前不屬於 dry run")
        sessions = self.runner.sessions(start, session)
        if not sessions or sessions[-1] != session:
            raise DryRunError(f"{session} 不是交易日")
        done = self.ledger.recorded_sessions(run_id)
        if session in done:
            return DayOutcome("ALREADY_RECORDED", session)

        snapshot = self.runner.latest_snapshot()
        for s in (x for x in sessions[:-1] if x not in done):
            self.ledger.append_missing(run_id, s, data_snapshot=snapshot, strategy_sha256=sha,
                                       reason="NOT_RUN", ts=self.now())
        self._pause_if_gap(run_id)

        if snapshot < session:
            self.ledger.append_missing(run_id, session, data_snapshot=snapshot, strategy_sha256=sha,
                                       reason=f"SNAPSHOT_STALE: bundle 最新 {snapshot} < {session}",
                                       ts=self.now())
            self._pause_if_gap(run_id)
            return DayOutcome("MISSING", session, "SNAPSHOT_STALE")

        sims = {sc: self.runner.simulate(sdir, start=start, end=session,
                                         capital_base=run["capital_base"],
                                         params=self.params[sc], scenario=sc)
                for sc in SCENARIO_ORDER}
        self._reconcile(run_id, sims)

        per, fills_out = {}, []
        for sc, sim in sims.items():
            idx = {d.session: i for i, d in enumerate(sim.daily)}
            if session not in idx:
                raise DryRunError(f"{sc}: 模擬結果缺 {session}")
            i = idx[session]
            today = sim.daily[i]
            prev = sim.daily[i - 1].equity if i > 0 else run["capital_base"]
            upto = [f for f in sim.fills if f.order.session <= session]
            per[sc] = dict(
                equity=today.equity, daily_pnl=today.equity - prev,
                position_json=json.dumps(today.position, sort_keys=True),
                cum_spread=sum(f.spread_points * f.filled_qty * f.multiplier for f in upto),
                cum_impact=sum(f.impact_points * f.filled_qty * f.multiplier for f in upto),
                cum_commission=sum(f.commission_ntd for f in upto), flags=[])
            for f in sim.fills:
                if f.order.session == session:
                    fills_out.append(dict(
                        scenario=sc, symbol=f.order.symbol, root_symbol=f.root_symbol,
                        side=f.order.side, requested_qty=f.order.qty, filled_qty=f.filled_qty,
                        ref_price=f.ref_price, fill_price=f.fill_price, multiplier=f.multiplier,
                        spread_pts=f.spread_points, impact_pts=f.impact_points,
                        spread_ntd=f.spread_points * f.filled_qty * f.multiplier,
                        impact_ntd=f.impact_points * f.filled_qty * f.multiplier,
                        commission_ntd=f.commission_ntd, reason=f.reason, flags=list(f.flags)))
        if not (per["pessimistic"]["equity"] <= per["base"]["equity"] <= per["optimistic"]["equity"]):
            for sc in per:
                per[sc]["flags"].append("SCENARIO_NON_MONOTONIC")
        if self.ledger.has_missing(run_id):
            for sc in per:
                per[sc]["flags"].append("AFTER_GAP")
        self.ledger.append_day(run_id, session, per, fills_out, data_snapshot=snapshot,
                               strategy_sha256=sha, ts=self.now())
        return DayOutcome("OK", session)

    # --- 內部 ---------------------------------------------------------------
    def _pause_if_gap(self, run_id: int) -> None:
        n = self.ledger.trailing_missing(run_id)
        if n >= self.max_missing:
            self._pause(run_id, f"CONSECUTIVE_MISSING {n}")
            raise Paused(f"連續 {n} 個交易日缺漏，已暫停")

    def _reconcile(self, run_id: int, sims: dict) -> None:
        for sc, sim in sims.items():
            recorded = self.ledger.ok_equity(run_id, sc)
            for d in sim.daily:
                old = recorded.get(d.session)
                if old is not None and abs(old - d.equity) > EQUITY_TOL:
                    self._pause(run_id, f"REPLAY_MISMATCH {sc} {d.session}: {old} vs {d.equity}")
                    raise ReplayMismatch(
                        f"{sc} {d.session}: 重放權益 {d.equity} ≠ 帳本 {old}；未追加任何列")
