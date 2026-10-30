"""Roll 價差代理依日盤時段分窗：開盤窗、盤中、收盤窗（13:30–13:45、13:40–13:45）。

用法：python tick_close_window.py <silver/ticks/taifex_futures> 2023 2024 2025
每窗以逐日 lag-1 共變異數按筆數加權平均，再取 2*sqrt(-cov)。
"""
import json
import sys

import numpy as np
import pyarrow.parquet as pq

WINDOWS = {
    "open_0845_0900": ("084500", "090000"),
    "mid_0900_1330": ("090000", "133000"),
    "close_1330_1345": ("133000", "134501"),
    "close_1340_1345": ("134000", "134501"),
}


def run(root, product, years):
    out = {}
    for y in years:
        t = pq.read_table(
            f"{root}/year={y}/part-{y}.parquet",
            columns=["trade_date", "expiry", "trade_time", "price"],
            filters=[("product", "=", product)],
            partitioning=None,
        ).to_pandas()
        t = t[t.expiry.str.fullmatch(r"\d{6}")]
        for name, (a, b) in WINDOWS.items():
            w = t[(t.trade_time >= a) & (t.trade_time < b)]
            covs, ws = [], []
            for _, g in w.groupby("trade_date"):
                f = g[g.expiry == g.expiry.min()].sort_values("trade_time", kind="stable")
                d = np.diff(f.price.to_numpy())
                if len(d) < 30:
                    continue
                covs.append(np.cov(d[1:], d[:-1], ddof=0)[0, 1])
                ws.append(len(d) - 1)
            c = float(np.average(covs, weights=ws))
            out[f"{product}_{y}_{name}"] = dict(
                days=len(covs),
                trades_per_day=int(np.mean(ws)),
                pooled_cov=round(c, 4),
                roll=round(2 * np.sqrt(-c), 3) if c < 0 else None,
                neg_days_pct=round(float(np.mean(np.array(covs) < 0) * 100), 1),
            )
    return out


if __name__ == "__main__":
    r = {}
    for p in ("TX", "MTX"):
        r.update(run(sys.argv[1], p, [int(x) for x in sys.argv[2:]]))
    print(json.dumps(r, indent=1))
