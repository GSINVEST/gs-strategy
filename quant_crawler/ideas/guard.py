# quant_crawler/ideas/guard.py
"""守衛運算式：tokenizer、遞迴下降求值、Rust 運算式翻譯。不使用 eval。

文法：or < and < not < 比較(== != < <= > >=) < 加減 < 乘 < 原子（數字｜識別字｜括號）
"""
from __future__ import annotations

import re


class GuardError(ValueError):
    pass


_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(==|!=|<=|>=|[<>()+\-*]))")
_KW = {"and", "or", "not"}


def tokenize(src: str) -> list[str]:
    out, i = [], 0
    src = src.strip()
    while i < len(src):
        m = _TOKEN.match(src, i)
        if not m or m.end() == i:
            raise GuardError(f"不允許的字元：{src[i:i+10]!r}")
        out.append(m.group(1) or m.group(2) or m.group(3))
        i = m.end()
        while i < len(src) and src[i].isspace():
            i += 1
    return out


_NUM = re.compile(r"\d+(?:\.\d+)?")
_ID = re.compile(r"[A-Za-z_]\w*")
_CMP = ("==", "!=", "<", "<=", ">", ">=")


class _P:
    """遞迴下降 parser，產生 AST：("num", s)｜("id", s)｜("not", x)｜("bin", op, l, r)。
    evaluate 與 to_rust 共用，確保 Python 拒絕的輸入 Rust 端也拒絕。"""

    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def eat(self, tok=None):
        v = self.peek()
        if v is None or (tok is not None and v != tok):
            raise GuardError(f"預期 {tok!r}，收到 {v!r}")
        self.i += 1
        return v

    def expr(self):
        v = self.and_()
        while self.peek() == "or":
            self.eat()
            v = ("bin", "or", v, self.and_())
        return v

    def and_(self):
        v = self.not_()
        while self.peek() == "and":
            self.eat()
            v = ("bin", "and", v, self.not_())
        return v

    def not_(self):
        if self.peek() == "not":
            self.eat()
            return ("not", self.not_())
        return self.cmp()

    def cmp(self):
        v = self.add()
        op = self.peek()
        if op in _CMP:
            self.eat()
            return ("bin", op, v, self.add())
        return v

    def add(self):
        v = self.mul()
        while self.peek() in ("+", "-"):
            op = self.eat()
            v = ("bin", op, v, self.mul())
        return v

    def mul(self):
        v = self.atom()
        while self.peek() == "*":
            self.eat()
            v = ("bin", "*", v, self.atom())
        return v

    def atom(self):
        t = self.eat()
        if t == "(":
            v = self.expr()
            self.eat(")")
            return v
        if _NUM.fullmatch(t):
            return ("num", t)
        if t in _KW or not _ID.fullmatch(t):
            raise GuardError(f"不合法的原子：{t!r}")
        return ("id", t)


def parse(tokens: list[str]):
    p = _P(tokens)
    v = p.expr()
    if p.peek() is not None:
        raise GuardError(f"多餘的 token：{p.peek()!r}")
    return v


def _ev(n, env):
    k = n[0]
    if k == "num":
        return float(n[1]) if "." in n[1] else int(n[1])
    if k == "id":
        if n[1] not in env:
            raise GuardError(f"未知識別字：{n[1]!r}")
        return env[n[1]]
    if k == "not":
        return not _ev(n[1], env)
    op = n[1]
    v, r = _ev(n[2], env), _ev(n[3], env)  # 不短路：未知識別字一律報錯
    if op == "or":
        return bool(v) or bool(r)
    if op == "and":
        return bool(v) and bool(r)
    return {"==": lambda: v == r, "!=": lambda: v != r, "<": lambda: v < r, "<=": lambda: v <= r,
            ">": lambda: v > r, ">=": lambda: v >= r, "+": lambda: v + r, "-": lambda: v - r,
            "*": lambda: v * r}[op]()


def evaluate(tokens: list[str], env: dict):
    return _ev(parse(tokens), env)


_PREC = {"or": 1, "and": 2, **{c: 3 for c in _CMP}, "+": 4, "-": 4, "*": 5}
_RUST_OP = {"and": "&&", "or": "||"}


def _rs(n, m) -> tuple[str, int]:
    k = n[0]
    if k == "num":
        return (n[1] if "." in n[1] else f"{n[1]}.0"), 9
    if k == "id":
        if n[1] not in m:
            raise GuardError(f"未知識別字：{n[1]!r}")
        return m[n[1]], 9
    if k == "not":
        s, pr = _rs(n[1], m)
        return (f"!{s}" if pr == 9 else f"!({s})"), 8
    op = n[1]
    p = _PREC[op]
    ls, lp = _rs(n[2], m)
    rs, rp = _rs(n[3], m)
    if lp < p or (op in _CMP and lp == p):
        ls = f"({ls})"
    if rp <= p:
        rs = f"({rs})"
    return f"{ls} {_RUST_OP.get(op, op)} {rs}", p


def to_rust(tokens: list[str], ident_map: dict[str, str]) -> str:
    return _rs(parse(tokens), ident_map)[0]
