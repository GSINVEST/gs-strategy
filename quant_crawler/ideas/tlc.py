# quant_crawler/ideas/tlc.py
"""對單一 IR 跑 TLC（透過 genesis-deck fsm_atlas.py）。PASS 只代表「有限模型內未發現違反」。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from quant_crawler.ideas.gslang_bridge import _root

PARAMS_SCHEMA = "strategy-fsm-model-params/1"
# fsm_atlas.py 實際會輸出的 target status；未知值一律視為 inconclusive（不得當通過）
_KNOWN = {"pass_with_bounds", "counterexample", "mutation_not_caught", "inconclusive",
          "needs_review", "unchecked", "vacuous", "proposed_pass"}


@dataclass
class TlcResult:
    verdict: str
    detail: dict
    demo_params: bool = True


def verdict_from_atlas(atlas: dict, ir_id: str) -> str:
    if not atlas.get("tlc_ran"):
        return "unchecked"
    for t in atlas.get("targets", []):
        if t.get("id") == ir_id:
            s = t.get("status", "")
            return s if s in _KNOWN else "inconclusive"
    return "unchecked"


def _is_todo(v) -> bool:
    # gslang IR 中未定數值的標記形狀：{"todo": "TODO(人)"}；None 也視為未定
    return v is None or (isinstance(v, dict) and v.get("todo") == "TODO(人)")


def write_demo_params(ir: dict, d: Path) -> Path:
    """TODO(人) 的滑價／跳空以示範值頂替，並明確標 kind=demo（非統計結果）。
    IR 已有具體值的項目不寫入，否則 ir_to_tla 會以 PARAM_OVERRIDES_IR 拒絕整份產生。"""
    params: dict = {}
    slip_ir = ir.get("costs", {}).get("slippage", {}).get("adverse_points_per_fill")
    if _is_todo(slip_ir):
        spread = ir.get("costs", {}).get("slippage", {}).get("spread_points", 6.0)
        slip = float(spread) / 2 if isinstance(spread, (int, float)) else 3.0
        params["slip_pts"] = {"value": slip, "kind": "demo", "source": "由 IR 成本推得的示範值；非統計結果"}
    gap_ir = next((s.get("params", {}).get("gap_points") for s in ir.get("stress", []) if s.get("kind") == "gap"), None)
    if _is_todo(gap_ir):
        params["gap_points"] = {"value": 600, "kind": "demo", "source": "示範值；非歷史統計"}
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": PARAMS_SCHEMA,
        "ir_id": ir["id"],
        "search_bounds": {"max_steps": 4, "small_move_pts": 50},
        "params": params,
        "opt_in_invariants": [],
    }
    p = d / f"{ir['id']}.demo.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return p


def run_tlc(ir_path: Path, work: Path, root: str | None = None, timeout: int = 600, heap: str = "1g") -> TlcResult:
    formal = _root(root) / "scripts" / "formal"
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    ir_dir, params_dir = work / "ir", work / "params"
    ir_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(ir_path, ir_dir / f"{ir['id']}.ir.json")
    write_demo_params(ir, params_dir)
    out = work / "atlas.json"
    cmd = [sys.executable, str(formal / "fsm_atlas.py"), "--ir-dir", str(ir_dir),
           "--params-dir", str(params_dir), "--allow-demo-params", "--run-tlc",
           "--timeout", str(timeout), "--heap", heap, "--deterministic", "--out", str(out)]
    env = {**os.environ}
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * 4, env=env)
    if not out.is_file():
        return TlcResult("unchecked", {"stderr": p.stderr[-2000:], "rc": p.returncode})
    atlas = json.loads(out.read_text(encoding="utf-8"))
    return TlcResult(verdict_from_atlas(atlas, ir["id"]),
                     {"tlc_note": atlas.get("tlc_note"), "rc": p.returncode,
                      "stderr": p.stderr[-2000:]})
