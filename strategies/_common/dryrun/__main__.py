"""dry run CLI（影子模式）。

  python -m strategies._common.dryrun start  --strategy-id vgrsi_tx --start-date 2026-11-02
  python -m strategies._common.dryrun run    --strategy-id vgrsi_tx [--session 2026-11-02]
  python -m strategies._common.dryrun resume --strategy-id vgrsi_tx --by <人> --note <原因>

只跑、只寫 DRY 帳本；沒有任何下單或連券商的指令。需 TEJAPI_KEY 與 tquant_future bundle。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .benchmark import NoBenchmarkPack
from .guards import DEFAULT_ALLOWED_HOSTS, EgressGuard, ImportGuard

REPO = Path(__file__).resolve().parents[3]
DEFAULT_DIR = REPO / "log" / "dryrun"


def _default_session(runner) -> str:
    """最近一個已收盤的交易日（台北時間）。資料落後時會因此標 MISSING，而不是悄悄跑舊日。"""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from .executor import last_completed_session
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    sessions = runner.sessions((now - timedelta(days=14)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))
    return last_completed_session(sessions, today=now.strftime("%Y-%m-%d"), hhmm=now.strftime("%H:%M"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dryrun")
    ap.add_argument("cmd", choices=["start", "run", "resume", "framework-version"])
    ap.add_argument("--strategy-id")
    ap.add_argument("--ledger", type=Path, default=Path(os.environ.get("DRYRUN_LEDGER", DEFAULT_DIR / "dry_ledger.sqlite")))
    ap.add_argument("--start-date")
    ap.add_argument("--capital", type=float)
    ap.add_argument("--session")
    ap.add_argument("--by")
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)

    # 容器內所有出口都經 egress-proxy：行程內 guard 只看得到代理主機，目的地白名單由代理執行
    extra = tuple(h for h in os.environ.get("DRYRUN_EGRESS_ALLOW", "").split(",") if h)
    with ImportGuard(), EgressGuard(allowed_hosts=DEFAULT_ALLOWED_HOSTS + extra):
        from .executor import DryRunExecutor
        from .ledger import DryLedger
        from .zl_adapter import ZiplineSimRunner

        runner = ZiplineSimRunner()
        if a.cmd == "framework-version":
            print(runner.framework_version())      # 晉升時 registry 的 framework_version 要填這個
            return 0
        if not a.strategy_id:
            ap.error("需要 --strategy-id")
        ex = DryRunExecutor(ledger=DryLedger(a.ledger), runner=runner)
        if a.cmd == "start":
            if not a.start_date:
                ap.error("start 需要 --start-date")
            print("run_id", ex.start_run(a.strategy_id, start_date=a.start_date,
                                         capital_base=a.capital, benchmark=NoBenchmarkPack()))
        elif a.cmd == "run":
            session = a.session or _default_session(runner)
            print(ex.run_day(a.strategy_id, session))
        else:
            if not a.by:
                ap.error("resume 需要 --by")
            ex.resume(a.strategy_id, by=a.by, note=a.note)
            print("resumed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
