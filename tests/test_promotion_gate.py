import pytest

from strategies._common.promotion import rules
from strategies._common.promotion.schema import (
    BLOCKED, OK, PROVISIONAL, UNAVAILABLE, CheckResult, verdict_of,
)


def _c(status):
    return CheckResult(name="x", status=status, value=None, threshold="-", detail="")


def test_thresholds_are_pinned():
    # 門檻只准向上收緊：要改這些數字，必須同時改這個測試並在 PR 說明理由。
    assert rules.DSR_MIN == 0.95
    assert rules.PBO_MAX == 0.5
    assert rules.N_OK == 250
    assert rules.N_PROVISIONAL == 50
    assert rules.SPA_ALPHA == 0.05


def test_check_result_rejects_unknown_status():
    with pytest.raises(ValueError):
        CheckResult(name="x", status="maybe", value=None, threshold="-", detail="")


def test_verdict_all_ok_is_eligible():
    assert verdict_of([_c(OK), _c(OK)]) == "eligible"


def test_verdict_blocked_wins():
    assert verdict_of([_c(OK), _c(PROVISIONAL), _c(BLOCKED)]) == "blocked"


def test_verdict_unavailable_is_conditional_not_eligible():
    assert verdict_of([_c(OK), _c(UNAVAILABLE)]) == "conditional"


def test_verdict_provisional_is_conditional():
    assert verdict_of([_c(OK), _c(PROVISIONAL)]) == "conditional"


import numpy as np

from strategies._common.promotion import checks


def _good_returns(n=500, seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(0.002, 0.01, n)


def _zero_skill_returns(n=500, seed=1):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, 0.01, n)
    return r - r.mean()          # 夏普恰為 0，結果可重現


def test_clean_returns_drops_nan_and_inf():
    out = checks.clean_returns([0.01, float("nan"), float("inf"), -0.02])
    assert list(out) == [0.01, -0.02]


@pytest.mark.parametrize("n,expected", [(300, OK), (100, PROVISIONAL), (10, BLOCKED)])
def test_check_sample(n, expected):
    assert checks.check_sample(np.zeros(n) + 0.001).status == expected


def test_check_dsr_ok_for_strong_series():
    res = checks.check_dsr(_good_returns(), n_trials=5)
    assert res.status == OK and res.value > 0.95


def test_check_dsr_blocked_for_zero_skill_series():
    res = checks.check_dsr(_zero_skill_returns(), n_trials=5)
    assert res.status == BLOCKED and res.value < 0.95


def test_check_dsr_single_trial_is_provisional_not_ok():
    # N=1 時 DSR 等於 PSR，沒有做任何多重檢定校正，不得給 ok。
    res = checks.check_dsr(_good_returns(), n_trials=1)
    assert res.status == PROVISIONAL
    assert "未 deflate" in res.detail


def test_check_dsr_too_short_is_unavailable():
    assert checks.check_dsr(np.full(5, 0.01), n_trials=5).status == UNAVAILABLE


def test_check_pbo_none_is_unavailable():
    assert checks.check_pbo(None).status == UNAVAILABLE


def test_check_pbo_single_candidate_is_unavailable():
    assert checks.check_pbo(np.zeros((100, 1))).status == UNAVAILABLE


def test_check_pbo_threshold_logic(monkeypatch):
    m = np.zeros((100, 3))
    monkeypatch.setattr(checks, "pbo", lambda *a, **k: 0.3)
    assert checks.check_pbo(m).status == OK
    monkeypatch.setattr(checks, "pbo", lambda *a, **k: 0.5)
    assert checks.check_pbo(m).status == BLOCKED      # 必須嚴格小於
    monkeypatch.setattr(checks, "pbo", lambda *a, **k: float("nan"))
    assert checks.check_pbo(m).status == UNAVAILABLE


def test_check_pbo_real_robust_candidate_is_ok():
    rng = np.random.default_rng(2)
    m = rng.normal(0.0, 0.01, (400, 6))
    m[:, 0] += 0.003              # 第 0 欄在所有時段都明顯較好
    res = checks.check_pbo(m)
    assert res.status == OK and res.value < 0.5


def test_check_cost_stress():
    assert checks.check_cost_stress(None).status == UNAVAILABLE
    assert checks.check_cost_stress(np.full(100, 0.001)).status == OK
    assert checks.check_cost_stress(np.full(100, -0.001)).status == BLOCKED


def test_check_spa():
    assert checks.check_spa(None).status == UNAVAILABLE
    assert checks.check_spa(0.01).status == OK
    assert checks.check_spa(0.20).status == BLOCKED


@pytest.mark.parametrize("code,expected", [
    (0, OK), (1, PROVISIONAL), (2, BLOCKED), (3, UNAVAILABLE), (None, UNAVAILABLE),
])
def test_check_external_maps_exit_codes(code, expected):
    assert checks.check_external("param_robustness", code).status == expected


def test_check_external_unknown_code_is_unavailable():
    assert checks.check_external("tail_scenarios", 99).status == UNAVAILABLE


import json

from strategies._common.promotion import gate


def _ledger(tmp_path, n):
    p = tmp_path / "trials.jsonl"
    p.write_text(
        "\n".join(json.dumps({"stat_decision": {"delta_n": 1}}) for _ in range(n)) + "\n",
        encoding="utf-8",
    )
    return p


def _inputs(tmp_path, **kw):
    base = dict(
        strategy_id="s1",
        returns=_good_returns(),
        ledger=_ledger(tmp_path, 5),
        candidates=None,
        stress_returns=np.full(300, 0.001),
        spa_p=0.01,
        external={n: 0 for n in gate.EXTERNAL_CHECKS},
    )
    base.update(kw)
    return gate.GateInputs(**base)


def test_n_trials_comes_from_ledger_not_caller(tmp_path):
    rep = gate.evaluate(_inputs(tmp_path))
    assert rep.n_trials == 5 and rep.n_trials_source == "ledger"


def test_missing_ledger_gives_zero_trials_and_provisional_dsr(tmp_path):
    rep = gate.evaluate(_inputs(tmp_path, ledger=tmp_path / "nope.jsonl"))
    dsr = next(c for c in rep.checks if c.name == "dsr")
    assert rep.n_trials == 0 and dsr.status == PROVISIONAL


def _all_green(tmp_path, **kw):
    rng = np.random.default_rng(3)
    m = rng.normal(0.0, 0.01, (400, 6))
    m[:, 0] += 0.003
    return gate.evaluate(_inputs(tmp_path, candidates=m, **kw))


def test_all_green_with_self_declared_inputs_is_only_conditional(tmp_path):
    # SPA p 值與 --external 結束碼是手填的，閘門驗不了來源：全 ok 也不得 eligible。
    rep = _all_green(tmp_path)
    assert all(c.status == OK for c in rep.checks), [c.to_dict() for c in rep.checks if c.status != OK]
    assert rep.verdict == "conditional"
    declared = {c.name for c in rep.checks if c.self_declared}
    assert declared == {"spa", *gate.EXTERNAL_CHECKS}
    assert all("自行申報" in c.detail for c in rep.checks if c.self_declared)


def test_computed_checks_are_not_marked_self_declared(tmp_path):
    rep = _all_green(tmp_path)
    assert not any(c.self_declared for c in rep.checks if c.name in {"sample", "dsr", "pbo", "cost_stress"})


def test_verdict_eligible_requires_no_self_declared_check():
    ok = CheckResult("x", OK, None, "-", "")
    decl = CheckResult("y", OK, None, "-", "", self_declared=True)
    assert verdict_of([ok]) == "eligible"
    assert verdict_of([ok, decl]) == "conditional"
    assert verdict_of([ok, CheckResult("z", BLOCKED, None, "-", "", True)]) == "blocked"


@pytest.mark.parametrize("p", [-0.1, 1.5, float("nan")])
def test_check_spa_rejects_out_of_range_p(p):
    res = checks.check_spa(p)
    assert res.status == UNAVAILABLE and res.value is None


def test_check_spa_boundaries_are_valid():
    assert checks.check_spa(0.0).status == OK
    assert checks.check_spa(1.0).status == BLOCKED


def test_report_binds_inputs_by_sha256(tmp_path):
    a = gate.evaluate(_inputs(tmp_path)).inputs_sha256
    assert a["returns"] and a["candidates"] is None and a["stress_returns"]
    b = gate.evaluate(_inputs(tmp_path, returns=_good_returns(seed=9))).inputs_sha256
    assert a["returns"] != b["returns"] and a["stress_returns"] == b["stress_returns"]
    assert gate.evaluate(_inputs(tmp_path)).inputs_sha256 == a


def test_n_trials_uses_deduplicated_ledger(tmp_path):
    # gs-strategy#50：同 trial_id 的追加行只算一次；沒有 trial_id 的逐筆算。
    lines = [
        {"trial_id": "a", "stat_decision": {"delta_n": 1}},
        {"trial_id": "a", "stat_decision": {"delta_n": 1}},     # 完成行，不得讓 N 加倍
        {"trial_id": "b", "stat_decision": {"delta_n": 1}},
        {"stat_decision": {"delta_n": 1}},
    ]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert gate.evaluate(_inputs(tmp_path, ledger=p)).n_trials == 3


def test_missing_pbo_and_spa_make_it_conditional(tmp_path):
    rep = gate.evaluate(_inputs(tmp_path, candidates=None, spa_p=None))
    assert rep.verdict == "conditional"
    names = {c.name for c in rep.checks if c.status == UNAVAILABLE}
    assert {"pbo", "spa"} <= names


def test_missing_external_checks_are_listed_unavailable(tmp_path):
    rep = gate.evaluate(_inputs(tmp_path, external={}))
    names = {c.name for c in rep.checks if c.status == UNAVAILABLE}
    assert set(gate.EXTERNAL_CHECKS) <= names


def test_any_blocked_blocks(tmp_path):
    rep = gate.evaluate(_inputs(tmp_path, returns=_zero_skill_returns()))
    assert rep.verdict == "blocked"


def test_evaluate_is_deterministic(tmp_path):
    a = gate.evaluate(_inputs(tmp_path)).to_dict()
    b = gate.evaluate(_inputs(tmp_path)).to_dict()
    assert a == b


def test_report_carries_schema_and_rules_version(tmp_path):
    rep = gate.evaluate(_inputs(tmp_path)).to_dict()
    assert rep["schema"] == "promotion-report/1" and rep["rules_version"] == rules.RULES_VERSION
