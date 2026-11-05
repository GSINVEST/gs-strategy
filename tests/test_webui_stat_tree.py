"""/stat-tree 面板的測試。

面板的價值在於它讀的是**現在的檔案**而不是快照，所以測試的重點是：
資料真的來自那些檔案、而且來源不存在時它會**說不可用**而不是回 0。
把「還沒有」顯示成 0，會讓一個從未跑過分診的 repo 看起來像一個分診完發現
沒東西的 repo——這正是整套設計要防的錯誤形狀，所以要釘住。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from quant_crawler.webui import stat_tree  # noqa: E402


# ---------------------------------------------------------------------------
# 來源不存在時要說不可用，不要回 0
# ---------------------------------------------------------------------------

def test_missing_ledger_is_reported_as_unavailable_not_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(stat_tree, "LEDGER", tmp_path / "nope.jsonl")
    out = stat_tree.ledger_summary()
    assert out["available"] is False
    assert "還不存在" in out["reason"]
    assert "n_trials" not in out          # 不得混進一個看起來像真的 0


def test_missing_ruleset_dir_is_reported_as_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(stat_tree, "RULESET_DIR", tmp_path / "nope")
    out = stat_tree.ruleset_summary()
    assert out["available"] is False


def test_missing_prereg_dir_is_reported_as_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(stat_tree, "PREREG_DIR", tmp_path / "nope")
    out = stat_tree.preregistration_summary()
    assert out["available"] is False


def test_empty_prereg_dir_is_available_but_says_what_that_implies(tmp_path, monkeypatch):
    """空目錄不是錯，但它有後果：沒有登記就全部只能算探索性發現。

    「可用但是 0」與「不可用」是兩件事，這裡要能分得出來。
    """
    (tmp_path / "TEMPLATE.yaml").write_text("schema: preregistration/1\n",
                                            encoding="utf-8")
    monkeypatch.setattr(stat_tree, "PREREG_DIR", tmp_path)
    out = stat_tree.preregistration_summary()
    assert out["available"] is True
    assert out["n"] == 0
    assert "探索性發現" in out["note"]


# ---------------------------------------------------------------------------
# 母體與 benchmark
# ---------------------------------------------------------------------------

def _ledger(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "trials.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                 encoding="utf-8")
    return p


def test_only_selection_trials_count_towards_n(tmp_path, monkeypatch):
    recs = [
        {"trial_id": "a", "disposition": "adopted",
         "stat_decision": {"delta_n": 1}},
        {"trial_id": "b", "disposition": "excluded:無假說骨架",
         "stat_decision": {"delta_n": 0, "no_statistical_claim": True}},
        {"trial_id": "c", "disposition": "rejected",
         "stat_decision": {"delta_n": 1}},
    ]
    monkeypatch.setattr(stat_tree, "LEDGER", _ledger(tmp_path, recs))
    monkeypatch.setattr(stat_tree, "GENERATED", tmp_path / "none")
    out = stat_tree.ledger_summary()
    assert out["n_trials"] == 3
    assert out["n_selection"] == 2
    assert out["n_no_statistical_claim"] == 1


def test_same_trial_id_takes_the_last_record(tmp_path, monkeypatch):
    """與 harness 的 collect() 一致：重跑分診會產生同 id 的新行，取最後一筆。"""
    recs = [
        {"trial_id": "a", "disposition": "候選", "stat_decision": {"delta_n": 0}},
        {"trial_id": "a", "disposition": "excluded:與既有候選同組態",
         "stat_decision": {"delta_n": 0}},
    ]
    monkeypatch.setattr(stat_tree, "LEDGER", _ledger(tmp_path, recs))
    monkeypatch.setattr(stat_tree, "GENERATED", tmp_path / "none")
    out = stat_tree.ledger_summary()
    assert out["n_trials"] == 1
    assert out["dispositions"] == {"excluded:與既有候選同組態": 1}


def test_benchmark_is_undefined_below_two_trials_rather_than_zero(tmp_path, monkeypatch):
    """N < 2 時 E[max SR] 沒有定義。回 0 會被讀成「門檻很低」，那是相反的意思。"""
    monkeypatch.setattr(stat_tree, "LEDGER",
                        _ledger(tmp_path, [{"trial_id": "a",
                                            "stat_decision": {"delta_n": 1}}]))
    monkeypatch.setattr(stat_tree, "GENERATED", tmp_path / "none")
    b = stat_tree.ledger_summary()["deflation_benchmark"]
    assert b["n_selection"] == 1
    assert b["current"] is None


def test_benchmark_rises_with_the_hypothetical_frontier(tmp_path, monkeypatch):
    """「把前沿全部跑完」的代價要能被算出來——那是這個面板最該說的一件事。"""
    recs = [{"trial_id": f"t{i}", "stat_decision": {"delta_n": 1}} for i in range(40)]
    monkeypatch.setattr(stat_tree, "LEDGER", _ledger(tmp_path, recs))
    gen = tmp_path / "gen"
    for i in range(60):
        d = gen / f"c{i}"
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text("id: x\n", encoding="utf-8")
    monkeypatch.setattr(stat_tree, "GENERATED", gen)
    monkeypatch.setattr(stat_tree, "REPO", tmp_path)
    b = stat_tree.ledger_summary()["deflation_benchmark"]
    assert b["hypothetical_n"] == 100
    assert b["if_frontier_all_run"] > b["current"]


# ---------------------------------------------------------------------------
# 整體 payload 與路由
# ---------------------------------------------------------------------------

def test_payload_has_every_block_and_each_declares_availability():
    out = stat_tree.payload()
    for key in ("ruleset", "ledger", "audit", "preregistration", "online_fdr"):
        assert key in out, key
        assert "available" in out[key], f"{key} 沒有宣告 available"


def test_payload_is_json_serialisable():
    """面板走 JSON；有東西序列化不了會在瀏覽器端才炸，這裡先擋。"""
    json.dumps(stat_tree.payload(), ensure_ascii=False)


def test_ruleset_block_reflects_the_actual_yaml():
    out = stat_tree.ruleset_summary()
    assert out["available"] is True
    # 頁面讀的是目錄裡排序最後（最新）的規則集，不是 decision.py 的預設版本；版本號跟著檔案走
    latest = sorted(stat_tree.RULESET_DIR.glob("stat-ruleset-*.yaml"))[-1]
    assert out["version"] == latest.stem.removeprefix("stat-ruleset-")
    # 規則集是這一頁的權威來源，不是硬編在頁面上的表格
    ids = {b["id"] for b in out["base"]}
    assert "mean_return.paired" in ids
    assert "rank_ic.one_sample" in ids
    thr_ids = {t["id"] for t in out["thresholds"]}
    assert {"thr.single", "thr.bonferroni", "thr.hlz",
            "thr.mining", "thr.best_of_m"} <= thr_ids


def test_stat_tree_routes_are_registered():
    """路由漏掉的話頁面 404，但單元測試照樣全綠——所以要直接驗原始碼。"""
    src = (REPO / "quant_crawler" / "webui" / "server.py").read_text(encoding="utf-8")
    assert '"/stat-tree"' in src
    assert '"/api/stat-tree"' in src


def test_page_asset_exists_and_fetches_the_live_api():
    page = REPO / "quant_crawler" / "webui" / "static" / "stat-tree.html"
    assert page.is_file()
    html = page.read_text(encoding="utf-8")
    assert "/api/stat-tree" in html, "頁面必須讀即時 API，不能是靜態快照"
    assert "gs-theme.css" in html, "配色要走既有 theme，不要自己一套"
