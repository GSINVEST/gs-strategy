"""regime 標籤與切片：只用截至當日的資料、規則固定、樣本不足不給 Sharpe、無前視。"""
import datetime as dt

from strategies._common.dryrun import regime


def _days(n, start=dt.date(2020, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _closes(rets, start=100.0):
    days, px, out = _days(len(rets)), start, []
    for d, r in zip(days, rets):
        px *= 1 + r
        out.append((d, px))
    return out


def test_label_market_trend_bull_then_bear():
    rets = [0.001] * 320 + [-0.004] * 160
    lab = regime.label_market(_closes(rets))
    days = _days(len(rets))
    assert lab[days[300]]["trend"] == "bull"
    assert lab[days[-1]]["trend"] == "bear"
    assert days[100] not in lab                          # 暖機期不給標籤


def test_label_market_uses_no_future_data():
    rets = [0.001] * 320 + [-0.004] * 160
    full = regime.label_market(_closes(rets))
    cut = regime.label_market(_closes(rets)[:400])
    d = _days(len(rets))[399]
    assert full[d] == cut[d]                             # 截斷未來資料，當日標籤不變


def test_label_market_vol_percentile_rises_with_amplitude():
    rets = [0.0005 + (0.0005 + 0.00005 * i) * (-1) ** i for i in range(600)]
    lab = regime.label_market(_closes(rets))
    days = _days(len(rets))
    assert lab[days[-1]]["vol"] == "high"
    assert "vol" not in lab[days[100]] if days[100] in lab else True


def test_label_rules_version_is_fixed():
    assert regime.RULES_VERSION == "regime-1"


def _eq(daily, start=100.0):
    days, v, out = _days(len(daily) + 1), start, []
    out.append([days[0], v])
    for d, r in zip(days[1:], daily):
        v *= 1 + r
        out.append([d, v])
    return out


def test_slice_compounds_returns_per_regime_and_counts_episodes():
    n = 80
    eq = _eq([0.01] * n)
    days = [p[0] for p in eq]
    # 標籤是「前一交易日收盤已知」：第 i 天報酬用 days[i-1] 的標籤
    lab = {d: {"trend": "bull" if i < 40 else "bear", "vol": "low"} for i, d in enumerate(days)}
    out = regime.slice_equity(eq, lab)
    t = {x["label"]: x for x in out["dims"]["trend"]}
    assert t["bull"]["days"] == 40 and t["bear"]["days"] == 40
    assert abs(t["bull"]["ret"] - ((1.01 ** 40 - 1) * 100)) < 1e-6
    assert t["bull"]["episodes"] == 1 and t["bear"]["episodes"] == 1
    assert out["dims"]["vol"][0]["days"] == 80


def test_slice_no_lookahead_uses_previous_session_label():
    eq = _eq([0.01, 0.01, 0.01])
    days = [p[0] for p in eq]
    lab = {days[0]: {"trend": "bull", "vol": "low"}, days[1]: {"trend": "bear", "vol": "low"},
           days[2]: {"trend": "bear", "vol": "low"}, days[3]: {"trend": "bear", "vol": "low"}}
    t = {x["label"]: x for x in regime.slice_equity(eq, lab)["dims"]["trend"]}
    assert t["bull"]["days"] == 1 and t["bear"]["days"] == 2     # 第 1 天用 days[0] 的標籤，之後用 bear


def test_slice_sharpe_withheld_when_sample_thin():
    eq = _eq([0.01, -0.005] * 20)                                 # 40 天 < 60
    days = [p[0] for p in eq]
    lab = {d: {"trend": "bull", "vol": "mid"} for d in days}
    x = regime.slice_equity(eq, lab)["dims"]["trend"][0]
    assert x["days"] == 40 and x["sharpe"] is None


def test_slice_sharpe_given_when_enough_days_and_unlabeled_counted():
    eq = _eq([0.01, -0.005] * 50)                                 # 100 天
    days = [p[0] for p in eq]
    lab = {d: {"trend": "bull", "vol": "mid"} for d in days[20:]}  # 前 20 天沒標籤
    out = regime.slice_equity(eq, lab)
    assert out["unlabeled_days"] == 20
    x = out["dims"]["trend"][0]
    assert x["days"] == 80 and x["sharpe"] is not None and x["sharpe"] > 0


def test_slice_maxdd_is_negative_percent_within_regime_days():
    eq = _eq([0.10, -0.20, 0.05] + [0.0] * 60)
    days = [p[0] for p in eq]
    lab = {d: {"trend": "bull", "vol": "mid"} for d in days}
    x = regime.slice_equity(eq, lab)["dims"]["trend"][0]
    assert abs(x["maxdd"] - (-20.0)) < 1e-6


def test_slice_empty_inputs():
    assert regime.slice_equity([], {})["dims"] == {"trend": [], "vol": []}
    assert regime.slice_equity([["2020-01-01", 100]], {})["dims"] == {"trend": [], "vol": []}
