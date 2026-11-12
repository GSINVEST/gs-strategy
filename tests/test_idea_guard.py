# tests/test_idea_guard.py
import pytest

from quant_crawler.ideas.guard import GuardError, evaluate, to_rust, tokenize


def ev(src, **env):
    return evaluate(tokenize(src), env)


def test_comparison_and_logic():
    assert ev("a == HIGH and position == 0", a=3, HIGH=3, position=0) is True
    assert ev("a == HIGH or position > 0", a=1, HIGH=3, position=0) is False
    assert ev("not (a == LOW)", a=3, LOW=1) is True


def test_arithmetic_precedence():
    assert ev("x + 2 * y", x=1, y=3) == 7
    assert ev("(x + 2) * y", x=1, y=3) == 9


def test_unknown_identifier_rejected():
    with pytest.raises(GuardError):
        ev("secret == 1")


def test_forbidden_token_rejected():
    for bad in ("__import__('os')", "a.b", "a; b"):
        with pytest.raises(GuardError):
            tokenize(bad)


def test_to_rust():
    t = tokenize("trend_state == HIGH and position == 0")
    r = to_rust(t, {"trend_state": "sig_trend_state", "HIGH": "Z_HIGH", "position": "pos"})
    assert r == "sig_trend_state == Z_HIGH && pos == 0.0"
    assert to_rust(tokenize("not a"), {"a": "x"}) == "!x"


def test_not_binds_looser_than_comparison():
    m = {"a": "x", "b": "y"}
    assert to_rust(tokenize("not a == b"), m) == "!(x == y)"
    assert to_rust(tokenize("(not a) == b"), m) == "!x == y"
    assert ev("not a == b", a=3, b=3) is False
    assert ev("not a == b", a=3, b=4) is True


def test_to_rust_parenthesizes_by_precedence():
    m = {"a": "x", "b": "y", "c": "z"}
    assert to_rust(tokenize("a - (b - c)"), m) == "x - (y - z)"
    assert to_rust(tokenize("(a or b) and c"), m) == "(x || y) && z"
    assert to_rust(tokenize("a or b and c"), m) == "x || y && z"
    assert to_rust(tokenize("(a + b) * c"), m) == "(x + y) * z"


@pytest.mark.parametrize("bad", ["- 1", "a ==", "a b", "(a", "a )", "and", "a == b == c", "not", ""])
def test_to_rust_rejects_what_evaluate_rejects(bad):
    env = {"a": 1, "b": 2, "c": 3}
    with pytest.raises(GuardError):
        ev(bad, **env)
    with pytest.raises(GuardError):
        to_rust(tokenize(bad), {k: k for k in env})


def test_to_rust_unknown_identifier():
    with pytest.raises(GuardError):
        to_rust(tokenize("a == zz"), {"a": "x"})
