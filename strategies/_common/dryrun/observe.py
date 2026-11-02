"""觀察層（observation tier）：給「D1 沒過」的策略一條**前向紙上模擬**，供桌面版面板觀察。

與 promoted 完全分離（設計見 docs/spec/dryrun-observation-tier.md）：

- 只給 registry **最新事件**為 ``screened`` 且判定 ``blocked``／``conditional`` 的策略；已晉升、或
  eligible 的策略一律拒絕。
- **絕不寫 registry**：本模組只呼叫唯讀的 ``latest``，沒有 ``promote``／``record_screening``。
  觀察不是晉升的前置步驟，也不構成晉升的證據。
- 沿用同一個模擬器、L1 成本、同一份 DRY 帳本；帳本列以 ``tier='observing'`` 區分，``find_run`` 預設只看
  promoted，所以 ``DryRunExecutor`` 永遠看不到 observing 的 run。
- 隔離規則照舊：``selfcheck``（憑證、import、出口、唯讀、命名）在每個入口都跑。
"""
from __future__ import annotations

from typing import Optional

from ..promotion.registry import latest, strategy_sha256
from .d1view import OBSERVABLE_VERDICTS, d1_summary
from .costs import COST_MODEL_VERSION
from .executor import DryRunError, DryRunExecutor, FrameworkMismatch, FrozenHashMismatch

TIER = "observing"


class NotObservable(DryRunError): ...


def _observable(rec: Optional[dict]) -> bool:
    return bool(rec and rec.get("event") == "screened"
                and (rec.get("report") or {}).get("verdict") in OBSERVABLE_VERDICTS)


class ObservationExecutor(DryRunExecutor):
    TIER = TIER
    # 觀察不要求框架版本等於篩選時的記錄；以啟動當下實際安裝的版本凍結，之後每日比對（不放寬）。
    REQUIRE_REGISTRY_FRAMEWORK = False

    def _start_basis(self, strategy_id: str) -> dict:
        rec = latest(strategy_id, self.registry)
        if rec is None:
            raise NotObservable(f"{strategy_id}: registry 沒有記錄；必須先跑 D1 篩選（screened）")
        if rec.get("event") == "promoted":
            raise NotObservable(f"{strategy_id}: 最新記錄是 promoted，請走 promoted 路徑，不進觀察層")
        if not _observable(rec):
            raise NotObservable(
                f"{strategy_id}: 最新篩選判定 {(rec.get('report') or {}).get('verdict')!r}，"
                f"觀察層只收 {'／'.join(OBSERVABLE_VERDICTS)}")
        return rec

    def _still_eligible(self, strategy_id: str, sha: str) -> Optional[str]:
        rec = latest(strategy_id, self.registry)
        if not _observable(rec):
            return "NO_LONGER_OBSERVING: 最新記錄已不是 blocked／conditional 的 screened"
        if rec["strategy_sha256"] != sha:
            return "NO_LONGER_OBSERVING: 策略內容已改版，請以新版本重新啟動觀察"
        return None

    def _ineligible_error(self, strategy_id: str) -> Exception:
        return NotObservable(f"{strategy_id}: 已不符合觀察資格，暫停")

    def _run_extras(self, basis: dict) -> dict:
        return {"d1": d1_summary(basis)}

    # --- 歷史回補（樣本內，非前向）---------------------------------------------
    def backfill(self, strategy_id: str, *, hist_start: str) -> int:
        """以 L1 成本三情境重跑 [hist_start, 觀察起算日前一交易日]，寫入 ``dry_history``。

        這是樣本內重跑：策略參數與 D1 的選擇都看過這段資料，**不是前向績效**，也不與前向曲線相連
        （資金從 capital_base 重新起算）。同一個 run 只能回補一次；重複呼叫回 0（不改帳本）。
        """
        self.selfcheck()
        sdir = self._scope(strategy_id)
        run = self.ledger.find_run(strategy_id, tier=TIER)
        if run is None:
            raise DryRunError(f"{strategy_id}: 尚未啟動觀察（先 observe-start）")
        run_id, sha = run["run_id"], run["strategy_sha256"]
        why = self._still_eligible(strategy_id, sha)
        if why is not None:
            raise self._ineligible_error(strategy_id)
        if strategy_sha256(sdir) != sha:
            raise FrozenHashMismatch(f"{strategy_id}: 策略內容與凍結雜湊不符，拒絕回補")
        fw = self.runner.framework_version()
        if fw != run["framework_version"]:
            raise FrameworkMismatch(f"{strategy_id}: 框架版本 {fw!r} ≠ 凍結的 {run['framework_version']!r}")
        if self.ledger.has_history(run_id):
            return 0
        prior = [s for s in self.runner.sessions(hist_start, run["start_date"]) if s < run["start_date"]]
        if len(prior) < 2:
            raise DryRunError(f"{hist_start} 到觀察起算日 {run['start_date']} 之間沒有足夠的交易日可回補")
        series = {}
        for sc, params in self.params.items():
            sim = self.runner.simulate(sdir, start=prior[0], end=prior[-1],
                                       capital_base=run["capital_base"], params=params, scenario=sc)
            series[sc] = [(d.session, d.equity) for d in sim.daily]
        return self.ledger.append_history(run_id, series, cost_model_version=COST_MODEL_VERSION,
                                          data_snapshot=self.runner.latest_snapshot(), ts=self.now())
