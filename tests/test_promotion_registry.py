import json

import numpy as np
import pytest

from strategies._common.promotion import registry
from strategies._common.promotion.schema import (
    BLOCKED, OK, CheckResult, GateReport, verdict_of,
)


def _report(verdict_statuses, strategy_id="s1"):
    checks = tuple(CheckResult(f"c{i}", s, None, "-", "") for i, s in enumerate(verdict_statuses))
    return GateReport("promotion-report/1", "1", strategy_id, 5, "ledger", checks, verdict_of(checks))


@pytest.fixture
def strat(tmp_path):
    d = tmp_path / "s1"
    d.mkdir()
    (d / "strategy.py").write_text("def handle_data(c, d):\n    pass\n", encoding="utf-8")
    (d / "config.yaml").write_text("start: '2020-01-01'\n", encoding="utf-8")
    return d


@pytest.fixture
def reg(tmp_path):
    return tmp_path / "promotions.jsonl"


def _screen(report, strat, reg, **kw):
    args = dict(
        strategy_dir=strat, lineage_id="L1", branch="main",
        framework_version="zipline-tej@abc", data_snapshot="2026-10-30",
        cost_model_version="L1-v0", registry=reg,
    )
    args.update(kw)
    return registry.record_screening(report, **args)


def test_strategy_sha256_changes_with_content(strat):
    a = registry.strategy_sha256(strat)
    (strat / "strategy.py").write_text("# changed\n", encoding="utf-8")
    assert registry.strategy_sha256(strat) != a


def test_record_screening_appends_one_line(strat, reg):
    ev = _screen(_report([OK]), strat, reg)
    lines = reg.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["event"] == "screened"
    assert ev["strategy_sha256"] == registry.strategy_sha256(strat)


def test_registry_is_append_only(strat, reg):
    _screen(_report([OK]), strat, reg)
    first = reg.read_text(encoding="utf-8")
    _screen(_report([OK]), strat, reg)
    assert reg.read_text(encoding="utf-8").startswith(first)


def test_promote_refuses_when_promoters_unset(strat, reg, monkeypatch):
    monkeypatch.delenv("GS_PROMOTERS", raising=False)
    _screen(_report([OK]), strat, reg)
    with pytest.raises(registry.PromotionError, match="GS_PROMOTERS"):
        registry.promote("s1", strategy_dir=strat, by="kevin", registry=reg)


def test_promote_refuses_unlisted_signer(strat, reg, monkeypatch):
    monkeypatch.setenv("GS_PROMOTERS", "kevin")
    _screen(_report([OK]), strat, reg)
    with pytest.raises(registry.PromotionError, match="不在"):
        registry.promote("s1", strategy_dir=strat, by="mallory", registry=reg)


def test_promote_eligible_writes_event(strat, reg, monkeypatch):
    monkeypatch.setenv("GS_PROMOTERS", "kevin,admin")
    _screen(_report([OK, OK]), strat, reg)
    ev = registry.promote("s1", strategy_dir=strat, by="kevin", registry=reg)
    assert ev["event"] == "promoted" and ev["by"] == "kevin"
    assert registry.latest("s1", registry=reg)["event"] == "promoted"


def test_promote_conditional_requires_note(strat, reg, monkeypatch):
    monkeypatch.setenv("GS_PROMOTERS", "kevin")
    _screen(_report([OK, "unavailable"]), strat, reg)
    with pytest.raises(registry.PromotionError, match="簽註"):
        registry.promote("s1", strategy_dir=strat, by="kevin", registry=reg)
    ev = registry.promote("s1", strategy_dir=strat, by="kevin",
                          note="SPA 未跑，人工簽註", registry=reg)
    assert ev["verdict"] == "conditional"


def test_promote_blocked_is_never_allowed(strat, reg, monkeypatch):
    monkeypatch.setenv("GS_PROMOTERS", "kevin")
    _screen(_report([OK, BLOCKED]), strat, reg)
    with pytest.raises(registry.PromotionError, match="blocked"):
        registry.promote("s1", strategy_dir=strat, by="kevin", note="硬上", registry=reg)


def test_promote_without_screening_fails(strat, reg, monkeypatch):
    monkeypatch.setenv("GS_PROMOTERS", "kevin")
    with pytest.raises(registry.PromotionError, match="screened"):
        registry.promote("s1", strategy_dir=strat, by="kevin", registry=reg)


def test_promote_refuses_when_strategy_changed_after_screening(strat, reg, monkeypatch):
    monkeypatch.setenv("GS_PROMOTERS", "kevin")
    _screen(_report([OK]), strat, reg)
    (strat / "strategy.py").write_text("# edited after screening\n", encoding="utf-8")
    with pytest.raises(registry.PromotionError, match="內容已變更"):
        registry.promote("s1", strategy_dir=strat, by="kevin", registry=reg)


def test_cannot_promote_twice(strat, reg, monkeypatch):
    monkeypatch.setenv("GS_PROMOTERS", "kevin")
    _screen(_report([OK]), strat, reg)
    registry.promote("s1", strategy_dir=strat, by="kevin", registry=reg)
    with pytest.raises(registry.PromotionError, match="已晉升"):
        registry.promote("s1", strategy_dir=strat, by="kevin", registry=reg)


def test_new_branch_must_be_screened_again(strat, reg, monkeypatch):
    # 規格 Q9：改策略 = 同 lineage 的另一分支，必須重新 screened，不能沿用舊晉升。
    monkeypatch.setenv("GS_PROMOTERS", "kevin")
    _screen(_report([OK]), strat, reg)
    registry.promote("s1", strategy_dir=strat, by="kevin", registry=reg)
    (strat / "strategy.py").write_text("# branch b\n", encoding="utf-8")
    _screen(_report([OK], strategy_id="s1"), strat, reg, branch="b")
    ev = registry.promote("s1", strategy_dir=strat, by="kevin", registry=reg)
    assert ev["branch"] == "b"


import pandas as pd

from scripts import promotion_gate


def _write_perf(path, n=300, seed=0):
    rng = np.random.default_rng(seed)
    pd.DataFrame({"returns": rng.normal(0.002, 0.01, n)}).to_parquet(path)


def test_cli_evaluate_then_promote(tmp_path, strat, monkeypatch, capsys):
    monkeypatch.setenv("GS_PROMOTERS", "kevin")
    perf = tmp_path / "perf.parquet"
    _write_perf(perf)
    ledger = tmp_path / "trials.jsonl"
    ledger.write_text(json.dumps({"stat_decision": {"delta_n": 1}}) + "\n" +
                      json.dumps({"stat_decision": {"delta_n": 1}}) + "\n", encoding="utf-8")
    reg = tmp_path / "promotions.jsonl"
    common = ["--strategy-dir", str(strat), "--registry", str(reg)]
    rc = promotion_gate.main(["evaluate", *common, "--perf", str(perf), "--ledger", str(ledger),
                              "--lineage-id", "L1", "--branch", "main",
                              "--framework-version", "zipline-tej@abc",
                              "--data-snapshot", "2026-10-30",
                              "--cost-model-version", "L1-v0",
                              "--strategy-id", "s1"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["verdict"] == "conditional"      # 缺 PBO／SPA／外部檢查
    rc = promotion_gate.main(["promote", *common, "--strategy-id", "s1", "--by", "kevin"])
    assert rc == 2                                            # conditional 沒附簽註 → 被拒
    rc = promotion_gate.main(["promote", *common, "--strategy-id", "s1", "--by", "kevin",
                              "--note", "PBO／SPA 未跑，人工簽註"])
    assert rc == 0


def test_cli_missing_perf_is_input_error(tmp_path, strat):
    rc = promotion_gate.main(["evaluate", "--strategy-dir", str(strat),
                              "--perf", str(tmp_path / "nope.parquet"),
                              "--strategy-id", "s1", "--lineage-id", "L", "--branch", "b",
                              "--framework-version", "v", "--data-snapshot", "d",
                              "--cost-model-version", "c",
                              "--registry", str(tmp_path / "r.jsonl")])
    assert rc == 3


def test_screening_count_and_previous_verdict_recorded(strat, reg):
    a = _screen(_report([BLOCKED]), strat, reg)
    b = _screen(_report([OK]), strat, reg)
    assert (a["screening_count"], a["previous_verdict"]) == (1, None)
    assert (b["screening_count"], b["previous_verdict"]) == (2, "blocked")


def test_rescreened_version_needs_note_even_if_eligible(strat, reg, monkeypatch):
    # 換輸入重跑直到過關不計入 trial：至少要人簽註說明為何重篩。
    monkeypatch.setenv("GS_PROMOTERS", "kevin")
    _screen(_report([BLOCKED]), strat, reg)
    _screen(_report([OK]), strat, reg)
    with pytest.raises(registry.PromotionError, match="篩選 2 次"):
        registry.promote("s1", strategy_dir=strat, by="kevin", registry=reg)
    assert registry.promote("s1", strategy_dir=strat, by="kevin",
                            note="修正輸入檔錯誤後重篩", registry=reg)["event"] == "promoted"


def test_screening_count_is_per_version(strat, reg):
    _screen(_report([OK]), strat, reg)
    (strat / "strategy.py").write_text("# v2\n", encoding="utf-8")
    assert _screen(_report([OK]), strat, reg)["screening_count"] == 1


def test_later_blocked_rescreen_revokes_promotion(strat, reg, monkeypatch):
    monkeypatch.setenv("GS_PROMOTERS", "kevin")
    _screen(_report([OK]), strat, reg)
    registry.promote("s1", strategy_dir=strat, by="kevin", registry=reg)
    sha = registry.strategy_sha256(strat)
    assert registry.is_promoted("s1", sha, registry=reg)
    _screen(_report([OK, BLOCKED]), strat, reg)
    assert not registry.is_promoted("s1", sha, registry=reg)
    with pytest.raises(registry.PromotionError, match="blocked"):
        registry.promote("s1", strategy_dir=strat, by="kevin", note="x", registry=reg)


def test_later_non_blocked_rescreen_keeps_promotion(strat, reg, monkeypatch):
    monkeypatch.setenv("GS_PROMOTERS", "kevin")
    _screen(_report([OK]), strat, reg)
    registry.promote("s1", strategy_dir=strat, by="kevin", registry=reg)
    _screen(_report([OK]), strat, reg)
    assert registry.is_promoted("s1", registry.strategy_sha256(strat), registry=reg)


def test_corrupt_lines_are_skipped_with_warning(strat, reg):
    _screen(_report([OK]), strat, reg)
    with reg.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n[]\n")
    with pytest.warns(UserWarning, match="registry 事件"):
        assert registry.latest("s1", registry=reg)["event"] == "screened"


def test_cli_tags_are_recorded(tmp_path, strat, capsys):
    perf = tmp_path / "perf.parquet"
    _write_perf(perf)
    reg = tmp_path / "r.jsonl"
    rc = promotion_gate.main(["evaluate", "--strategy-dir", str(strat), "--registry", str(reg),
                              "--perf", str(perf), "--ledger", str(tmp_path / "none.jsonl"),
                              "--strategy-id", "s1", "--lineage-id", "L", "--branch", "b",
                              "--framework-version", "v", "--data-snapshot", "d",
                              "--cost-model-version", "c", "--tags", "momentum, tw-futures"])
    assert rc == 0
    assert registry.latest("s1", registry=reg)["tags"] == ["momentum", "tw-futures"]
    capsys.readouterr()


def test_cli_warns_on_repeated_screening(tmp_path, strat, capsys):
    perf = tmp_path / "perf.parquet"
    _write_perf(perf)
    argv = ["evaluate", "--strategy-dir", str(strat), "--registry", str(tmp_path / "r.jsonl"),
            "--perf", str(perf), "--ledger", str(tmp_path / "none.jsonl"),
            "--strategy-id", "s1", "--lineage-id", "L", "--branch", "b",
            "--framework-version", "v", "--data-snapshot", "d", "--cost-model-version", "c"]
    promotion_gate.main(argv)
    capsys.readouterr()
    promotion_gate.main(argv)
    assert "第 2 次篩選" in capsys.readouterr().err
