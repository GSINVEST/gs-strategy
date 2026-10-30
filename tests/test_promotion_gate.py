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
