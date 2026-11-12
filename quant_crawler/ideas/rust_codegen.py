# quant_crawler/ideas/rust_codegen.py
"""IR → Rust 單檔程式（v1：CLOSE@1／SMA@1／EMA@1 的 zone_cmp 訊號）。

協定：stdin 每行 `open high low close`；stdout 每個成交一行 `index,price,position`。
語意與 ir_sim.simulate 相同；不含換月、成本、保證金。
"""
from __future__ import annotations

from quant_crawler.ideas.guard import GuardError, tokenize, to_rust

SUPPORTED = {"CLOSE@1", "SMA@1", "EMA@1"}
ZONES = {"NO_SIGNAL": 0, "LOW": 1, "MID": 2, "HIGH": 3}


class RustUnsupported(Exception):
    pass


HEADER = """\
#![allow(non_upper_case_globals, non_snake_case, dead_code)]
use std::io::{self, BufRead};

const Z_NO_SIGNAL: u8 = 0;
const Z_LOW: u8 = 1;
const Z_MID: u8 = 2;
const Z_HIGH: u8 = 3;

fn ind_sma(c: &[f64], w: usize) -> Option<f64> {
    if c.len() < w { return None; }
    let t = &c[c.len() - w..];
    Some(t.iter().sum::<f64>() / w as f64)
}

fn ind_ema(c: &[f64], w: usize) -> Option<f64> {
    let n = 10 * w;
    if c.len() < n { return None; }
    let t = &c[c.len() - n..];
    let a = 2.0 / (w as f64 + 1.0);
    let mut e: f64 = t[..w].iter().sum::<f64>() / w as f64;
    for v in &t[w..] { e = e + a * (*v - e); }
    Some(e)
}

fn zone(a: Option<f64>, b: Option<f64>) -> u8 {
    match (a, b) {
        (Some(x), Some(y)) => if x < y { Z_LOW } else if x > y { Z_HIGH } else { Z_MID },
        _ => Z_NO_SIGNAL,
    }
}
"""


def _num(v) -> str:
    f = float(v) if not isinstance(v, bool) else float(bool(v))
    return repr(f)


def generate_rust(ir: dict) -> str:
    consts = {c["name"]: c["value"] for c in ir.get("constants", [])}
    for c in ir.get("constants", []):
        if not isinstance(c["value"], (int, float, bool)):
            raise RustUnsupported(f"常數 {c['name']} 不是數值：{c['value']!r}")
    out = [HEADER]
    for name, v in consts.items():
        out.append(f"const K_{name}: f64 = {_num(v)};")
    idents = {"position": "pos", **{k: f"Z_{k}" for k in ZONES}, **{k: f"K_{k}" for k in consts}}

    body = []
    for s in ir["signals"]:
        comp = s.get("compute", {})
        if comp.get("form") != "zone_cmp":
            raise RustUnsupported(f"訊號 {s['id']} 的形式 {comp.get('form')!r} 不支援（v1 只有 zone_cmp）")
        for iname, spec in comp["indicators"].items():
            if spec["fn"] not in SUPPORTED:
                raise RustUnsupported(f"指標 {spec['fn']} 不支援（v1：{sorted(SUPPORTED)}）")
            if spec["fn"] == "CLOSE@1":
                body.append(f"        let v_{iname}: Option<f64> = closes.last().copied();")
            else:
                fn = "ind_sma" if spec["fn"] == "SMA@1" else "ind_ema"
                w = spec["args"]["window"]
                body.append(f"        let v_{iname}: Option<f64> = {fn}(&closes, K_{w} as usize);")
        body.append(f"        let sig_{s['id']}: u8 = zone(v_{comp['left']}, v_{comp['right']});")
        idents[s["id"]] = f"sig_{s['id']}"

    try:
        state_exprs = [(st["id"], to_rust(tokenize(st["predicate"]), idents)) for st in ir["states"]]
        trans = [(t["id"], t["from"], to_rust(tokenize(t["guard"]), idents),
                  to_rust(tokenize(t["orders"][0]["target"]), idents))
                 for t in ir["transitions"] if t.get("on") == "BAR_CLOSE"]
    except GuardError as e:
        raise RustUnsupported(f"守衛／狀態運算式不支援：{e}") from e

    body.append("        let state: &str = " + " else ".join(
        f'if {expr} {{ "{sid}" }}' for sid, expr in state_exprs) + ' else { "?" };')
    body.append("        let mut hits = 0;")
    for tid, frm, guard, tgt in trans:
        cond = " || ".join(f'state == "{f}"' for f in frm)
        body.append(f"        if ({cond}) && ({guard}) {{ hits += 1; pending = Some({tgt}); }}  // {tid}")
    body.append('        if hits > 1 { panic!("guard overlap"); }')

    main = """
fn main() {
    let stdin = io::stdin();
    let mut closes: Vec<f64> = Vec::new();
    let mut pos: f64 = 0.0;
    let mut pending: Option<f64> = None;
    for (i, line) in stdin.lock().lines().enumerate() {
        let line = line.unwrap();
        if line.trim().is_empty() { continue; }
        let f: Vec<f64> = line.split_whitespace().map(|x| x.parse().unwrap()).collect();
        let (o, c) = (f[0], f[3]);
        if let Some(t) = pending.take() {
            pos = t;
            println!("{},{},{}", i, o, pos);
        }
        closes.push(c);
""" + "\n".join(body) + "\n    }\n}\n"
    out.append(main)
    return "\n".join(out)
