# tests/test_idea_batch.py
import json

import pytest
import yaml

from quant_crawler.ideas.batch import MAX_BATCH, register_batch, select_batch
from quant_crawler.ideas.card import IdeaCard, set_status


def _impl(i, verdict="pass_with_bounds"):
    c = IdeaCard(id=f"c_{i:03d}", source="s", title="t", hypothesis=f"h{i}", market="TX futures",
                 verdict=verdict)
    for s in ("triaged", "fsm", "impl"):
        set_status(c, s)
    return c


def test_only_passing_impl_selected():
    cards = [_impl(1), _impl(2, "counterexample"), _impl(3, "unchecked")]
    assert [c.id for c in select_batch(cards)] == ["c_001"]


def test_cap_enforced():
    cards = [_impl(i) for i in range(30)]
    assert len(select_batch(cards)) == MAX_BATCH
    with pytest.raises(ValueError):
        select_batch(cards, limit=MAX_BATCH + 1)


def test_order_independent_of_input_order():
    cards = [_impl(i) for i in range(30)]
    a = [c.id for c in select_batch(cards)]
    b = [c.id for c in select_batch(list(reversed(cards)))]
    assert a == b


def test_register_appends_and_drafts(tmp_path):
    ledger = tmp_path / "trials.jsonl"
    ledger.write_text('{"schema":"research-trial/v1","trial_id":"old"}\n', encoding="utf-8")
    cards = select_batch([_impl(i) for i in range(3)])
    info = register_batch(cards, tmp_path / "batches", "2026-11-12T00:00:00Z", ledger,
                          tmp_path / "prereg")
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"schema":"research-trial/v1","trial_id":"old"}'     # 既有行不動
    new = [json.loads(x) for x in lines[1:]]
    assert len(new) == 1 + 3                                                  # 根 + 3 成員
    assert sum(1 for r in new if r["stat_decision"]["delta_n"] == 1) == 3
    assert info["n_trials"] == 3 and all(c.status == "bt_registered" for c in cards)
    pre = yaml.safe_load((tmp_path / "prereg" / f"{info['batch_id']}.yaml").read_text(encoding="utf-8"))
    assert pre["schema"] == "preregistration/1" and pre["author"] == ""
    assert pre["stat_plan"]["family"] == "bounded_multiple" and pre["stat_plan"]["n_trials"] == 3


def test_register_twice_refused(tmp_path):
    cards = select_batch([_impl(1)])
    args = (tmp_path / "b", "2026-11-12T00:00:00Z", tmp_path / "t.jsonl", tmp_path / "p")
    register_batch(cards, *args)
    with pytest.raises(FileExistsError):
        register_batch([_impl(2)], *args)


# ---------------------------------------------------------------------------
# 審查修正：寫入前全部驗證、失敗零副作用、ledger 結尾換行、limit 下界
# ---------------------------------------------------------------------------

NOW = "2026-11-12T00:00:00Z"


def _paths(tmp_path):
    return dict(root=tmp_path / "b", ledger=tmp_path / "t.jsonl", prereg_dir=tmp_path / "p")


def test_select_rejects_nonpositive_limit():
    with pytest.raises(ValueError):
        select_batch([_impl(1)], limit=0)
    with pytest.raises(ValueError):
        select_batch([_impl(1)], limit=-1)


def test_register_refuses_non_impl_card_without_side_effects(tmp_path):
    ledger = tmp_path / "t.jsonl"
    ledger.write_bytes(b'{"trial_id":"old"}\n')
    good = _impl(1)
    bad = IdeaCard(id="c_009", source="s", title="t", hypothesis="h", verdict="pass_with_bounds")
    p = _paths(tmp_path)
    with pytest.raises(ValueError):
        register_batch([good, bad], p["root"], NOW, ledger, p["prereg_dir"], batch_id="bx")
    assert ledger.read_bytes() == b'{"trial_id":"old"}\n'
    assert good.status == "impl" and good.batch_id == ""
    assert not (p["root"] / "bx.json").exists()
    assert not (p["prereg_dir"] / "bx.yaml").exists()


def test_register_refuses_failed_verdict_without_side_effects(tmp_path):
    ledger = tmp_path / "t.jsonl"
    c = _impl(1, verdict="counterexample")
    p = _paths(tmp_path)
    with pytest.raises(ValueError):
        register_batch([c], p["root"], NOW, ledger, p["prereg_dir"], batch_id="bx")
    assert not ledger.exists() and c.status == "impl"


def test_register_refuses_duplicate_card_without_side_effects(tmp_path):
    ledger = tmp_path / "t.jsonl"
    p = _paths(tmp_path)
    a, b = _impl(1), _impl(1)
    with pytest.raises(ValueError):
        register_batch([a, b], p["root"], NOW, ledger, p["prereg_dir"], batch_id="bx")
    assert not ledger.exists() and a.status == "impl" and b.status == "impl"
    assert not (p["root"] / "bx.json").exists()


def test_register_refuses_card_already_in_ledger(tmp_path):
    """同一張卡不得登記到第二個批次（否則 delta_n 重複計入 N）。"""
    ledger = tmp_path / "t.jsonl"
    p = _paths(tmp_path)
    register_batch([_impl(1)], p["root"], NOW, ledger, p["prereg_dir"], batch_id="first")
    before = ledger.read_bytes()
    c2 = _impl(1)
    c3 = _impl(3)
    with pytest.raises(ValueError):
        register_batch([c2, c3], p["root"], NOW, ledger, p["prereg_dir"], batch_id="second")
    assert ledger.read_bytes() == before
    assert c3.status == "impl" and c3.batch_id == ""
    assert not (p["root"] / "second.json").exists()
    assert not (p["prereg_dir"] / "second.yaml").exists()


def test_ledger_scan_tolerates_corrupt_lines(tmp_path):
    ledger = tmp_path / "t.jsonl"
    ledger.write_bytes(b'not json\n[1,2]\n{"trial_id":"old"}\n')
    p = _paths(tmp_path)
    info = register_batch([_impl(2)], p["root"], NOW, ledger, p["prereg_dir"], batch_id="bx")
    assert info["n_trials"] == 1
    assert ledger.read_bytes().startswith(b'not json\n[1,2]\n{"trial_id":"old"}\n')


def test_register_refuses_existing_prereg_file(tmp_path):
    ledger = tmp_path / "t.jsonl"
    p = _paths(tmp_path)
    p["prereg_dir"].mkdir()
    (p["prereg_dir"] / "bx.yaml").write_text("x: 1\n", encoding="utf-8")
    c = _impl(1)
    with pytest.raises(FileExistsError):
        register_batch([c], p["root"], NOW, ledger, p["prereg_dir"], batch_id="bx")
    assert not ledger.exists() and c.status == "impl"
    assert (p["prereg_dir"] / "bx.yaml").read_text(encoding="utf-8") == "x: 1\n"


def test_mkdir_failure_leaves_ledger_and_cards_untouched(tmp_path):
    """prereg_dir 是一個檔案，mkdir 必然失敗：ledger 不得被追加、卡片不得轉狀態。"""
    ledger = tmp_path / "t.jsonl"
    ledger.write_bytes(b'{"trial_id":"old"}\n')
    blocker = tmp_path / "p"
    blocker.write_text("i am a file", encoding="utf-8")
    c = _impl(1)
    with pytest.raises(OSError):
        register_batch([c], tmp_path / "b", NOW, ledger, blocker, batch_id="bx")
    assert ledger.read_bytes() == b'{"trial_id":"old"}\n'
    assert c.status == "impl" and c.batch_id == ""
    assert not (tmp_path / "b" / "bx.json").exists()


def test_ledger_append_failure_rolls_back_batch_files(tmp_path, monkeypatch):
    """批次檔與 prereg 草稿寫完後、ledger 追加失敗：兩個檔案必須被刪除，卡片不轉狀態。"""
    import quant_crawler.ideas.batch as batch_mod

    def boom(ledger, records):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(batch_mod, "_append_lines", boom)
    ledger = tmp_path / "t.jsonl"
    p = _paths(tmp_path)
    c = _impl(1)
    with pytest.raises(OSError):
        register_batch([c], p["root"], NOW, ledger, p["prereg_dir"], batch_id="bx")
    assert not (p["root"] / "bx.json").exists()
    assert not (p["prereg_dir"] / "bx.yaml").exists()
    assert c.status == "impl" and c.batch_id == ""


def test_ledger_without_trailing_newline_is_repaired(tmp_path):
    ledger = tmp_path / "t.jsonl"
    original = b'{"trial_id":"old"}'            # 結尾沒有換行
    ledger.write_bytes(original)
    p = _paths(tmp_path)
    register_batch([_impl(1)], p["root"], NOW, ledger, p["prereg_dir"], batch_id="bx")
    data = ledger.read_bytes()
    assert data.startswith(original + b"\n")     # 既有內容逐位元組不變，只多了換行
    lines = data.decode("utf-8").splitlines()
    assert lines[0] == '{"trial_id":"old"}'
    recs = [json.loads(x) for x in lines[1:]]    # 每一行都必須可解析（沒有黏行）
    assert len(recs) == 2 and all(r["schema"] == "research-trial/v1" for r in recs)


def test_ledger_with_trailing_newline_gets_no_extra_blank_line(tmp_path):
    ledger = tmp_path / "t.jsonl"
    original = b'{"trial_id":"old"}\n'
    ledger.write_bytes(original)
    p = _paths(tmp_path)
    register_batch([_impl(1)], p["root"], NOW, ledger, p["prereg_dir"], batch_id="bx")
    data = ledger.read_bytes()
    assert data.startswith(original) and b"\n\n" not in data


def test_card_state_updated_only_after_success(tmp_path):
    ledger = tmp_path / "t.jsonl"
    p = _paths(tmp_path)
    c = _impl(1)
    cards_dir = tmp_path / "cards"
    register_batch([c], p["root"], NOW, ledger, p["prereg_dir"], batch_id="bx", cards_dir=cards_dir)
    assert c.status == "bt_registered" and c.batch_id == "bx"
    assert (cards_dir / "c_001.md").exists()


def test_default_batch_id_is_date_based(tmp_path):
    p = _paths(tmp_path)
    info = register_batch([_impl(1)], p["root"], NOW, p["ledger"], p["prereg_dir"])
    assert info["batch_id"] == "idea-batch-2026-11-12"
