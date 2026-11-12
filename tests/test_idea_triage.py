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


# --- fix round 1：誤判與漏判的正負對照 ---

def test_trailing_return_is_not_trailing_stop():
    r = triage(_c(hypothesis="trailing 12-month return 為正時做多 TX"), set())
    assert r.status == "triaged"


def test_trailing_stop_still_deferred():
    r = triage(_c(hypothesis="trailing stop 2 ATR 出場"), set())
    assert r.status == "defer"


def test_repair_is_not_pairs():
    r = triage(_c(hypothesis="均線 repair 型態後做多 TX"), set())
    assert r.status == "triaged"


def test_stick_is_not_tick():
    r = triage(_c(hypothesis="價格黏著 stick 於均線上方時做多 TX"), set())
    assert r.status == "triaged"


def test_frank_is_not_rank():
    r = triage(_c(hypothesis="frank 式均線規則做多 TX"), set())
    assert r.status == "triaged"


def test_multi_signal_combination_not_cross_sectional():
    r = triage(_c(hypothesis="多種訊號組合後站上均線做多 TX"), set())
    assert r.status == "triaged"


def test_optimization_word_alone_not_deferred():
    r = triage(_c(hypothesis="引用 optimization 文獻的均線規則做多 TX"), set())
    assert r.status == "triaged"


def test_grid_search_and_param_optimization_deferred():
    assert triage(_c(hypothesis="用 grid search 找均線長度做多 TX"), set()).status == "defer"
    assert triage(_c(hypothesis="以參數優化決定均線長度做多 TX"), set()).status == "defer"


def test_crosses_above_deferred():
    r = triage(_c(hypothesis="Long when close crosses above SMA"), set())
    assert r.status == "defer"
