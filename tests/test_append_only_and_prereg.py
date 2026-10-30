"""pre-registration 不可改寫、ledger 只准追加——這兩道閘門的測試。

這兩件事都可以寫在文件裡請人遵守。測試存在的理由是：它們要擋的正是「請人遵守」
擋不住的情境——結果不如預期、時間壓力大、而改一行就能讓數字好看。所以每個
測試都對應一種**具體的作弊動作**，而不只是對應一個函式。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import check_append_only as cao          # noqa: E402
from scripts import check_preregistration as cpr      # noqa: E402


# ---------------------------------------------------------------------------
# 在一個乾淨的暫存 repo 上跑，不碰真的 repo
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, check=False)
    return r.stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "experiments" / "preregistration").mkdir(parents=True)
    (tmp_path / "log").mkdir()
    (tmp_path / "experiments" / "preregistration" / "001-a.yaml").write_text(
        "id: 001-a\n", encoding="utf-8")
    (tmp_path / "log" / "trials.jsonl").write_text(
        '{"trial_id":"t-1"}\n{"trial_id":"t-2"}\n', encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _stage(repo: Path, rel: str, content: str) -> None:
    (repo / rel).write_text(content, encoding="utf-8")
    _git(repo, "add", rel)


# ---------------------------------------------------------------------------
# 一、pre-registration 凍結
# ---------------------------------------------------------------------------

def test_adding_a_new_preregistration_is_allowed(repo):
    """新增永遠可以——凍結的是既有的那些，不是這個目錄。"""
    _stage(repo, "experiments/preregistration/002-b.yaml", "id: 002-b\n")
    assert cao.check() == []


def test_editing_an_existing_preregistration_is_blocked(repo):
    """看完結果再回頭改判準——這是這道閘門的頭號目標。"""
    _stage(repo, "experiments/preregistration/001-a.yaml",
           "id: 001-a\nhypothesis: 改成一個我知道會顯著的\n")
    v = cao.check()
    assert len(v) == 1
    assert "不可修改" in v[0].kind
    assert "取代了哪一份" in v[0].detail


def test_deleting_an_existing_preregistration_is_blocked(repo):
    (repo / "experiments" / "preregistration" / "001-a.yaml").unlink()
    _git(repo, "add", "-A")
    v = cao.check()
    assert len(v) == 1 and "不可刪除" in v[0].kind


def test_renaming_an_existing_preregistration_is_blocked(repo):
    """改名是刪除加新增的偽裝，一樣要擋。"""
    _git(repo, "mv", "experiments/preregistration/001-a.yaml",
         "experiments/preregistration/009-a.yaml")
    v = cao.check()
    assert v and any("改名" in x.kind for x in v)


def test_unrelated_files_are_untouched(repo):
    _stage(repo, "notes.md", "隨便寫\n")
    assert cao.check() == []


def test_readme_and_template_in_the_prereg_dir_stay_editable(repo):
    """凍結的是登記檔，不是那個目錄。

    第一版把整個目錄凍住，端到端測試立刻撞上——連 README 都改不了。那沒有
    保護到任何統計宣稱，只會逼人養成 --no-verify 的習慣，而那才是真正的損失。
    """
    _stage(repo, "experiments/preregistration/README.md", "說明改一改\n")
    _stage(repo, "experiments/preregistration/TEMPLATE.yaml", "schema: x\n")
    assert cao.check() == []


def test_frozen_predicate_is_narrow_and_explicit():
    assert cao.is_frozen_registration("experiments/preregistration/001-a.yaml")
    assert not cao.is_frozen_registration("experiments/preregistration/README.md")
    assert not cao.is_frozen_registration("experiments/preregistration/TEMPLATE.yaml")
    assert not cao.is_frozen_registration("experiments/other/001-a.yaml")
    assert not cao.is_frozen_registration("experiments/preregistration/notes.txt")


# ---------------------------------------------------------------------------
# 二、ledger 只准追加
# ---------------------------------------------------------------------------

def test_appending_to_the_ledger_is_allowed(repo):
    _stage(repo, "log/trials.jsonl",
           '{"trial_id":"t-1"}\n{"trial_id":"t-2"}\n{"trial_id":"t-3"}\n')
    assert cao.check() == []


def test_rewriting_an_existing_ledger_line_is_blocked(repo):
    """把一次失敗的嘗試改成別的東西，好讓它不算進 N。"""
    _stage(repo, "log/trials.jsonl",
           '{"trial_id":"t-1"}\n{"trial_id":"t-2-EDITED"}\n')
    v = cao.check()
    assert len(v) == 1
    assert "第 2 行被改寫" in v[0].kind


def test_truncating_the_ledger_is_blocked(repo):
    """直接刪掉幾行讓 N 變小、DSR 變好看——最直接的一種作弊。"""
    _stage(repo, "log/trials.jsonl", '{"trial_id":"t-1"}\n')
    v = cao.check()
    assert len(v) == 1
    assert "被截短" in v[0].kind
    assert "DSR" in v[0].detail


def test_deleting_the_ledger_is_blocked(repo):
    (repo / "log" / "trials.jsonl").unlink()
    _git(repo, "add", "-A")
    v = cao.check()
    assert v and "不可刪除或改名" in v[0].kind


def test_creating_the_ledger_for_the_first_time_is_allowed(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "seed.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "log").mkdir()
    monkeypatch.chdir(tmp_path)
    _stage(tmp_path, "log/trials.jsonl", '{"trial_id":"t-1"}\n')
    assert cao.check() == []


def test_cli_exit_code_is_the_gate(repo):
    """閘門是 exit code，不是印出來的字——CI 與 hook 都只看它。"""
    _stage(repo, "log/trials.jsonl", '{"trial_id":"t-1"}\n')
    assert cao.main([]) == 1
    _stage(repo, "log/trials.jsonl",
           '{"trial_id":"t-1"}\n{"trial_id":"t-2"}\n{"trial_id":"t-3"}\n')
    assert cao.main([]) == 0


# ---------------------------------------------------------------------------
# 三、pre-registration 內容驗證
# ---------------------------------------------------------------------------

def _prereg(**over) -> dict:
    doc = {
        "schema": "preregistration/1",
        "id": "001-x",
        "title": "t",
        "author": "a",
        "registered_at": "2026-09-01T00:00:00Z",
        "hypothesis": "TX 的 5 日動能延續",
        "economic_mechanism": "散戶追漲殺跌造成短期反應不足",
        "falsification": ["IC 不顯著"],
        "sample": {"contains_full_bear_market": True},
        "stat_plan": {
            "estimand": "rank_ic", "design": "one_sample",
            "overlap": "overlapping", "holding_periods": 5,
            "family": "bounded_multiple", "n_trials": 9,
            "purpose": "selection",
        },
    }
    doc.update(over)
    return doc


def _write(tmp_path: Path, doc: dict, name="001-x.yaml") -> Path:
    p = tmp_path / name
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    return p


def test_a_complete_preregistration_passes(tmp_path):
    assert cpr.validate(_write(tmp_path, _prereg())) == []


def test_stat_plan_that_cannot_resolve_a_test_is_rejected(tmp_path):
    """看起來填滿、但推導不出檢定的登記檔最危險——它給人一種已經綁死的錯覺。

    這裡 overlap 宣告 overlapping 卻沒給 holding_periods，Newey-West 的 lag
    就定不下來，等於檢定還沒選完。
    """
    doc = _prereg()
    doc["stat_plan"].pop("holding_periods")
    problems = cpr.validate(_write(tmp_path, doc))
    assert any("推導不出檢定處方" in p for p in problems)


def test_empty_falsification_is_rejected(tmp_path):
    problems = cpr.validate(_write(tmp_path, _prereg(falsification=[])))
    assert any("falsification" in p for p in problems)


def test_open_mining_without_a_p_value_definition_is_rejected(tmp_path):
    doc = _prereg()
    doc["stat_plan"]["family"] = "open_mining"
    doc["stat_plan"].pop("n_trials")
    problems = cpr.validate(_write(tmp_path, doc))
    assert any("p_value_definition" in p for p in problems)


def test_online_fdr_without_a_floor_is_rejected(tmp_path):
    """floor 是「多小的 p 值還當真」的判斷，沒有預設值可套。"""
    doc = _prereg(online_fdr={"enabled": True, "alpha": 0.05,
                              "exhausted_floor": None})
    problems = cpr.validate(_write(tmp_path, doc))
    assert any("exhausted_floor" in p for p in problems)


def test_unfilled_bear_market_field_is_not_treated_as_compliant(tmp_path):
    """未填不等於符合。留 None 會讓「樣本期含空頭」變成預設通過。"""
    doc = _prereg(sample={"contains_full_bear_market": None})
    problems = cpr.validate(_write(tmp_path, doc))
    assert any("contains_full_bear_market" in p for p in problems)


def test_id_must_match_filename(tmp_path):
    problems = cpr.validate(_write(tmp_path, _prereg(), name="002-y.yaml"))
    assert any("與檔名" in p for p in problems)


def test_shipped_template_is_not_mistaken_for_a_real_registration(tmp_path):
    """TEMPLATE.yaml 欄位是空的，驗證器不該把它當成一份未通過的登記檔。"""
    assert cpr.main([]) == 0


# ---------------------------------------------------------------------------
# 四、CI 的 --rev-range 模式與提交佇列的保護路徑
# ---------------------------------------------------------------------------

def _commit_ledger(repo: Path, content: str) -> None:
    (repo / "log" / "trials.jsonl").write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ledger")


def test_rev_range_catches_a_rewritten_ledger_line(repo):
    """CI 檢查的是已 commit 的範圍；index 與 HEAD 相同，不能拿 index 當「改後」。"""
    _commit_ledger(repo, '{"trial_id":"t-1"}\n{"trial_id":"t-2-EDITED"}\n')
    v = cao.check("HEAD~1..HEAD", base="HEAD~1")
    assert len(v) == 1 and "第 2 行被改寫" in v[0].kind


def test_rev_range_allows_append_and_catches_truncation(repo):
    _commit_ledger(repo, '{"trial_id":"t-1"}\n{"trial_id":"t-2"}\n{"trial_id":"t-3"}\n')
    assert cao.check("HEAD~1..HEAD", base="HEAD~1") == []
    _commit_ledger(repo, '{"trial_id":"t-1"}\n')
    v = cao.check("HEAD~1..HEAD", base="HEAD~1")
    assert len(v) == 1 and "被截短" in v[0].kind


def test_rev_range_cli_derives_base_from_the_range(repo):
    _commit_ledger(repo, '{"trial_id":"t-1"}\n{"trial_id":"t-2-EDITED"}\n')
    assert cao.main(["--rev-range", "HEAD~1..HEAD"]) == 1


def test_submission_event_log_is_append_only(repo):
    (repo / "log" / "submissions.jsonl").write_text('{"e":1}\n{"e":2}\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "events")
    _stage(repo, "log/submissions.jsonl", '{"e":1}\n{"e":"EDITED"}\n')
    v = cao.check()
    assert len(v) == 1 and "第 2 行被改寫" in v[0].kind


def test_submitted_bundles_are_write_once(repo):
    (repo / "strategies" / "_submitted" / "s-abc").mkdir(parents=True)
    f = "strategies/_submitted/s-abc/strategy.py"
    _stage(repo, f, "x = 1\n")
    assert cao.check() == []
    _git(repo, "commit", "-q", "-m", "bundle")
    _stage(repo, f, "x = 2\n")
    v = cao.check()
    assert len(v) == 1 and "不可修改" in v[0].kind
    assert cao.is_frozen_submission(f)
    assert not cao.is_frozen_submission("strategies/_generated/a/strategy.py")
