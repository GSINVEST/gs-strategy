"""晉升閘門 CLI：evaluate（算閘門並記 screened）、promote（人按晉升）。

結束碼：0 成功、2 被拒（PromotionError）、3 輸入錯誤。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))     # 直接 `python scripts/promotion_gate.py` 時 scripts.* 才找得到

from scripts.triage_generated import LEDGER  # noqa: E402
from strategies._common.promotion import (  # noqa: E402
    EXTERNAL_CHECKS, GateInputs, PromotionError, evaluate, promote, record_screening,
)
from strategies._common.promotion.registry import REGISTRY  # noqa: E402


def _returns(path: Path) -> np.ndarray:
    df = pd.read_parquet(path)
    if "returns" not in df.columns:
        raise ValueError(f"{path}: 沒有 returns 欄")
    return df["returns"].to_numpy(dtype=float)


def _external(pairs: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in pairs:
        name, _, code = p.partition("=")
        if name not in EXTERNAL_CHECKS or not code.lstrip("-").isdigit():
            raise ValueError(f"--external 格式應為 <{'|'.join(EXTERNAL_CHECKS)}>=<結束碼>：{p!r}")
        out[name] = int(code)
    return out


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    ev = sub.add_parser("evaluate")
    ev.add_argument("--strategy-dir", type=Path, required=True)
    ev.add_argument("--strategy-id", required=True)
    ev.add_argument("--perf", type=Path, required=True)
    ev.add_argument("--candidates", type=Path)
    ev.add_argument("--stress", type=Path)
    ev.add_argument("--spa-p", type=float)
    ev.add_argument("--external", action="append", default=[])
    ev.add_argument("--ledger", type=Path, default=LEDGER)
    ev.add_argument("--registry", type=Path, default=REGISTRY)
    ev.add_argument("--lineage-id", required=True)
    ev.add_argument("--branch", required=True)
    ev.add_argument("--framework-version", required=True)
    ev.add_argument("--data-snapshot", required=True)
    ev.add_argument("--cost-model-version", required=True)
    ev.add_argument("--tags", default="", help="逗號分隔")

    pr = sub.add_parser("promote")
    pr.add_argument("--strategy-dir", type=Path, required=True)
    pr.add_argument("--strategy-id", required=True)
    pr.add_argument("--by", required=True)
    pr.add_argument("--note", default="")
    pr.add_argument("--registry", type=Path, default=REGISTRY)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.cmd == "evaluate":
            inputs = GateInputs(
                strategy_id=args.strategy_id,
                returns=_returns(args.perf),
                ledger=args.ledger,
                candidates=(pd.read_parquet(args.candidates).to_numpy(dtype=float)
                            if args.candidates else None),
                stress_returns=_returns(args.stress) if args.stress else None,
                spa_p=args.spa_p,
                external=_external(args.external),
            )
            report = evaluate(inputs)
            rec = record_screening(
                report, strategy_dir=args.strategy_dir, lineage_id=args.lineage_id,
                branch=args.branch, framework_version=args.framework_version,
                data_snapshot=args.data_snapshot, cost_model_version=args.cost_model_version,
                tags=[t.strip() for t in args.tags.split(",") if t.strip()],
                registry=args.registry,
            )
            if rec["screening_count"] > 1:
                flip = (f"；判定 {rec['previous_verdict']} → {report.verdict}"
                        if rec["previous_verdict"] != report.verdict else "")
                print(f"警告：此版本第 {rec['screening_count']} 次篩選{flip}。"
                      "重跑換輸入不計入 trial，晉升時須附簽註。", file=sys.stderr)
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
            return 0
        rec = promote(args.strategy_id, strategy_dir=args.strategy_dir, by=args.by,
                      note=args.note, registry=args.registry)
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0
    except PromotionError as exc:
        print(f"拒絕：{exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"輸入錯誤：{exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
