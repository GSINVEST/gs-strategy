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


def _fill_prereg(kw, info):
    pf = kw["prereg_dir"] / f"{info['batch_id']}.yaml"
    doc = yaml.safe_load(pf.read_text(encoding="utf-8"))
    doc.update(author="test-human", hypothesis="測試用：方向已押定", economic_mechanism="測試用機制")
    doc["sample"].update(start="2020-01-01", end="2026-04-30", contains_full_bear_market=True, holdout="2025")
    doc["stat_plan"]["holding_periods"] = 5
    doc["thresholds"].update(primary_metric="sharpe", pass_condition="sharpe>0.5")
    doc["falsification"] = ["成本乘二後 sharpe<0"]
    pf.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_admit_rejected_and_bundle_removed_when_bundle_invalid(tmp_path):
    c, info, kw, _ = _setup_admit(tmp_path, strategy_py="x = 1\n")   # 沒有 initialize／handle_data
    _fill_prereg(kw, info)                                           # prereg 已過，只剩 bundle 這個原因
    why = admit_card(c, **kw)
    assert why
    assert any("bundle 未通過" in w for w in why)
    assert any("initialize" in w or "handle_data" in w for w in why)
    assert not (kw["strategies_dir"] / "e2e_x").exists()
    assert c.status == "bt_done"


def test_admit_existing_bundle_not_removed(tmp_path):
    c, info, kw, _ = _setup_admit(tmp_path)
    _fill_prereg(kw, info)
    existing = kw["strategies_dir"] / "e2e_x"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("mine", encoding="utf-8")
    why = admit_card(c, **kw)
    assert why and (existing / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert not (kw["strategies_dir"] / "INDEX.yaml").exists() and c.status == "bt_done"


def test_admit_invalid_id_rejected_without_traceback(tmp_path):
    c, info, kw, _ = _setup_admit(tmp_path)
    _fill_prereg(kw, info)
    c.id = "../evil"
    why = admit_card(c, **kw)
    assert why and any("card.id" in w for w in why)
    assert not (tmp_path / "evil").exists() and c.status == "bt_done"


def test_write_bundle_failure_becomes_rejection(tmp_path, monkeypatch):
    c, info, kw, _ = _setup_admit(tmp_path)
    _fill_prereg(kw, info)
    import quant_crawler.ideas.admit as admit_mod

    def boom(*a, **k):
        raise ValueError("壞 id")
    monkeypatch.setattr(admit_mod, "write_bundle", boom)
    why = admit_card(c, **kw)
    assert why and "寫入 bundle 失敗" in why[0] and c.status == "bt_done"


def test_check_timeout_is_fail_closed_and_rolls_back(tmp_path, monkeypatch):
    c, info, kw, _ = _setup_admit(tmp_path)
    fake = tmp_path / "fakerepo"
    (fake / "scripts").mkdir(parents=True)
    (fake / "scripts" / "check_preregistration.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (fake / "scripts" / "validate_dashboard_bundle.py").write_text(
        "import time\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(cli, "REPO", fake)
    monkeypatch.setattr(cli, "CHECK_TIMEOUT", 1)
    why = admit_card(c, **kw)
    assert any("逾時" in w for w in why)
    assert not (kw["strategies_dir"] / "e2e_x").exists()
    assert not (kw["strategies_dir"] / "INDEX.yaml").exists() and c.status == "bt_done"


def test_check_oserror_is_fail_closed(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("nope")
    monkeypatch.setattr(cli.subprocess, "run", boom)
    ok, out = cli._run_check("check_preregistration.py", Path("x"))
    assert ok is False and "無法執行" in out


def test_save_card_failure_after_index_is_detectable_and_rerun_recognizable(tmp_path, monkeypatch):
    c, info, kw, _ = _setup_admit(tmp_path)
    _fill_prereg(kw, info)
    real = cli.save_card

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(cli, "save_card", boom)
    why = admit_card(c, **kw)
    assert why and "手動設為 admitted" in why[0]
    assert (kw["strategies_dir"] / "e2e_x" / "manifest.yaml").is_file()
    assert cli._index_has(kw["strategies_dir"], "e2e_x")
    monkeypatch.setattr(cli, "save_card", real)
    again = load_card(kw["cards_dir"] / "e2e_x.md")          # 磁碟上尚未寫入 admitted
    assert again.status != "admitted"
    set_status(again, "bt_done")                              # 模擬人把卡片補回 bt_done 後重跑
    why2 = admit_card(again, **kw)
    assert why2 and "已入庫過" in why2[0]


def _fsm_env(tmp_path, monkeypatch, ids=("f_1", "f_2")):
    from quant_crawler.ideas import extract, gslang_bridge as gb
    cards = tmp_path / "ideas"
    for i in ids:
        c = IdeaCard(id=i, source="s", title="t", hypothesis="h", market="TX futures")
        set_status(c, "triaged")
        save_card(c, cards)
    monkeypatch.setattr(cli, "CARDS", cards)
    monkeypatch.setattr(gb, "_root", lambda root=None: tmp_path)
    monkeypatch.setattr(extract, "OpenAICompatLLM", lambda: object())
    return cards, gb


def test_fsm_missing_formal_snapshot_exits_2(tmp_path, monkeypatch, capsys):
    from quant_crawler.ideas import gslang_bridge as gb

    def nope(root=None):
        raise gb.FormalUnavailable("找不到 Gslang 快照")
    monkeypatch.setattr(gb, "_root", nope)
    assert cli.main(["fsm"]) == 2
    assert "找不到 Gslang 快照" in capsys.readouterr().err


def test_fsm_per_card_failure_recorded_and_batch_continues(tmp_path, monkeypatch, capsys):
    cards, gb = _fsm_env(tmp_path, monkeypatch)
    calls = []

    def draft(c, llm, root=None):
        calls.append(c.id)
        raise RuntimeError("LLM 連線失敗")
    monkeypatch.setattr(gb, "draft_gsl", draft)
    assert cli.main(["fsm"]) == 1
    assert calls == ["f_1", "f_2"]                          # 第一張失敗不中斷整批
    for i in ("f_1", "f_2"):
        c = load_card(cards / f"{i}.md")
        assert c.status == "triaged" and "階段 draft_gsl" in c.reason and "LLM 連線失敗" in c.reason
    assert "[fsm 失敗] f_1" in capsys.readouterr().err


def test_fsm_success_cleans_tempdir(tmp_path, monkeypatch):
    from quant_crawler.ideas import tlc
    cards, gb = _fsm_env(tmp_path, monkeypatch, ids=("f_1",))
    seen = []

    class R:
        ir = {}

    def adv(c, gsl, out_dir, root=None):
        out_dir.mkdir(parents=True)
        (out_dir / "i.ir.json").write_text("{}", encoding="utf-8")
        c.ir_path = str(out_dir / "i.ir.json")
        set_status(c, "fsm")
        return R()

    def run(ir_path, work, root=None, **k):
        seen.append(Path(work))
        assert Path(work).is_dir()
        return tlc.TlcResult("pass_with_bounds", {})
    monkeypatch.setattr(gb, "draft_gsl", lambda c, llm, root=None: "gsl")
    monkeypatch.setattr(gb, "advance_card_to_fsm", adv)
    monkeypatch.setattr(gb, "gen_python", lambda ir, root=None: "code\n")
    monkeypatch.setattr(tlc, "run_tlc", run)
    assert cli.main(["fsm"]) == 0
    c = load_card(cards / "f_1.md")
    assert c.status == "impl" and (cards / "_work" / "f_1" / "strategy.py").read_text(encoding="utf-8") == "code\n"
    assert seen and not seen[0].exists()


def test_batch_register_without_candidates_exits_4(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CARDS", tmp_path / "ideas")
    assert cli.main(["batch", "--register"]) == 4


def test_invalid_ids_rejected_before_path_use(capsys):
    assert cli.main(["admit", "../x"]) == 2
    assert cli.main(["backtest", "A/B"]) == 2
    assert "不合法的卡片 id" in capsys.readouterr().err


def test_extract_bad_since_exits_2(capsys):
    assert cli.main(["extract", "--since", "yesterday"]) == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_admit_rejected_without_backtest_result(tmp_path):
    c, info, kw, _ = _setup_admit(tmp_path)
    kw["bt_result"] = None
    why = admit_card(c, **kw)
    assert any("回測結果" in w for w in why)
    assert not kw["strategies_dir"].exists() or not (kw["strategies_dir"] / "e2e_x").exists()


def test_admit_succeeds_only_after_human_fills_prereg(tmp_path):
    """人工補完 author／hypothesis／mechanism／holding_periods 等後，才由真的驗證腳本放行。"""
    c, info, kw, _ = _setup_admit(tmp_path)
    _fill_prereg(kw, info)
    why = admit_card(c, **kw)
    assert why == [], why
    assert c.status == "admitted"
    assert load_card(kw["cards_dir"] / "e2e_x.md").status == "admitted"
    assert (kw["strategies_dir"] / "e2e_x" / "manifest.yaml").is_file()
    idx = yaml.safe_load((kw["strategies_dir"] / "INDEX.yaml").read_text(encoding="utf-8"))
    assert [e["id"] for e in idx["entries"]] == ["e2e_x"]
