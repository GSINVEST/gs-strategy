"""檢定處方 resolver 的測試。

測試的重點不是「函式跑得動」，而是三類**規範被違反時必須失敗**的情況：

1. 該拒答的地方沒有拒答（套了預設值 → 沒檢查的人得到最寬鬆的待遇）。
2. 門檻沒有隨 N 變嚴（多重檢定校正失效 → 暴力搜不自傷）。
3. 記錄與規則集對不上時稽核沒有抓到（可回溯性形同虛設）。

這三類每一類都對應一個真實會發生的失效模式，所以每一類都要有測試釘住。
"""
from __future__ import annotations

import pytest

from strategies._common.validation.decision import (
    Facts,
    RulesetError,
    UnderdeterminedError,
    audit_ledger,
    audit_record,
    load_ruleset,
    resolve,
)


def _facts(**kw) -> Facts:
    base = dict(
        estimand="mean_return",
        design="one_sample",
        overlap="none",
        autocorr="no",
        normal="no",
        family="single_preregistered",
        purpose="selection",
        n_eff=500,
    )
    base.update(kw)
    return Facts(**base)


# ---------------------------------------------------------------------------
# 一、fail-closed：不確定時拒答，不套預設
# ---------------------------------------------------------------------------

def test_missing_autocorr_refuses_rather_than_assuming_iid():
    """沒跑 Ljung-Box 不得自動得到 iid。

    iid 是唯一會讓標準誤變小的選項。若此處預設 iid，「沒檢查」就會自動拿到
    最寬鬆的待遇，方向與規範完全相反。
    """
    with pytest.raises(UnderdeterminedError) as e:
        resolve(_facts(autocorr="unknown"))
    assert "Ljung-Box" in str(e.value) or "iid" in str(e.value)


def test_missing_n_eff_refuses():
    """n_eff 沒有保守方向可猜，所以缺值必須拒答。"""
    with pytest.raises(UnderdeterminedError) as e:
        resolve(_facts(n_eff=None))
    assert "n_eff" in str(e.value)


def test_bounded_multiple_without_n_trials_refuses_with_useful_message():
    """bounded_multiple 缺 n_trials 要報「缺 N」，不能報成「規則集有洞」。

    兩條門檻規則（Bonferroni 與 HLZ）都以 n_trials 分界，缺值會讓兩條都不命中。
    若讓它掉進 _pick_one，錯誤訊息會把使用者指向規則集，而不是指向他該去讀
    ledger——訊息指錯地方，等於這個 fail-closed 沒有作用。
    """
    with pytest.raises(UnderdeterminedError) as e:
        resolve(_facts(family="bounded_multiple", n_trials=None))
    assert "n_trials" in str(e.value)
    assert "ledger" in str(e.value)


def test_unknown_normality_defaults_to_nonparametric_primary():
    """normal 缺值走無母數，因為那是保守方向——這個預設是規範的一部分。"""
    p = resolve(_facts(normal="unknown"))
    assert p.primary_test == "Wilcoxon 符號檢定"
    assert p.alternative_test == "單樣本 t 檢定"
    assert any("預設立場" in n for n in p.notes)


# ---------------------------------------------------------------------------
# 二、多重檢定：門檻必須隨 N 變嚴
# ---------------------------------------------------------------------------

def test_threshold_tightens_with_n_trials():
    """N=1 -> |t|>=2；N<=5 -> Bonferroni；N>=6 -> |t|>=3。"""
    single = resolve(_facts(family="single_preregistered"))
    assert single.threshold == "|t| >= 2"

    bonf = resolve(_facts(family="bounded_multiple", n_trials=4))
    assert "0.0125" in bonf.threshold          # 0.05 / 4

    hlz = resolve(_facts(family="bounded_multiple", n_trials=9))
    assert hlz.threshold == "|t| >= 3"


def test_unregistered_finding_is_downgraded_not_voided():
    """未事前登記的顯著結果不是無效，是降級到 open_mining（|t|>=3）。"""
    p = resolve(_facts(preregistered=False))
    assert p.threshold_id == "thr.mining"
    assert p.threshold == "|t| >= 3"
    assert any("探索性" in n for n in p.notes)


def test_open_mining_demands_online_fdr_not_just_a_fixed_threshold():
    """|t|>=3 是固定門檻，表達不了「你已經燒掉多少搜尋預算」。

    連續挖掘的母體是線上成長的，所以這一支必須同時走 ADDIS。這個旗標存在的
    意義是讓呼叫端**沒辦法只看門檻就交差**——處方裡會明講還要跑什麼。
    """
    p = resolve(_facts(family="open_mining"))
    assert p.threshold == "|t| >= 3"
    assert p.online_fdr_required is True
    assert any("AddisBudget" in n or "線上 FDR" in n for n in p.notes)
    assert any("pre-registration" in n for n in p.notes)


def test_bounded_families_do_not_demand_online_fdr():
    """N 事先已知時離線修正就夠了；不該無差別要求線上程序。"""
    assert resolve(_facts(family="single_preregistered")).online_fdr_required is False
    assert resolve(
        _facts(family="bounded_multiple", n_trials=9)
    ).online_fdr_required is False


def test_best_of_m_routes_to_dsr_not_bonferroni():
    """從 M 個跑完的策略挑最好的那個，工具是 DSR/SPA，不是 Bonferroni。

    這是本規則集對規範原文的增補之一：混用會讓「選最好那個」的偏差被當成
    「單一策略測了 N 組參數」處理，低估了偏差。
    """
    p = resolve(_facts(estimand="sharpe", family="best_of_M", n_trials=41))
    assert p.threshold_id == "thr.best_of_m"
    assert "DSR" in p.threshold and "PBO" in p.threshold


# ---------------------------------------------------------------------------
# 三、N 記帳：screening / diagnostic 不得推高母體，也不得被選用
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "purpose, delta_n, selectable",
    [("screening", 0, False), ("diagnostic", 0, False), ("selection", 1, True)],
)
def test_purpose_controls_n_accounting(purpose, delta_n, selectable):
    p = resolve(_facts(purpose=purpose))
    assert p.delta_n == delta_n
    assert p.may_be_selected is selectable


def test_non_selection_says_its_threshold_is_not_a_verdict():
    """非 selection 仍會解析出門檻，但必須明講那不是判決閘門。

    否則報告上並排的「|t| >= 2」會被讀成「這個候選過關了」——這正是把
    screening 結果偷渡成研究結論的路徑。
    """
    p = resolve(_facts(purpose="screening"))
    assert p.max_verdict == "候選"
    assert any("僅供參考" in n for n in p.notes)


# ---------------------------------------------------------------------------
# 四、標準誤：重疊與群聚必須被修正
# ---------------------------------------------------------------------------

def test_overlapping_holding_period_forces_newey_west_with_spec_lag():
    """規範 §零 Q4：lag 取持有天數減一。"""
    p = resolve(_facts(overlap="overlapping", holding_periods=5, autocorr="yes"))
    assert p.se_correction == "newey_west"
    assert p.se_lag == 4


def test_overlap_rule_does_not_depend_on_ljung_box():
    """重疊本身就保證自相關，不需要 Ljung-Box 也該修正。

    若這條規則去看 autocorr，一個宣告 autocorr='no' 的重疊樣本就會拿到 iid
    標準誤——而那組宣告在重疊取樣下本來就不可能成立。
    """
    p = resolve(_facts(overlap="overlapping", holding_periods=10, autocorr="no"))
    assert p.se_correction == "newey_west"
    assert p.se_lag == 9


def test_clustered_events_require_cluster_key():
    with pytest.raises(UnderdeterminedError):
        resolve(_facts(overlap="clustered", cluster_by=None, autocorr="yes"))
    p = resolve(_facts(overlap="clustered", cluster_by="event_date", autocorr="yes"))
    assert p.se_correction == "cluster"


# ---------------------------------------------------------------------------
# 五、最小樣本閘門：「不得宣稱」與「不顯著」是兩件事
# ---------------------------------------------------------------------------

def test_win_rate_under_30_is_blocked_not_merely_insignificant():
    p = resolve(_facts(estimand="win_rate", n_eff=22))
    assert p.gate_status == "blocked"
    assert p.min_n_eff == 30
    assert any("不得宣稱勝率" in n for n in p.gate_notes)


def test_event_study_between_soft_and_hard_floor_is_provisional():
    """50 <= n < 250：可出方向性結論，但不得判決——不是 blocked，也不是 ok。"""
    p = resolve(_facts(unit="event", n_eff=120, overlap="clustered",
                       cluster_by="event_date", autocorr="yes"))
    assert p.gate_status == "provisional"


def test_paired_design_forbids_independent_two_sample_t():
    """規範 §零 Q2 明列的最常見選錯，處方要主動列為禁用。"""
    p = resolve(_facts(design="paired"))
    assert any("獨立雙樣本 t" in f for f in p.forbids)


# ---------------------------------------------------------------------------
# 六、規則集必須是一個真正的分割
# ---------------------------------------------------------------------------

def test_ruleset_slots_are_partitions_over_realistic_fact_space():
    """對一組真實會出現的事實組合窮舉，確認沒有「零命中」或「多重命中」。

    規則集有洞或有重疊都是 bug。resolver 刻意不用「最具體者勝」去掩蓋，
    所以這裡只要有任何一組炸成 RulesetError，就是規則集要修。
    """
    estimand_design = [
        ("mean_return", "one_sample"), ("mean_return", "paired"),
        ("group_difference", "two_independent"), ("group_difference", "k_groups"),
        ("monotonicity", "none"), ("rank_ic", "one_sample"),
        ("win_rate", "one_sample"), ("sharpe", "one_sample"), ("sharpe", "paired"),
        ("contingency", "two_independent"), ("cointegration", "series_vs_series"),
        ("distribution_shape", "one_sample"), ("serial_structure", "none"),
        ("drawdown", "none"),
    ]
    overlaps = [("none", None), ("overlapping", 5), ("clustered", "event_date")]
    families = [("single_preregistered", None), ("bounded_multiple", 3),
                ("bounded_multiple", 12), ("open_mining", None), ("best_of_M", 41)]

    for estimand, design in estimand_design:
        for overlap, extra in overlaps:
            for family, n_trials in families:
                kw = dict(estimand=estimand, design=design, overlap=overlap,
                          autocorr="yes", normal="no", family=family,
                          n_trials=n_trials, purpose="selection", n_eff=400)
                if overlap == "overlapping":
                    kw["holding_periods"] = extra
                if overlap == "clustered":
                    kw["cluster_by"] = extra
                try:
                    resolve(Facts(**kw))
                except RulesetError as e:      # 規則集的洞／重疊
                    pytest.fail(f"{estimand}/{design}/{overlap}/{family}: {e}")
                except UnderdeterminedError:   # 事實不足是預期行為，不是規則集 bug
                    pass


def test_rule_id_is_stable_across_wording_but_not_across_slots():
    """rule_id 是五個槽位的指紋，不是整份處方的指紋。

    處方裡的說明文字日後會潤飾；若 rule_id 跟著變，歷史 trial 就無法跨版本
    比對。反之換了任一槽位就必須換 id，否則兩個不同判決會共用一個編號。
    """
    a = resolve(_facts())
    b = resolve(_facts(n_eff=999))               # n_eff 不是槽位，id 應相同
    c = resolve(_facts(design="paired"))         # base 換了，id 必須不同
    assert a.rule_id == b.rule_id
    assert a.rule_id != c.rule_id


# ---------------------------------------------------------------------------
# 七、可回溯性：稽核要抓得到「記載與規則集不符」
# ---------------------------------------------------------------------------

def _record(**overrides) -> dict:
    facts = _facts()
    p = resolve(facts)
    block = {
        "ruleset_version": p.ruleset_version,
        "decision_path": facts.as_path(),
        "rule_id": p.rule_id,
        "primary_test": p.primary_test,
        "se_correction": p.se_correction,
        "threshold": p.threshold,
        "delta_n": p.delta_n,
        "may_be_selected": p.may_be_selected,
    }
    block.update(overrides)
    return {"trial_id": "t-x", "stat_decision": block}


def test_audit_passes_on_a_faithful_record():
    assert audit_record(_record()) == []


def test_audit_catches_a_test_swapped_after_the_fact():
    """記錄說跑的是 t 檢定，但那組事實依規則集該走 Wilcoxon —— 必須被抓到。

    這是整套設計的收口：前面所有紀律都靠「呼叫端誠實宣告」，這裡讓不誠實
    變成可機械偵測的。
    """
    problems = audit_record(_record(primary_test="單樣本 t 檢定"))
    assert len(problems) == 1
    assert "primary_test" in problems[0]


def test_audit_catches_n_accounting_understated():
    """把 selection 的 ΔN 記成 0 —— DSR 的分母被低估，必須被抓到。"""
    problems = audit_record(_record(delta_n=0))
    assert any("delta_n" in p for p in problems)


def test_audit_reports_missing_block_instead_of_crashing():
    """整份 ledger 要能一次掃完，所以壞記錄回報問題而不是丟例外。"""
    assert audit_record({"trial_id": "t-y"})
    assert audit_record({"trial_id": "t-z", "stat_decision": {}})


def test_audit_ledger_recomputes_n_rather_than_trusting_it():
    """N 是 DSR 的分母，不信任任何人寫在報告裡的數字，逐筆重算。"""
    ledger = [{**_record(), "trial_id": "t-1"}, {**_record(), "trial_id": "t-2"}, {"trial_id": "s", "stat_decision": {
        **_record()["stat_decision"],
        "decision_path": {**_facts(purpose="screening").as_path()},
        "rule_id": resolve(_facts(purpose="screening")).rule_id,
        "primary_test": resolve(_facts(purpose="screening")).primary_test,
        "delta_n": 0,
        "may_be_selected": False,
    }}]
    out = audit_ledger(ledger)
    assert out["n_records"] == 3
    assert out["n_selection_recomputed"] == 2      # screening 那筆不算
    assert out["auditable"], out["problems"]


# ---------------------------------------------------------------------------
# 八、規則集自身的完整性
# ---------------------------------------------------------------------------

def test_every_base_rule_declares_a_nonparametric_or_says_why_not():
    """規範 §零 要求無母數並列；沒有並列版本必須寫明理由。

    靜默省略會讓「這個估計量沒有無母數版本」跟「忘了跑」長得一模一樣。
    """
    rs = load_ruleset()
    for rule in rs["base"]:
        if rule.get("nonparametric") is None:
            assert rule.get("nonparametric_absent_because"), \
                f"{rule['id']} 缺無母數版本且未說明理由"
        if rule.get("parametric") is None:
            assert rule.get("parametric_absent_because"), \
                f"{rule['id']} 缺參數版本且未說明理由"


def test_forbidden_bootstrap_methods_are_carried_into_the_prescription():
    """規範明列的死路（交易層級 iid bootstrap、非 block permutation）要出現在處方裡。

    寫在規則集裡但不送到呼叫端看得到的地方，等於沒寫。
    """
    p = resolve(_facts(estimand="sharpe"))
    joined = " ".join(p.forbids)
    assert "iid bootstrap" in joined
    assert "permutation" in joined


def test_audit_ledger_counts_latest_line_per_trial_id():
    """同 trial_id 追加「完成行」是合法的；N 不得因此加倍（與 stat_tree 一致）。"""
    first = {**_record(), "trial_id": "t-a"}
    done = {**_record(), "trial_id": "t-a", "state": "complete"}
    out = audit_ledger([first, done, {**_record(), "trial_id": "t-b"}])
    assert out["n_selection_recomputed"] == 2
    assert out["auditable"], out["problems"]


def test_audit_ledger_flags_delta_n_decreasing_for_same_trial_id():
    """追加一行同 id 但 delta_n 較小，讀端取最後一筆會讓 N 下降——TrialMonotone。"""
    first = {**_record(), "trial_id": "t-a"}
    shrunk = {**first, "stat_decision": {**first["stat_decision"], "delta_n": 0}}
    out = audit_ledger([first, shrunk])
    assert not out["auditable"]
    assert any("t-a" in p and "delta_n" in p for p in out["problems"])
