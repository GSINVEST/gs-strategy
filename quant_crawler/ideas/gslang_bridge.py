# quant_crawler/ideas/gslang_bridge.py
"""橋接 genesis-deck 的 Gslang 編譯器／codegen（唯讀快照，由 GDECK_FORMAL 指向）。

Gslang 快照在一個行程內是單例（模組進 sys.modules 後不再切換 root）。
不複製編譯器：複製會讓語言版本與桌面版／gateway 漂移。快照 sha 記在 $GDECK_FORMAL/SNAPSHOT_SHA。
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from quant_crawler.ideas.card import IdeaCard, rel_to_repo, set_status

DEFAULT_ROOT = "/home/kevin/gdeck-formal"


class FormalUnavailable(RuntimeError):
    pass


class CodegenFailed(RuntimeError):
    def __init__(self, reasons):
        super().__init__("；".join(reasons))
        self.reasons = list(reasons)


@dataclass
class CompileResult:
    ok: bool
    ir: dict | None
    unsupported: bool
    message: str
    todo_count: int = 0


def _root(root: str | None) -> Path:
    p = Path(root or os.environ.get("GDECK_FORMAL") or DEFAULT_ROOT)
    if not (p / "scripts" / "formal" / "gslang_compiler.py").is_file():
        raise FormalUnavailable(f"找不到 Gslang 快照：{p}（見計畫 Task 0 Step 2）")
    return p


def _import(name: str, path: Path):
    s = str(path)
    if s not in sys.path:
        sys.path.insert(0, s)
    mod = importlib.import_module(name)
    f = Path(getattr(mod, "__file__", "") or "").resolve()
    if path.resolve() not in f.parents:
        raise FormalUnavailable(f"本行程已載入另一個 Gslang 快照 {f.parent}，不能再切到 {path}")
    return mod


def load_formal(root: str | None = None):
    p = _root(root)
    f = p / "scripts" / "formal"
    return _import("gslang_compiler", f), _import("gslang_codegen", f)


def _strip_fence(text: str) -> str:
    m = re.search(r"```(?:gsl|gslang)?\r?\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def draft_gsl(card: IdeaCard, llm, root: str | None = None) -> str:
    p = _root(root)
    _import("gslang_compiler", p / "scripts" / "formal")
    prompt = _import("gslang_ai_prompt", p / "server" / "gateway")
    user = (f"策略構想（只依此寫，缺的數字寫 TODO(人)）：\n假說：{card.hypothesis}\n"
            f"機制：{card.mechanism}\n市場：{card.market}\n週期：{card.horizon}\n")
    return _strip_fence(llm(prompt.build_system_prompt(), user))


def compile_gsl(source: str, card_id: str, root: str | None = None) -> CompileResult:
    comp, _ = load_formal(root)
    try:
        ir = comp.compile_source(source, f"{card_id}.gsl")
    except comp.GslangError as e:
        msg = str(e)
        return CompileResult(False, None, "v0.1 不支援" in msg, msg)
    return CompileResult(True, ir, False, "", comp.count_todos(ir))


def gen_python(ir: dict, root: str | None = None) -> str:
    _, cg = load_formal(root)
    try:
        return cg.generate(ir)
    except cg.CodegenRefused as e:
        raise CodegenFailed(e.reasons) from e


def advance_card_to_fsm(card: IdeaCard, gsl: str, out_dir: Path, root: str | None = None) -> CompileResult:
    if card.status != "triaged":
        raise ValueError(f"只有 triaged 卡片能進 fsm（目前 {card.status}）：{card.id}")
    r = compile_gsl(gsl, card.id, root)
    if r.ok:
        out_dir.mkdir(parents=True, exist_ok=True)
        gp, ip = out_dir / f"{card.id}.gsl", out_dir / f"{card.id}.ir.json"
        gp.write_text(gsl, encoding="utf-8", newline="\n")
        ip.write_text(json.dumps(r.ir, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        card.gsl_path, card.ir_path = rel_to_repo(gp), rel_to_repo(ip)
        set_status(card, "fsm", f"編譯通過，TODO(人)={r.todo_count}")
    elif r.unsupported:
        set_status(card, "defer", r.message)
        card.resurrect_when = "Gslang 擴充該語法後重新分診"
    else:
        card.reason = f"AI 草稿編譯失敗：{r.message}"
    return r
