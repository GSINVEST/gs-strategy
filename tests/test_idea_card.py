# tests/test_idea_card.py
import pytest
from quant_crawler.ideas.card import (
    IdeaCard, save_card, load_card, iter_cards, set_status, slug,
)


def _card(**kw):
    base = dict(id="arxiv_2605_04004", source="arxiv:2605.04004", title="T",
                hypothesis="TX 收盤突破 200 日均線後續漲", market="TX futures")
    base.update(kw)
    return IdeaCard(**base)


def test_roundtrip(tmp_path):
    c = _card(quotes=[{"field": "hypothesis", "text": "breakout"}], flaws=["樣本短"])
    p = save_card(c, tmp_path)
    assert p.name == "arxiv_2605_04004.md"
    back = load_card(p)
    assert back == c


def test_iter_cards_sorted(tmp_path):
    save_card(_card(id="b_1"), tmp_path)
    save_card(_card(id="a_1"), tmp_path)
    assert [c.id for c in iter_cards(tmp_path)] == ["a_1", "b_1"]


def test_slug():
    assert slug("arXiv:2605.04004v2") == "arxiv_2605_04004v2"
    assert slug("A  B/C") == "a_b_c"


def test_id_must_be_slug():
    with pytest.raises(ValueError):
        _card(id="Bad Id")


def test_status_transitions():
    c = _card()
    set_status(c, "triaged", "ok")
    assert c.status == "triaged" and c.reason == "ok"
    set_status(c, "fsm")
    with pytest.raises(ValueError):
        set_status(c, "new")          # 不得倒退
    with pytest.raises(ValueError):
        set_status(c, "admitted")     # 不得跳級（fsm 之後要先 impl）


def test_defer_and_drop_are_terminal():
    c = _card()
    set_status(c, "defer", "非單一期貨")
    with pytest.raises(ValueError):
        set_status(c, "triaged")
