# tests/test_idea_e2e.py
import json
import sys
from pathlib import Path

import pytest
import yaml

from quant_crawler.ideas import cli
from quant_crawler.ideas.batch import register_batch, select_batch
from quant_crawler.ideas.card import IdeaCard, iter_cards, load_card, save_card, set_status
from quant_crawler.ideas.cli import admit_card, report_counts, run_all, triage_dir

GSL_SRC = Path(__file__).resolve().parents[1] / "strategies" / "_common" / "gslang"
STRAT_OK = ("def initialize(context):\n    pass\n\n\ndef handle_data(context, data):\n    pass\n")


def test_triage_dir_and_report(tmp_path):
    save_card(IdeaCard(id="a_1", source="s", title="t", hypothesis="TX 站上均線做多",
                       market="TX futures", signature="sma tx"), tmp_path)
    save_card(IdeaCard(id="a_2", source="s", title="t", hypothesis="", market="TX futures"), tmp_path)
    save_card(IdeaCard(id="a_3", source="s", title="t", hypothesis="cross-sectional momentum",
                       market="TX futures", signature="xs mom"), tmp_path)
    save_card(IdeaCard(id="a_4", source="s", title="t", hypothesis="TX 站上均線做多",
                       market="TX futures", signature="TX sma"), tmp_path)   # 與 a_1 同簽章
    n = triage_dir(tmp_path)
    assert n == 4
    st = {c.id: c.status for c in iter_cards(tmp_path)}
    assert st == {"a_1": "triaged", "a_2": "drop", "a_3": "defer", "a_4": "drop"}
    counts = report_counts(tmp_path)
    assert counts["triaged"] == 1 and counts["drop"] == 2 and counts["defer"] == 1


def test_run_all_respects_known_signatures(tmp_path):
    save_card(IdeaCard(id="b_1", source="s", title="t", hypothesis="TX 站上均線做多",
                       market="TX futures", signature="sma tx"), tmp_path)
    assert run_all(tmp_path, {"TX sma"}) == 1
    assert load_card(tmp_path / "b_1.md").status == "drop"


def test_backtest_without_key_reports_not_run(monkeypatch, capsys):
    monkeypatch.delenv("TEJAPI_KEY", raising=False)
    assert cli.main(["backtest", "x"]) == 3
    assert "未實跑" in capsys.readouterr().err


def test_extract_without_llm_env_errors(monkeypatch, capsys):
    for k in ("GDECK_LLM_URL", "GDECK_LLM_KEY", "GDECK_LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert cli.main(["extract", "--since", "2026-01-01"]) == 2
    assert "GDECK_LLM" in capsys.readouterr().err


def test_batch_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "CARDS", tmp_path / "ideas")
    assert cli.main(["batch"]) == 0
    assert "候選 0 個" in capsys.readouterr().out


def _impl_card(tmp_path, cid="e2e_x"):
    c = IdeaCard(id=cid, source="arxiv:1", title="T", hypothesis="站上均線做多", market="TX futures",
                 signature="sma tx")
    for s in ("triaged", "fsm", "impl"):
        set_status(c, s)
    c.verdict = "pass_with_bounds"
    return c


def _setup_admit(tmp_path, strategy_py=STRAT_OK, fill_prereg=False):
    """tmp 內完整走：register_batch（tmp ledger／prereg_dir）→ bt_done → admit_card。"""
    cards, prereg_dir, strategies = tmp_path / "ideas", tmp_path / "prereg", tmp_path / "strategies"
    ledger = tmp_path / "log" / "trials.jsonl"
    ledger.parent.mkdir()
    c = _impl_card(tmp_path)
    ir = {"id": "e2e_x", "instruments": [{"id": "TX"}], "constants": []}
    work = cards / "_work" / c.id
    work.mkdir(parents=True)
    (work / "strategy.py").write_text(strategy_py, encoding="utf-8")
    (work / "e2e_x.ir.json").write_text(json.dumps(ir), encoding="utf-8")
    c.ir_path = str(work / "e2e_x.ir.json")
    sel = select_batch([c])
    info = register_batch(sel, tmp_path / "batches", "2026-11-12T00:00:00Z", ledger, prereg_dir, cards_dir=cards)
    c = load_card(cards / "e2e_x.md")
    assert c.status == "bt_registered"
    set_status(c, "bt_done")
    res = tmp_path / "result.pkl"
    res.write_bytes(b"x")
    return c, info, dict(strategies_dir=strategies, prereg_dir=prereg_dir, gslang_src=GSL_SRC,
                         bt_result=res, work_dir=cards / "_work", cards_dir=cards), ledger


def test_register_then_admit_rejected_when_prereg_unfilled(tmp_path):
    c, info, kw, ledger = _setup_admit(tmp_path)
    before = ledger.read_bytes()
    assert len(before.splitlines()) == 2              # 根紀錄＋1 個 trial，且寫在 tmp
    why = admit_card(c, **kw)
    assert any("preregistration" in w for w in why)   # 草稿 author／hypothesis／holding_periods 空 → 真的被擋
    assert not (kw["strategies_dir"] / "e2e_x").exists()
    assert not (kw["strategies_dir"] / "INDEX.yaml").exists()
    assert c.status == "bt_done"
    assert ledger.read_bytes() == before


def test_admit_rejected_and_bundle_removed_when_bundle_invalid(tmp_path):
    c, info, kw, _ = _setup_admit(tmp_path, strategy_py="x = 1\n")   # 沒有 initialize／handle_data
    why = admit_card(c, **kw)
    assert why and any("validate" in w.lower() or "initialize" in w or "handle_data" in w for w in why)
    assert not (kw["strategies_dir"] / "e2e_x").exists()
    assert c.status == "bt_done"


def test_admit_rejected_without_backtest_result(tmp_path):
    c, info, kw, _ = _setup_admit(tmp_path)
    kw["bt_result"] = None
    why = admit_card(c, **kw)
    assert any("回測結果" in w for w in why)
    assert not kw["strategies_dir"].exists() or not (kw["strategies_dir"] / "e2e_x").exists()


def test_admit_succeeds_only_after_human_fills_prereg(tmp_path):
    """人工補完 author／hypothesis／mechanism／holding_periods 等後，才由真的驗證腳本放行。"""
    c, info, kw, _ = _setup_admit(tmp_path)
    pf = kw["prereg_dir"] / f"{info['batch_id']}.yaml"
    doc = yaml.safe_load(pf.read_text(encoding="utf-8"))
    doc.update(author="test-human", hypothesis="測試用：方向已押定", economic_mechanism="測試用機制")
    doc["sample"].update(start="2020-01-01", end="2026-04-30", contains_full_bear_market=True, holdout="2025")
    doc["stat_plan"]["holding_periods"] = 5
    doc["thresholds"].update(primary_metric="sharpe", pass_condition="sharpe>0.5")
    doc["falsification"] = ["成本乘二後 sharpe<0"]
    pf.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    why = admit_card(c, **kw)
    assert why == [], why
    assert c.status == "admitted"
    assert (kw["strategies_dir"] / "e2e_x" / "manifest.yaml").is_file()
    idx = yaml.safe_load((kw["strategies_dir"] / "INDEX.yaml").read_text(encoding="utf-8"))
    assert [e["id"] for e in idx["entries"]] == ["e2e_x"]
