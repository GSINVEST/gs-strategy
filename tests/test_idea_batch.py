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
