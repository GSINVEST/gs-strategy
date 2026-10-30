"""以逐筆成交估計 TX/MTX「有效價差」代理（Roll 估計量），對照 FixedSlippage(6 點)。

資料：QuantData silver/ticks/taifex_futures（只有成交，沒有買賣報價）。
Roll (1984)：spread = 2*sqrt(-cov(Δp_t, Δp_{t-1}))；cov >= 0 時估計量無定義。
只讀、不寫入資料；輸出 JSON 到 stdout。

用法：python tick_spread_proxy.py <QUANTDATA/silver/ticks/taifex_futures> 2023 2024 2025
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

DAY = ("084500", "134500")


def load(root: str, year: int, product: str) -> pd.DataFrame:
    t = pq.read_table(
        f"{root}/year={year}/part-{year}.parquet",
        columns=["trade_date", "product", "expiry", "trade_time", "price", "volume_bs"],
        filters=[("product", "=", product)],
        partitioning=None,
    )
    df = t.to_pandas()
    df = df[df["expiry"].str.fullmatch(r"\d{6}")]  # 排除價差單 (含 '/')
    return df


def roll_stats(prices: np.ndarray) -> tuple[float, int, float]:
    """回傳 (lag-1 自共變異, 樣本數, 非零價差變動比例)。"""
    d = np.diff(prices)
    if len(d) < 50:
        return np.nan, 0, np.nan
    c = np.cov(d[1:], d[:-1], ddof=0)[0, 1]
    return float(c), len(d) - 1, float((d != 0).mean())


def analyse(df: pd.DataFrame, session: str) -> dict:
    if session == "day":
        df = df[(df.trade_time >= DAY[0]) & (df.trade_time <= DAY[1])]
    else:
        df = df[(df.trade_time < DAY[0]) | (df.trade_time > DAY[1])]
    out = []
    for date, g in df.groupby("trade_date"):
        front = g[g.expiry == g.expiry.min()]  # 近月
        front = front.sort_values("trade_time", kind="stable")
        p = front.price.to_numpy()
        c_trade, n, nz = roll_stats(p)
        # 1 秒收盤價序列（降低同秒多筆掃單的機械性彈跳）
        s = front.groupby("trade_time").price.last().to_numpy()
        c_sec, n_sec, _ = roll_stats(s)
        out.append(dict(date=str(date), n=n, nz=nz, cov_trade=c_trade, cov_sec=c_sec,
                        n_sec=n_sec, vol=int(front.volume_bs.sum()),
                        px=float(p.mean()) if len(p) else np.nan,
                        hi_lo=float(p.max() - p.min()) if len(p) else np.nan))
    r = pd.DataFrame(out).dropna(subset=["cov_trade"])

    def roll(col):
        neg = r[col][r[col] < 0]
        pooled = r[col].mean()
        return dict(
            days=int(len(r)),
            neg_cov_days_pct=round(float((r[col] < 0).mean() * 100), 1),
            pooled_cov=round(float(pooled), 4),
            roll_spread_pooled=round(float(2 * np.sqrt(-pooled)), 3) if pooled < 0 else None,
            roll_spread_daily_median=round(float(np.median(2 * np.sqrt(-neg))), 3) if len(neg) else None,
            roll_spread_daily_p90=round(float(np.percentile(2 * np.sqrt(-neg), 90)), 3) if len(neg) else None,
        )

    return dict(
        session=session,
        trade_level=roll("cov_trade"),
        one_second=roll("cov_sec"),
        nonzero_change_share_mean=round(float(r.nz.mean()), 3),
        avg_trades_per_day=int(r.n.mean()),
        avg_daily_volume_bs=int(r.vol.mean()),
        avg_daily_hi_lo=round(float(r.hi_lo.mean()), 1),
        avg_price=round(float(r.px.mean()), 0),
    )


if __name__ == "__main__":
    root, years = sys.argv[1], [int(y) for y in sys.argv[2:]]
    res = {}
    for prod in ("TX", "MTX"):
        for y in years:
            df = load(root, y, prod)
            res[f"{prod}_{y}"] = [analyse(df, s) for s in ("day", "night")]
    print(json.dumps(res, ensure_ascii=False, indent=1))
