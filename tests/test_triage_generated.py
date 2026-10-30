"""L0 機械分診的測試。

分診會決定「哪些候選永遠不會被跑」，所以它出錯的代價是不對稱的：漏放一個
真候選（假陽性排除）比多留一個骨架嚴重得多。因此測試的重點放在**排除判準
不得過寬**——有 keyword 的不得被當成無假說、組態不同的不得被當成重複。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import triage_generated as tg  # noqa: E402


BASE_MANIFEST = {
    "id": "x",
    "name": "A paper",
    "asset_class": "future",
    "bundle": "tquant_future",
    "start": "2020-01-01",
    "end": "2026-04-30",
    "capital_base": 5000000,
    "params": {"root_symbol": "TX", "position_contracts": 1},
    "symbols": ["TX"],
    "source": {
        "kind": "generated",
        "template": "buy_and_hold",
        "matched_keywords": [],
        "inputs": {"paper": {"title": "A paper", "url": "http://example/1"}},
    },
}

STRATEGY_SRC = '''"""{doc}"""


def _generate_signal(ctx):
    return {sig}
'''


def _make(tmp_path: Path, name: str, *, doc="doc", sig="0",
          template="buy_and_hold", keywords=(), params=None, raw=None) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True)
    (d / "strategy.py").write_text(STRATEGY_SRC.format(doc=doc, sig=sig),
                                   encoding="utf-8")
    if raw is not None:
        (d / "manifest.yaml").write_text(raw, encoding="utf-8")
        return d
    m = json.loads(json.dumps(BASE_MANIFEST))
    m["id"] = name
    m["source"]["template"] = template
    m["source"]["matched_keywords"] = list(keywords)
    if params:
        m["params"] = params
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(m, allow_unicode=True), encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# 指紋：docstring 不得影響身分
# ---------------------------------------------------------------------------

def test_code_fingerprint_ignores_docstrings(tmp_path):
    """這是整個分診的支點。

    349 份 strategy.py 的位元組全部不同，只因為每份 docstring 嵌了自己那篇
    論文的標題。若指紋看 docstring，就會得到 349 個相異值，然後讓所有人以為
    有 349 個相異的研究方向。
    """
    a = _make(tmp_path, "a", doc="論文甲的標題")
    b = _make(tmp_path, "b", doc="完全不同的論文乙標題")
    assert tg.code_fingerprint(a / "strategy.py") == tg.code_fingerprint(b / "strategy.py")


def test_code_fingerprint_still_separates_real_code_differences(tmp_path):
    """剝 docstring 不得剝掉真正的程式碼差異，否則分診會併掉真候選。"""
    a = _make(tmp_path, "a", sig="0")
    b = _make(tmp_path, "b", sig="1")
    assert tg.code_fingerprint(a / "strategy.py") != tg.code_fingerprint(b / "strategy.py")


def test_config_fingerprint_ignores_prose_but_not_params(tmp_path):
    """name/description 只影響閱讀，不得讓同一個回測被算成兩個候選。"""
    m1 = dict(BASE_MANIFEST, name="標題甲")
    m2 = dict(BASE_MANIFEST, name="標題乙")
    assert tg.config_fingerprint(m1) == tg.config_fingerprint(m2)

    m3 = json.loads(json.dumps(BASE_MANIFEST))
    m3["params"]["position_contracts"] = 2
    assert tg.config_fingerprint(m3) != tg.config_fingerprint(BASE_MANIFEST)


# ---------------------------------------------------------------------------
# 分診判準
# ---------------------------------------------------------------------------

def test_fallback_skeleton_without_keywords_is_excluded_not_rejected(tmp_path):
    """排除不是證偽——disposition 必須是 excluded:，不是 rejected。

    harness 的搜尋樹刻意把兩者分色。混用會讓「這從來不是候選」看起來像
    「這被測過而且輸了」，也就是憑空生出一個沒做過的研究結論。
    """
    _make(tmp_path, "a", template="buy_and_hold", keywords=())
    (cand,) = tg.triage(tg.scan(tmp_path))
    assert cand.disposition == "excluded:無假說骨架"
    assert not cand.disposition.startswith("rejected")
    assert cand.resurrect_when


def test_keyword_match_survives_even_on_fallback_template(tmp_path):
    """有抽到關鍵字就不是「無假說」，即使模板恰好是 fallback。

    這個方向的錯誤（把真候選當骨架排除）代價最高，所以獨立釘住。
    """
    _make(tmp_path, "a", template="buy_and_hold", keywords=("momentum",))
    (cand,) = tg.triage(tg.scan(tmp_path))
    assert cand.disposition == "候選"


def test_second_identical_config_is_deduped_and_points_at_the_first(tmp_path):
    _make(tmp_path, "a", template="momentum", keywords=("momentum",))
    _make(tmp_path, "b", template="momentum", keywords=("momentum",), doc="另一篇論文")
    a, b = tg.triage(tg.scan(tmp_path))
    assert a.disposition == "候選"
    assert b.disposition == "excluded:與既有候選同組態"
    assert b.duplicate_of == "a"
    assert "a" in b.resurrect_when


def test_different_params_are_not_deduped(tmp_path):
    """組態不同就是不同候選，不得因為程式碼相同而被併掉。"""
    _make(tmp_path, "a", template="momentum", keywords=("momentum",))
    _make(tmp_path, "b", template="momentum", keywords=("momentum",),
          params={"root_symbol": "TX", "position_contracts": 3})
    a, b = tg.triage(tg.scan(tmp_path))
    assert a.disposition == "候選" and b.disposition == "候選"


def test_no_hypothesis_beats_duplicate_in_reason_ordering(tmp_path):
    """既無假說又重複者，理由要記「無假說」——那才是它真正的問題。"""
    _make(tmp_path, "a", keywords=())
    _make(tmp_path, "b", keywords=(), doc="另一篇")
    for cand in tg.triage(tg.scan(tmp_path)):
        assert cand.disposition == "excluded:無假說骨架"


# ---------------------------------------------------------------------------
# 資料品質：壞掉的 manifest 不得被當成「沒有候選」
# ---------------------------------------------------------------------------

def test_unparsable_manifest_is_surfaced_not_silently_skipped(tmp_path):
    """讀不了的 manifest 是資料品質失敗，靜默跳過會讓壞掉的爬蟲永遠不被發現。"""
    _make(tmp_path, "bad", raw="name: 'xy'\nparams: {}\n")
    (cand,) = tg.triage(tg.scan(tmp_path))
    assert cand.disposition == "excluded:manifest 無法解析"
    assert cand.parse_error


def test_mojibake_is_named_as_such_and_not_repaired_in_place(tmp_path):
    """認出 mojibake 要指向爬蟲的解碼層；就地修字串會讓來源繼續壞下去。"""
    _make(tmp_path, "bad", raw="name: '2018â2020'\n")
    (cand,) = tg.triage(tg.scan(tmp_path))
    assert "mojibake" in cand.parse_error
    assert "爬蟲" in cand.parse_error
    # 檔案本身不得被改動
    assert "" in (tmp_path / "bad" / "manifest.yaml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 多重檢定成本
# ---------------------------------------------------------------------------

def test_deflation_benchmark_rises_with_n():
    """DSR 的門檻必須隨 N 單調上升——這是「多跑一個候選要付代價」的來源。"""
    assert tg.expected_max_sharpe(33) < tg.expected_max_sharpe(382)
    assert tg.expected_max_sharpe(2) < tg.expected_max_sharpe(33)


def test_deflation_benchmark_matches_bailey_lopez_de_prado_shape():
    """N=100 時 E[max SR] 應落在 2.5 附近（(1-γ)Φ⁻¹(1-1/N)+γΦ⁻¹(1-1/(Ne))）。"""
    assert 2.3 < tg.expected_max_sharpe(100) < 2.8


# ---------------------------------------------------------------------------
# trial 記錄
# ---------------------------------------------------------------------------

def test_records_never_consume_the_multiple_testing_budget(tmp_path):
    """分診的每一筆都必須 ΔN=0。這一關若偷偷計入 N，整個設計的意義就沒了。"""
    _make(tmp_path, "a", keywords=("momentum",), template="momentum")
    _make(tmp_path, "b", keywords=())
    cands = tg.triage(tg.scan(tmp_path))
    records = tg.build_records(cands, "2026-08-31T00:00:00Z")
    assert records
    for rec in records:
        block = rec["stat_decision"]
        assert block["delta_n"] == 0
        assert block["no_statistical_claim"] is True
        assert block["basis"]


def test_records_pass_the_ledger_audit(tmp_path):
    """分診產出的記錄必須通過 decision.audit_ledger——可回溯性是端到端的。"""
    from strategies._common.validation.decision import audit_ledger

    _make(tmp_path, "a", keywords=("momentum",), template="momentum")
    _make(tmp_path, "b", keywords=())
    records = tg.build_records(tg.triage(tg.scan(tmp_path)), "2026-08-31T00:00:00Z")
    out = audit_ledger(records)
    assert out["auditable"], out["problems"]
    assert out["n_selection_recomputed"] == 0


def test_duplicates_hang_under_their_class_representative(tmp_path):
    """樹的形狀要把「N 個候選其實是 M 個相異組態」畫出來，而不是排成平行節點。"""
    _make(tmp_path, "a", keywords=("momentum",), template="momentum")
    _make(tmp_path, "b", keywords=("momentum",), template="momentum", doc="另一篇")
    records = tg.build_records(tg.triage(tg.scan(tmp_path)), "2026-08-31T00:00:00Z")
    by_id = {r["trial_id"]: r for r in records}
    assert by_id["t-gen-b"]["parent_trial_id"] == "t-gen-a"


def test_every_candidate_gets_a_config_ref_so_the_frontier_clears(tmp_path):
    """harness 的前沿是靠 config_ref 比對來判定「已嘗試」的。

    漏掉 config_ref 的候選會永遠留在前沿上，讓分診看起來沒有發生。
    """
    _make(tmp_path, "a", keywords=("momentum",))
    records = tg.build_records(tg.triage(tg.scan(tmp_path)), "2026-08-31T00:00:00Z")
    leaves = [r for r in records if r["trial_id"].startswith("t-gen-")]
    assert leaves
    for rec in leaves:
        assert rec["config_ref"].endswith("manifest.yaml")


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    """預設不得寫 ledger。分診會一次寫進數百筆，誤觸的代價高。"""
    ledger = tmp_path / "log" / "trials.jsonl"
    monkeypatch.setattr(tg, "GENERATED", tmp_path / "gen")
    monkeypatch.setattr(tg, "LEDGER", ledger)
    (tmp_path / "gen").mkdir()
    _make(tmp_path / "gen", "a", keywords=("momentum",))
    assert tg.main(["--baseline-n", "33"]) == 0
    assert not ledger.exists()
    assert "dry-run" in capsys.readouterr().out


def test_count_existing_selection_trials_uses_latest_line_per_trial_id(tmp_path):
    from scripts.triage_generated import count_existing_selection_trials
    path = tmp_path / "trials.jsonl"
    lines = [
        {"trial_id": "t-a", "stat_decision": {"delta_n": 1}},
        {"trial_id": "t-a", "stat_decision": {"delta_n": 1, "p_value": 0.01}},
        {"trial_id": "t-b", "stat_decision": {"delta_n": 1}},
        {"trial_id": "t-c", "stat_decision": {"delta_n": 0}},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert count_existing_selection_trials(path) == 2
