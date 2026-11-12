# quant_crawler/ideas/tlc.py
"""對單一 IR 跑 TLC（透過 genesis-deck fsm_atlas.py）。PASS 只代表「有限模型內未發現違反」。

下游判斷通過必須用 `verdict == "pass_with_bounds"`，不得用 startswith／truthiness／`!= "counterexample"`。
"""
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


def verdict_from_atlas(atlas, ir_id: str) -> str:
    """任何結構異常都失敗關閉（回 unchecked），不丟例外。"""
    if not isinstance(atlas, dict) or not atlas.get("tlc_ran"):
        return "unchecked"
    targets = atlas.get("targets")
    if not isinstance(targets, list):
        return "unchecked"
    for t in targets:
        if not isinstance(t, dict):
            return "unchecked"
        if t.get("id") == ir_id:
            s = t.get("status", "")
            if not isinstance(s, str):
                return "unchecked"
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


def _clear_matching(d: Path, pattern: str) -> None:
    # fsm_atlas 會跑 ir 資料夾內所有 *.ir.json、params 內 *.demo.json；只清這些，不動其他檔案
    if d.is_dir():
        for f in d.glob(pattern):
            if f.is_file():
                f.unlink()


def run_tlc(ir_path: Path, work: Path, root: str | None = None, timeout: int = 600, heap: str = "1g") -> TlcResult:
    formal = _root(root) / "scripts" / "formal"
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    ir_dir, params_dir = work / "ir", work / "params"
    ir_dir.mkdir(parents=True, exist_ok=True)
    _clear_matching(ir_dir, "*.ir.json")
    _clear_matching(params_dir, "*.demo.json")
    shutil.copy(ir_path, ir_dir / f"{ir['id']}.ir.json")
    write_demo_params(ir, params_dir)
    out = work / "atlas.json"
    out.unlink(missing_ok=True)  # 避免上一輪的舊 atlas 被當成本次結果
    cmd = [sys.executable, str(formal / "fsm_atlas.py"), "--ir-dir", str(ir_dir),
           "--params-dir", str(params_dir), "--allow-demo-params", "--run-tlc",
           "--timeout", str(timeout), "--heap", heap, "--deterministic", "--out", str(out)]
    env = {**os.environ}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * 4, env=env)
    except subprocess.TimeoutExpired:
        return TlcResult("inconclusive", {"error": f"timeout：fsm_atlas 超過 {timeout * 4} 秒未結束，結果未知"})
    except OSError as e:
        return TlcResult("unchecked", {"error": f"無法啟動 fsm_atlas：{e}"})
    # fsm_atlas.main 成功時一律 return 0；非零＝崩潰，atlas 不可信（合法的反例仍是 rc=0）
    if p.returncode != 0:
        return TlcResult("unchecked", {"error": "fsm_atlas 非零結束，atlas 不可信",
                                       "rc": p.returncode, "stderr": p.stderr[-2000:]})
    try:
        atlas = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return TlcResult("unchecked", {"error": f"atlas 缺失或毀損：{e}", "rc": p.returncode})
    return TlcResult(verdict_from_atlas(atlas, ir["id"]),
                     {"tlc_note": atlas.get("tlc_note") if isinstance(atlas, dict) else None,
                      "rc": p.returncode})
