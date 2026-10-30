"""python -m strategies._common.submission {submit,list,show,approve,retire}

核准者名單讀 config/approvers.yaml。``--as`` 只是字串比對，不是身分驗證——
擋得住手誤，擋不住有 shell 的惡意者（設計文件 §4）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .models import BacktestRun, Outcome, Submission
from .service import SubmissionQueue

REPO = Path(__file__).resolve().parents[3]


def load_approvers(root: Path) -> list[str]:
    path = root / "config" / "approvers.yaml"
    if not path.is_file():
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(a) for a in doc.get("approvers", [])]


def default_backtest(root: Path):
    """用 runner 跑核准後的 bundle。n_trials 取 ledger 的 selection 數、來源標 ledger。

    p 值目前**沒有**接上（BacktestRun.p_value=None）：完成行不會帶 p_value，
    online FDR 面板會因缺 p 值顯示 unavailable，直到 p 值的來源定義好。
    """
    def run(sub: Submission) -> BacktestRun:
        from strategies._common.runner import run_strategy_from_config
        out = root / ".submission-runs" / sub.sid / "perf.pkl"
        run_strategy_from_config(sub.bundle_dir / "strategy.py", sub.bundle_dir / "config.yaml",
                                 out, n_trials=sub.n_trials, approved_trial_id=sub.trial_id)
        return BacktestRun(summary={"perf": str(out.relative_to(root))})
    return run


def main(argv: list[str] | None = None, *, backtest=None) -> int:
    ap = argparse.ArgumentParser(prog="submission", description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=REPO)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit", help="提交 bundle（不跑回測）")
    s.add_argument("bundle", type=Path)
    s.add_argument("--prereg", required=True)
    s.add_argument("--submitter", required=True)
    sub.add_parser("list", help="列出所有提交與狀態")
    sh = sub.add_parser("show"); sh.add_argument("sid")
    a = sub.add_parser("approve", help="核准：記一筆 trial、簽核、跑回測")
    a.add_argument("sid"); a.add_argument("--as", dest="who", required=True)
    a.add_argument("--no-run", action="store_true", help="只記帳不跑回測")
    r = sub.add_parser("retire"); r.add_argument("sid"); r.add_argument("--as", dest="who", required=True)
    args = ap.parse_args(argv)

    root = args.root
    q = SubmissionQueue(root, approvers=load_approvers(root),
                        backtest=backtest or default_backtest(root))
    if args.cmd == "list":
        snap = q.snapshot()
        for sid, st in sorted(snap.status.items()):
            print(f"{sid}\t{st}\truns={snap.runs.get(sid, 0)}\ttrial={snap.trials.get(sid, 0)}")
        return 0
    if args.cmd == "show":
        print(json.dumps({"status": getattr(q.status(args.sid), "value", None),
                          "snapshot_trial": q.snapshot().trials.get(args.sid)}, ensure_ascii=False))
        return 0
    if args.cmd == "submit":
        res = q.submit(args.bundle, args.prereg, args.submitter)
    elif args.cmd == "approve":
        res = q.approve(args.sid, args.who, run=not args.no_run)
    else:
        res = q.retire(args.sid, args.who)
    print(f"{res.outcome.value}\t{res.submission_id}" + (f"\t{res.reason}" if res.reason else ""))
    return 1 if res.outcome is Outcome.REJECTED else 0


if __name__ == "__main__":
    sys.exit(main())
