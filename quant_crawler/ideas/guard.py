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


class _P:
    def __init__(self, toks, env):
        self.t, self.i, self.env = toks, 0, env

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
            r = self.and_()
            v = bool(v) or bool(r)
        return v

    def and_(self):
        v = self.not_()
        while self.peek() == "and":
            self.eat()
            r = self.not_()
            v = bool(v) and bool(r)
        return v

    def not_(self):
        if self.peek() == "not":
            self.eat()
            return not self.not_()
        return self.cmp()

    def cmp(self):
        v = self.add()
        op = self.peek()
        if op in ("==", "!=", "<", "<=", ">", ">="):
            self.eat()
            r = self.add()
            return {"==": v == r, "!=": v != r, "<": v < r, "<=": v <= r, ">": v > r, ">=": v >= r}[op]
        return v

    def add(self):
        v = self.mul()
        while self.peek() in ("+", "-"):
            op = self.eat()
            r = self.mul()
            v = v + r if op == "+" else v - r
        return v

    def mul(self):
        v = self.atom()
        while self.peek() == "*":
            self.eat()
            v = v * self.atom()
        return v

    def atom(self):
        t = self.eat()
        if t == "(":
            v = self.expr()
            self.eat(")")
            return v
        if re.fullmatch(r"\d+(?:\.\d+)?", t):
            return float(t) if "." in t else int(t)
        if t in _KW or not re.fullmatch(r"[A-Za-z_]\w*", t):
            raise GuardError(f"不合法的原子：{t!r}")
        if t not in self.env:
            raise GuardError(f"未知識別字：{t!r}")
        return self.env[t]


def evaluate(tokens: list[str], env: dict):
    p = _P(tokens, env)
    v = p.expr()
    if p.peek() is not None:
        raise GuardError(f"多餘的 token：{p.peek()!r}")
    return v


def to_rust(tokens: list[str], ident_map: dict[str, str]) -> str:
    out = []
    for t in tokens:
        if t == "and":
            out.append("&&")
        elif t == "or":
            out.append("||")
        elif t == "not":
            out.append("!")
        elif re.fullmatch(r"\d+(?:\.\d+)?", t):
            out.append(t if "." in t else f"{t}.0")
        elif re.fullmatch(r"[A-Za-z_]\w*", t):
            if t not in ident_map:
                raise GuardError(f"未知識別字：{t!r}")
            out.append(ident_map[t])
        else:
            out.append(t)
    s = " ".join(out)
    return s.replace("! ", "!").replace("( ", "(").replace(" )", ")")
