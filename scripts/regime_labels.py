"""產生 regime 標籤檔（給 dryrun.snapshot --regime-labels 用）。需要網路（QUANTDATA REST），所以和只用標準函式庫的快照端分開。

    python scripts/regime_labels.py --out regime_labels.json [--start 2016-01-04] [--end YYYY-MM-DD]

市場序列用台指期近月連續（TX，2016-01-04 起）收盤；規則見 strategies/_common/dryrun/regime.py。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from strategies._common import qd
from strategies._common.dryrun import regime


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="regime_labels")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--end", default=dt.date.today().isoformat())
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")       # Windows 中文版終端預設 cp950
    df = qd.futures_continuous("TX", a.start, a.end)
    df = df[df["close"] > 0].sort_values("trading_date")
    closes = [(d.strftime("%Y-%m-%d"), float(c)) for d, c in zip(df["trading_date"], df["close"])]
    labels = regime.label_market(closes)
    Path(a.out).write_text(json.dumps({"rules_version": regime.RULES_VERSION, "source": "tx_continuous_d",
                                       "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                       "labels": labels}, ensure_ascii=False), encoding="utf-8")
    print(f"{len(closes)} 個交易日、{len(labels)} 個有標籤 → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
