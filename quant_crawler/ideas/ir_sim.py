# quant_crawler/ideas/ir_sim.py
"""IR 的 Python 參考模擬器（Gslang v0.1 語意；不含換月、成本、保證金）。

指標一律呼叫 strategies/_common/gslang/indicators.py（與 Gslang codegen 產出的 strategy.py 同一份實作），
所以 Rust 版對它逐筆比對，等於對標準庫做差異測試。
"""
from __future__ import annotations

from quant_crawler.ideas.guard import evaluate, tokenize

ZONES = {"NO_SIGNAL": 0, "LOW": 1, "MID": 2, "HIGH": 3}


def _consts(ir: dict) -> dict:
    out = {}
    for c in ir.get("constants", []):
        v = c["value"]
        out[c["name"]] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float(bool(v))
    return out


def _signal(sig: dict, closes: list[float], consts: dict, ind_mod) -> int:
    comp = sig["compute"]
    if comp.get("form") != "zone_cmp":
        raise ValueError(f"參考模擬器只支援 zone_cmp，收到 {comp.get('form')!r}")
    vals = {}
    for name, spec in comp["indicators"].items():
        key = spec["fn"]
        args = {k: (int(consts[v]) if isinstance(v, str) else v) for k, v in spec["args"].items()}
        vals[name] = ind_mod.INDICATORS[key].compute(closes, **args)
    a, b = vals[comp["left"]], vals[comp["right"]]
    if a is None or b is None:
        return ZONES["NO_SIGNAL"]
    return ZONES["LOW"] if a < b else ZONES["HIGH"] if a > b else ZONES["MID"]


def simulate(ir: dict, bars: list[tuple[float, float, float, float]]):
    from strategies._common.gslang import indicators as ind_mod

    consts = _consts(ir)
    pos, pending, closes, fills = 0.0, None, [], []
    base_env = {**consts, **ZONES}
    state_preds = [(s["id"], tokenize(s["predicate"])) for s in ir["states"]]
    for i, (o, _h, _l, c) in enumerate(bars):
        if pending is not None:
            pos = pending
            fills.append((i, o, pos))
            pending = None
        closes.append(c)
        env = {**base_env, "position": pos}
        for s in ir["signals"]:
            env[s["id"]] = _signal(s, closes, consts, ind_mod)
        state = next((sid for sid, toks in state_preds if evaluate(toks, env)), None)
        hits = [t for t in ir["transitions"]
                if t.get("on") == "BAR_CLOSE" and state in t["from"] and evaluate(tokenize(t["guard"]), env)]
        if len(hits) > 1:
            raise RuntimeError(f"守衛不互斥：{[t['id'] for t in hits]}")
        if hits:
            tgt = evaluate(tokenize(hits[0]["orders"][0]["target"]), env)
            pending = float(tgt)
    return fills
