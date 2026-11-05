"""stat-ruleset 1.2（lockbox）的測試。

1.2 的宣稱是：L1 用探索窗績效篩選的 S 個候選不計 N，確認在封存窗、N 只計晉級數 K。
這個宣稱只有在下面這些事被**機制**擋住時才成立，所以每一條都對應一個「違反必須失敗」：

1. 沒宣告資料窗就拒答（否則 screening 放寬沒有任何檢查點）。
2. L1 篩選碰封存窗、或在 full 資料上用績效篩選 -> 硬錯誤。
3. 封存窗確認的門檻隨 K 收緊（HLZ 固定 |t|>=3 對大 K 太鬆），K 超過上限拒答。
4. 1.1 的行為完全不變（舊 ledger 記錄依舊版規則重算仍成立）。
"""
from __future__ import annotations

import math
from statistics import NormalDist

import pytest
import yaml

from strategies._common.validation import decision as dec
from strategies._common.validation.decision import (
    DecisionError,
    Facts,
    UnderdeterminedError,
    audit_record,
    load_ruleset,
    resolve,
)

V = "1.2"


def _facts(**kw) -> Facts:
    base = dict(
        estimand="sharpe",
        design="one_sample",
        overlap="none",
        autocorr="no",
        normal="no",
        family="bounded_multiple",
        purpose="selection",
        n_eff=1200,
        n_trials=20,
        window="lockbox",
    )
    base.update(kw)
    return Facts(**base)


def _z(k: int) -> float:
    return NormalDist().inv_cdf(1 - 0.025 / k)


# --- 一、資料窗必須宣告 -------------------------------------------------------

def test_window_is_required_in_12():
    f = _facts()
    f = dec._replace(f, window=None)
    with pytest.raises(UnderdeterminedError, match="window"):
        resolve(f, V)


def test_11_does_not_need_window_and_is_unchanged():
    f = _facts(window=None, n_trials=8)
    p = resolve(f, "1.1")
    assert p.ruleset_version == "1.1" and p.threshold_id == "thr.hlz"
    assert p.threshold_t is None


def test_default_version_stays_11():
    assert dec.DEFAULT_RULESET_VERSION == "1.1"


# --- 二、screening / diagnostic 不得碰封存窗 ----------------------------------

def test_screening_on_exploration_is_free_of_n():
    p = resolve(_facts(purpose="screening", window="exploration"), V)
    assert p.delta_n == 0 and p.may_be_selected is False


@pytest.mark.parametrize("window", ["lockbox", "full"])
def test_screening_outside_exploration_is_a_hard_error(window):
    with pytest.raises(DecisionError, match="exploration"):
        resolve(_facts(purpose="screening", window=window), V)


def test_diagnostic_may_not_read_lockbox():
    with pytest.raises(DecisionError, match="lockbox"):
        resolve(_facts(purpose="diagnostic", window="lockbox"), V)


# --- 三、封存窗確認：N 計 K、門檻隨 K 收緊 ------------------------------------

def test_confirmation_counts_one_n_per_candidate():
    p = resolve(_facts(), V)
    assert p.delta_n == 1 and p.may_be_selected is True


@pytest.mark.parametrize("k", [20, 50])
def test_confirmation_threshold_is_max_of_floor_and_bonferroni(k):
    p = resolve(_facts(n_trials=k), V)
    assert p.threshold_t == pytest.approx(max(3.0, _z(k)), abs=1e-3)


def test_confirmation_threshold_floor_is_three_for_small_k():
    p = resolve(_facts(n_trials=8), V)
    assert p.threshold_t == 3.0


def test_confirmation_threshold_grows_with_k_unlike_11_hlz():
    small, large = resolve(_facts(n_trials=10), V), resolve(_facts(n_trials=50), V)
    assert large.threshold_t > small.threshold_t
    p11 = resolve(_facts(window=None, n_trials=50), "1.1")
    assert large.threshold_t > 3.0 and p11.threshold_t is None  # 1.1 固定 |t|>=3


def test_k_above_cap_refuses():
    cap = load_ruleset(V)["lockbox"]["parameters"]["k_cap"]
    with pytest.raises(DecisionError, match="k_cap|上限"):
        resolve(_facts(n_trials=cap + 1), V)
    resolve(_facts(n_trials=cap), V)  # 邊界成立


@pytest.mark.parametrize("k,thr", [(1, "thr.single"), (3, "thr.bonferroni"), (5, "thr.bonferroni")])
def test_small_k_keeps_11_thresholds(k, thr):
    fam = "single_preregistered" if k == 1 else "bounded_multiple"
    p = resolve(_facts(family=fam, n_trials=k), V)
    assert p.threshold_id == thr and p.threshold_t is None


def test_best_of_m_keeps_dsr_route_and_adds_nothing_loose():
    p = resolve(_facts(family="best_of_M", n_trials=20), V)
    assert p.threshold_id == "thr.best_of_m"


def test_confirmation_requires_n_trials():
    with pytest.raises(UnderdeterminedError, match="n_trials"):
        resolve(_facts(n_trials=None), V)


def test_lockbox_disclosures_are_carried_into_must_report():
    p = resolve(_facts(), V)
    assert any("lockbox" in r for r in p.must_report)


# --- 四、規則集結構：1.2 相對 1.1 只改該改的地方 -----------------------------------

def test_12_is_11_plus_declared_changes_only():
    a, b = load_ruleset("1.1"), load_ruleset("1.2")
    for key in ("schema", "base", "se_policy", "bootstrap_spec", "gates", "verdicts", "derived_from"):
        assert a[key] == b[key], key
    assert {k: v for k, v in b["axes"].items() if k != "window"} == a["axes"]
    for k in a["multiplicity"]:
        if k != "n_accounting":
            assert a["multiplicity"][k] == b["multiplicity"][k], k
    na, nb = a["multiplicity"]["n_accounting"], b["multiplicity"]["n_accounting"]
    assert na["selection"] == nb["selection"] and na["diagnostic"] == nb["diagnostic"]
    assert nb["screening"]["delta_n"] == 0


def test_lockbox_condition_ids_exact():
    ids = {c["id"] for c in load_ruleset("1.2")["lockbox"]["conditions"]}
    assert ids == {"lb.disjoint", "lb.unread", "lb.prereg_before_read",
                   "lb.one_shot", "lb.burn_on_read", "lb.frozen_library"}


def test_power_table_arithmetic():
    for r in load_ruleset("1.2")["lockbox"]["power_table"]["rows"]:
        assert abs(r["t"] / math.sqrt(r["years"]) - r["sr_observed_min"]) < 0.006
        assert abs((r["t"] + 0.84) / math.sqrt(r["years"]) - r["sr_true_for_80pct_power"]) < 0.006


# --- 五、稽核：舊版記錄依舊版重算 -------------------------------------------------

def test_audit_recomputes_under_recorded_version_with_window():
    f = _facts()
    p = resolve(f, V)
    rec = {"trial_id": "t1", "stat_decision": {
        "ruleset_version": V, "decision_path": f.as_path(), "rule_id": p.rule_id,
        "primary_test": p.primary_test, "se_correction": p.se_correction,
        "threshold": p.threshold, "delta_n": p.delta_n, "may_be_selected": p.may_be_selected}}
    assert audit_record(rec) == []
    rec["stat_decision"]["delta_n"] = 0  # 偷把確認記成不佔 N
    assert audit_record(rec)
