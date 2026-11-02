#!/usr/bin/env python3
"""策略 FSM IR（strategies/<id>/fsm.ir.json）的來源檔漂移偵測。純 stdlib。

IR 記了它是依哪幾個檔案（strategy.py、config.yaml…）抽出來的，並附 sha256_lf（換行統一成 LF 後的 SHA-256）。
本腳本把記錄和目前的檔案比對：不同＝來源改過、IR 可能過期；找不到＝來源被搬走或刪除。

它只偵測『改過』，不判斷改得對不對、IR 還忠不忠於程式；後者要重新抽取或人審（見 genesis-deck
docs/superpowers/specs/2026-11-02-strategy-fsm-ir-design.md §7.3）。IR 的 schema／語意檢查在 genesis-deck
（scripts/formal/validate_ir.py），這裡不重複。

用法：  python scripts/check_fsm_ir_drift.py [--root .]
結束碼：0 無漂移（或沒有任何 fsm.ir.json）；1 有漂移或找不到來源檔；2 IR 讀不懂。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def sha256_lf(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def check(root: Path) -> tuple[list[str], list[str]]:
    """回傳 (問題清單, 已檢查的 IR 清單)。"""
    problems, checked = [], []
    for ir_path in sorted((root / "strategies").glob("*/fsm.ir.json")):
        rel_ir = ir_path.relative_to(root).as_posix()
        try:
            sources = json.loads(ir_path.read_text(encoding="utf-8"))["provenance"]["sources"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            problems.append(f"{rel_ir}：讀不懂（{exc!r}）")
            continue
        checked.append(rel_ir)
        for s in sources:
            p = root / s["path"]
            if not p.is_file():
                problems.append(f"{rel_ir}：找不到來源檔 {s['path']}")
            elif sha256_lf(p) != s["sha256_lf"]:
                problems.append(f"{rel_ir}：漂移——{s['path']} 的內容與 IR 記錄的 sha256_lf 不同，IR 可能過期")
    return problems, checked


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    a = ap.parse_args()
    problems, checked = check(Path(a.root))
    for ir in checked:
        print(f"檢查 {ir}")
    for pr in problems:
        print(f"✖ {pr}")
    if not checked and not problems:
        print("沒有任何 strategies/*/fsm.ir.json，略過")
    return 2 if any("讀不懂" in p for p in problems) else (1 if problems else 0)


if __name__ == "__main__":
    sys.exit(main())
