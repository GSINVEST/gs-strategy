# tests/test_idea_triage.py
from quant_crawler.ideas.card import IdeaCard
from quant_crawler.ideas.triage import apply_triage, normalize_signature, triage


def _c(**kw):
    base = dict(id="x_1", source="s", title="t",
                hypothesis="TX 收盤站上 200 日均線做多，跌破出場",
                market="TX futures", horizon="daily", signature="sma trend tx")
    base.update(kw)
    return IdeaCard(**base)


def test_ok_is_triaged():
    r = triage(_c(), set())
    assert r.status == "triaged"


def test_no_hypothesis_dropped():
    r = triage(_c(hypothesis="  "), set())
    assert r.status == "drop" and "假說" in r.reason


def test_duplicate_signature_dropped():
    sigs = {normalize_signature("SMA trend TX")}
    r = triage(_c(signature="TX trend sma"), sigs)
    assert r.status == "drop" and "重複" in r.reason


def test_signature_normalization_order_and_case():
    assert normalize_signature("B a  A") == normalize_signature("a b")


def test_cross_sectional_deferred():
    r = triage(_c(hypothesis="cross-sectional momentum ranking across futures"), set())
    assert r.status == "defer" and r.resurrect_when


def test_stop_loss_deferred():
    r = triage(_c(mechanism="使用 2 ATR 移動停損出場"), set())
    assert r.status == "defer"


def test_intraday_deferred():
    r = triage(_c(horizon="5-minute intraday"), set())
    assert r.status == "defer"


def test_stock_market_deferred():
    r = triage(_c(market="Taiwan single stocks long/short"), set())
    assert r.status == "defer"


def test_apply_triage_updates_card():
    c = _c()
    apply_triage(c, set())
    assert c.status == "triaged" and c.fsm_expressible is True
    d = _c(id="x_2", hypothesis="")
    apply_triage(d, set())
    assert d.status == "drop"
