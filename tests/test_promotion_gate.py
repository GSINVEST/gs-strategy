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
