"""regime 標籤與績效切片。只用標準函式庫（快照端要能在主機系統 python 上跑），不連網。

兩維、規則固定、版本化（改規則＝改 RULES_VERSION，舊結果不回頭重貼標籤）：

  trend：收盤相對 200 日均線，加上該均線與 60 個交易日前相比的斜率
         bull＝收盤在均線上且均線上升；bear＝收盤在均線下且均線下降；其餘 range
  vol  ：20 日實現波動率（日對數報酬標準差 × √252）在「截至當日」的擴張歷史中的分位
         low＝分位 ≤ 1/3；high＝分位 > 2/3；其餘 mid

標籤只用截至當日收盤的資料（不看未來）。切片時第 t 日的策略報酬歸給「前一個有標籤的交易日」的標籤，
因為那才是 t 日開盤前已知的環境，避免把當日行情本身當成分類依據。

這是樣本內描述：同一段歷史已被策略設計與 D1 篩選看過，不是樣本外證據，不能當晉升依據。
"""
from __future__ import annotations

import bisect
import math

RULES_VERSION = "regime-1"
BASIS = "in_sample_backfill"
TREND_MA, TREND_SLOPE, VOL_WIN, VOL_MIN_HISTORY = 200, 60, 20, 250
MIN_DAYS_FOR_SHARPE = 60
TRADING_DAYS = 252
DIMS = ("trend", "vol")
ORDER = {"trend": ("bull", "range", "bear"), "vol": ("low", "mid", "high")}


def label_market(closes) -> dict:
    """closes：依日期升冪的 (date, close)。回 {date: {"trend": ..., "vol": ...}}；暖機期的欄位不給。"""
    dates = [str(d) for d, _ in closes]
    px = [float(c) for _, c in closes]
    n = len(px)
    out = {d: {} for d in dates}
    # trend：用累積和算 200 日均線
    cs = [0.0]
    for p in px:
        cs.append(cs[-1] + p)
    ma = [None] * n
    for i in range(TREND_MA - 1, n):
        ma[i] = (cs[i + 1] - cs[i + 1 - TREND_MA]) / TREND_MA
    for i in range(TREND_MA - 1 + TREND_SLOPE, n):
        up, down = ma[i] > ma[i - TREND_SLOPE], ma[i] < ma[i - TREND_SLOPE]
        out[dates[i]]["trend"] = "bull" if px[i] > ma[i] and up else "bear" if px[i] < ma[i] and down else "range"
    # vol：20 日實現波動率，與截至當日的歷史比分位（含當日）
    lr = [None] + [math.log(px[i] / px[i - 1]) if px[i] > 0 and px[i - 1] > 0 else 0.0 for i in range(1, n)]
    hist = []                                   # 升冪的歷史 vol，用 bisect 算分位
    for i in range(VOL_WIN, n):
        w = lr[i - VOL_WIN + 1:i + 1]
        m = sum(w) / VOL_WIN
        v = math.sqrt(sum((x - m) ** 2 for x in w) / (VOL_WIN - 1)) * math.sqrt(TRADING_DAYS)
        bisect.insort(hist, v)
        if len(hist) >= VOL_MIN_HISTORY:
            rank = bisect.bisect_right(hist, v) / len(hist)
            out[dates[i]]["vol"] = "low" if rank <= 1 / 3 else "high" if rank > 2 / 3 else "mid"
    return {d: lab for d, lab in out.items() if lab}


def _stats(rets, episodes):
    n = len(rets)
    growth, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        growth *= 1 + r
        peak = max(peak, growth)
        dd = min(dd, growth / peak - 1)
    sharpe = None
    if n >= MIN_DAYS_FOR_SHARPE:
        m = sum(rets) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in rets) / (n - 1))
        sharpe = m / sd * math.sqrt(TRADING_DAYS) if sd > 0 else None
    return {"days": n, "episodes": episodes, "ret": (growth - 1) * 100, "sharpe": sharpe, "maxdd": dd * 100}


def slice_equity(equity, labels) -> dict:
    """equity：[[date, equity], ...]（基準情境）；labels：label_market() 的結果。

    每個 regime 回 days／episodes（連續出現的段數，看有幾個獨立時段）／ret（%，各日複利）／
    sharpe（天數不足 MIN_DAYS_FOR_SHARPE 則為 None）／maxdd（%，只算該 regime 的日子串起來）。
    """
    ldates = sorted(labels)
    buckets = {d: {} for d in DIMS}              # dim -> label -> {"rets": [], "episodes": n, "last": idx}
    unlabeled, prev_idx = 0, {d: None for d in DIMS}
    for i in range(1, len(equity or [])):
        d0, v0 = equity[i - 1][0], equity[i - 1][1]
        v1 = equity[i][1]
        if not v0 or v0 <= 0:
            continue
        j = bisect.bisect_left(ldates, str(equity[i][0])) - 1       # 嚴格早於當日的最近標籤日
        lab = labels[ldates[j]] if j >= 0 else {}
        r = v1 / v0 - 1
        if not any(dim in lab for dim in DIMS):
            unlabeled += 1
            prev_idx = {d: None for d in DIMS}
            continue
        for dim in DIMS:
            name = lab.get(dim)
            if name is None:
                prev_idx[dim] = None
                continue
            b = buckets[dim].setdefault(name, {"rets": [], "episodes": 0})
            if prev_idx[dim] != name:
                b["episodes"] += 1
            b["rets"].append(r)
            prev_idx[dim] = name
    dims = {dim: [dict(label=name, **_stats(buckets[dim][name]["rets"], buckets[dim][name]["episodes"]))
                  for name in ORDER[dim] if name in buckets[dim]] for dim in DIMS}
    return {"rules_version": RULES_VERSION, "basis": BASIS, "unlabeled_days": unlabeled, "dims": dims}
