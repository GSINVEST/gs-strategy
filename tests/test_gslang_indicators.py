"""Gslang 指標標準庫（G1a）的測試。

最重要的一組是「對原策略的逐位元一致」：把 vgrsi_tx／cubic_momentum_tx 的真 `strategy.py`
載入（zipline 與 futures_setup 以假模組代替），用真的 `_evaluate` 跑同一串收盤價，
與標準庫的輸出比位元。這不是拿標準庫自己的輸出自己驗。

其餘：版本必填、未知版本不得降級、NO_SIGNAL 條件、分區邊界嚴格、取整與夾限、
SMA 對獨立的純 Python 參考實作。
"""
from __future__ import annotations

import importlib.util
import math
import struct
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from strategies._common.gslang import indicators as ind

ROOT = Path(__file__).resolve().parents[1] / "strategies"


def bits(x: float) -> bytes:
    return struct.pack(">d", float(x))


# ---------------------------------------------------------------------------
# 真策略載入（假 zipline）
# ---------------------------------------------------------------------------

class _Recorder:
    def __init__(self):
        self.records: list[dict] = []
        self.orders: list[tuple] = []


def _load_strategy(name: str, monkeypatch):
    rec = _Recorder()
    api = types.ModuleType("zipline.api")
    for fn in ("continuous_future", "date_rules", "order_target", "record", "schedule_function", "time_rules"):
        setattr(api, fn, lambda *a, **k: None)
    api.record = lambda **kw: rec.records.append(kw)
    api.order_target = lambda asset, target: rec.orders.append((asset, target))
    zl = types.ModuleType("zipline")
    zl.api = api
    fs = types.ModuleType("futures_setup")
    fs.apply_taiwan_futures_costs = lambda **k: None
    fs.make_roll_futures_handler = lambda **k: (lambda c, d: None)
    fs.make_continuous_taiwan_futures = lambda *a, **k: []
    monkeypatch.setitem(sys.modules, "zipline", zl)
    monkeypatch.setitem(sys.modules, "zipline.api", api)
    monkeypatch.setitem(sys.modules, "futures_setup", fs)
    path = ROOT / name / "strategy.py"
    spec = importlib.util.spec_from_file_location(f"_real_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, rec


class _Data:
    def __init__(self, closes):
        self._closes = closes

    def history(self, cont, field, n, freq):
        import pandas as pd
        return pd.Series(self._closes[-n:], dtype=float)

    def current(self, cont, field):
        return "FRONT"


def _ctx(**kw):
    c = types.SimpleNamespace(portfolio=types.SimpleNamespace(positions={}))
    c.cont = "CONT"
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _series(seed: int, n: int, nan_every: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    x = 17000 + np.cumsum(rng.normal(0, 60, n))
    x = np.round(x)  # 期貨收盤是整數點，會出現相等價位
    out = x.tolist()
    if nan_every:
        for i in range(nan_every, n, nan_every):
            out[i] = float("nan")
    return out


# ---------------------------------------------------------------------------
# 一、與原策略逐位元一致
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed,nan_every", [(1, 0), (2, 0), (3, 17), (4, 0)])
def test_vgrsi_matches_real_strategy_bit_for_bit(seed, nan_every, monkeypatch):
    mod, rec = _load_strategy("vgrsi_tx", monkeypatch)
    window = 30
    ctx = _ctx(window=window, lower=30.0, upper=70.0, allow_short=False, position_contracts=1)
    closes = _series(seed, 140, nan_every)
    spec = ind.get("VGRSI@1")
    compared = 0
    for t in range(1, len(closes) + 1):
        rec.records.clear()
        mod._evaluate(ctx, _Data(closes[:t]))
        mine = spec.compute(closes[:t][-spec.history_bars(window=window):], window=window)
        if not rec.records:
            assert mine is None, f"t={t}: 原策略 NO_SIGNAL 但標準庫給 {mine}"
        else:
            assert mine is not None, f"t={t}: 原策略有值但標準庫 NO_SIGNAL"
            assert bits(mine) == bits(rec.records[0]["vgrsi"]), f"t={t}"
            compared += 1
    assert compared > 50


@pytest.mark.parametrize("seed,nan_every", [(11, 0), (12, 0), (13, 23)])
@pytest.mark.parametrize("allow_short", [True, False])
def test_cubic_matches_real_strategy_bit_for_bit(seed, nan_every, allow_short, monkeypatch):
    mod, rec = _load_strategy("cubic_momentum_tx", monkeypatch)
    lb, sw, zc, mx = 20, 60, 1.5, 2
    ctx = _ctx(lookback=lb, sigma_window=sw, z_crit=zc, max_contracts=mx, allow_short=allow_short)
    closes = _series(seed, 200, nan_every)
    spec = ind.get("CUBIC@1")
    params = dict(lookback=lb, sigma_window=sw, z_crit=zc)
    compared = 0
    for t in range(1, len(closes) + 1):
        rec.records.clear()
        mod._evaluate(ctx, _Data(closes[:t]))
        window = closes[:t][-spec.history_bars(**params):]
        out = spec.compute(window, **params)
        if not rec.records:
            assert out is None, f"t={t}: 原策略 NO_SIGNAL 但標準庫給 {out}"
            continue
        assert out is not None, f"t={t}: 原策略有值但標準庫 NO_SIGNAL"
        r = rec.records[0]
        assert bits(out.score) == bits(r["score"]), f"t={t} score"
        assert bits(out.z) == bits(r["z"]), f"t={t} z"
        lo = -mx if allow_short else 0
        assert ind.round_clamp(out.score * mx, lo, mx) == r["target"], f"t={t} target"
        compared += 1
    assert compared > 80


# ---------------------------------------------------------------------------
# 二、註冊表：版本必填、不降級
# ---------------------------------------------------------------------------

def test_version_is_mandatory():
    with pytest.raises(ind.IndicatorError, match="版本"):
        ind.get("VGRSI")


def test_unknown_version_does_not_fall_back():
    with pytest.raises(ind.IndicatorError, match="VGRSI@2"):
        ind.get("VGRSI@2")


def test_unknown_indicator_rejected():
    with pytest.raises(ind.IndicatorError):
        ind.get("NOPE@1")


@pytest.mark.parametrize("key", ["VGRSI@1", "CUBIC@1", "SMA@1"])
def test_every_entry_documents_itself(key):
    s = ind.get(key)
    for field in ("formula", "inputs", "window_semantics", "no_signal", "source", "version"):
        assert getattr(s, field), f"{key} 缺 {field}"


def test_unknown_param_rejected():
    with pytest.raises(ind.IndicatorError, match="參數"):
        ind.get("SMA@1").compute([1.0, 2.0, 3.0], window=2, bogus=1)


def test_missing_param_rejected_not_defaulted():
    with pytest.raises(ind.IndicatorError, match="window"):
        ind.get("SMA@1").compute([1.0, 2.0, 3.0])


# ---------------------------------------------------------------------------
# 三、分區與取整夾限
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("v,expected", [
    (None, "NO_SIGNAL"), (29.999, "LOW"), (30.0, "MID"), (50.0, "MID"),
    (70.0, "MID"), (70.001, "HIGH"),
])
def test_zone_boundaries_are_strict(v, expected):
    assert ind.zone(v, below=30.0, above=70.0) == expected


def test_zone_rejects_nan():
    assert ind.zone(float("nan"), below=30.0, above=70.0) == "NO_SIGNAL"


def test_zone_requires_low_below_high():
    with pytest.raises(ind.IndicatorError):
        ind.zone(50.0, below=70.0, above=30.0)


@pytest.mark.parametrize("x,lo,hi,expected", [
    (0.5, -2, 2, 0), (1.5, -2, 2, 2), (2.5, -2, 2, 2), (-3.7, -2, 2, -2),
    (0.49, 0, 2, 0), (-0.9, 0, 2, 0),
])
def test_round_clamp_uses_python_round_semantics(x, lo, hi, expected):
    # Python round() 是銀行家捨入：round(0.5)=0、round(1.5)=2、round(2.5)=2
    assert ind.round_clamp(x, lo, hi) == expected


def test_round_clamp_rejects_nonfinite():
    with pytest.raises(ind.IndicatorError):
        ind.round_clamp(float("inf"), -2, 2)


# ---------------------------------------------------------------------------
# 四、SMA 對獨立參考實作；NO_SIGNAL
# ---------------------------------------------------------------------------

def _sma_ref(closes, window):
    xs = [c for c in closes if not (isinstance(c, float) and math.isnan(c))]
    if len(xs) < window:
        return None
    tail = xs[-window:]
    return sum(tail) / window


@pytest.mark.parametrize("seed", range(5))
def test_sma_matches_independent_reference(seed):
    closes = _series(seed, 60, nan_every=11)
    spec = ind.get("SMA@1")
    for t in range(1, len(closes) + 1):
        win = closes[:t][-spec.history_bars(window=10):]
        got = spec.compute(win, window=10)
        ref = _sma_ref(win, 10)
        if ref is None:
            assert got is None
        else:
            assert got == pytest.approx(ref, rel=1e-12)


def test_sma_includes_current_bar():
    assert ind.get("SMA@1").compute([1.0, 2.0, 3.0, 4.0], window=2) == 3.5


def test_insufficient_data_is_no_signal_not_zero():
    assert ind.get("SMA@1").compute([1.0], window=5) is None
    assert ind.get("VGRSI@1").compute([1.0] * 5, window=30) is None


def test_deterministic_repeat():
    c = _series(7, 80)
    a = ind.get("VGRSI@1").compute(c, window=30)
    assert all(bits(ind.get("VGRSI@1").compute(c, window=30)) == bits(a) for _ in range(3))


def test_history_bars_contracts():
    assert ind.get("VGRSI@1").history_bars(window=30) == 35
    assert ind.get("CUBIC@1").history_bars(lookback=20, sigma_window=60, z_crit=1.5) == 65


# ---------------------------------------------------------------------------
# 五、參數驗證
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [0, -3, 2.5, True, "30"])
def test_window_must_be_positive_int(bad):
    with pytest.raises(ind.IndicatorError, match="window"):
        ind.get("VGRSI@1").compute([1.0] * 50, window=bad)


def test_sigma_window_needs_at_least_two():
    with pytest.raises(ind.IndicatorError, match="sigma_window"):
        ind.get("CUBIC@1").compute([1.0] * 50, lookback=5, sigma_window=1, z_crit=1.5)


def test_nan_inputs_are_dropped_not_propagated():
    closes = [float("nan")] * 3 + [float(i) for i in range(1, 11)]
    assert ind.get("SMA@1").compute(closes, window=4) == pytest.approx(8.5)


# ---------------------------------------------------------------------------
# 六、G1b：EMA／RSI／BOLL／DONCHIAN／CLOSE
#
# 無限記憶指標（EMA、RSI）的確定性靠「歷史長度慣例」：固定只用最近 10*window 根
# （丟 NaN 後），不足則 NO_SIGNAL。結果因此只取決於參數與這 10*window 根，
# 與資料庫總長度無關——prefix 獨立性測試強制這一點。
# ---------------------------------------------------------------------------

NEW = ["EMA@1", "RSI@1", "BOLL@1", "DONCHIAN_HIGH@1", "DONCHIAN_LOW@1", "CLOSE@1"]


@pytest.mark.parametrize("key", NEW)
def test_new_entries_document_themselves(key):
    s = ind.get(key)
    for field in ("formula", "inputs", "window_semantics", "no_signal", "source", "version"):
        assert getattr(s, field), f"{key} 缺 {field}"


def test_history_bars_new_contracts():
    assert ind.get("EMA@1").history_bars(window=20) == 205
    assert ind.get("RSI@1").history_bars(window=14) == 145
    assert ind.get("BOLL@1").history_bars(window=20, k=2.0) == 25
    assert ind.get("DONCHIAN_HIGH@1").history_bars(window=20) == 26
    assert ind.get("CLOSE@1").history_bars() == 5


# --- 手算值 ------------------------------------------------------------------

def test_ema_core_hand_computed():
    # w=2, alpha=2/3；種子 = 前 2 根平均 = 2；再吃 5：2 + 2/3*(5-2) = 4
    assert ind._ema_core(np.array([1.0, 3.0, 5.0]), 2) == pytest.approx(4.0)


def test_rsi_core_hand_computed():
    # w=2；diffs=[1,-1,2]；種子 avg_gain=0.5 avg_loss=0.5；再吃 gain=2：
    # avg_gain=(0.5*1+2)/2=1.25，avg_loss=(0.5*1+0)/2=0.25，RS=5，RSI=100-100/6
    assert ind._rsi_core(np.array([10.0, 11.0, 10.0, 12.0]), 2) == pytest.approx(100 - 100 / 6)


def test_rsi_conventions_for_degenerate_series():
    w = 3
    n = 10 * w
    assert ind.get("RSI@1").compute([100.0] * n, window=w) == 50.0               # 全平：無資訊
    assert ind.get("RSI@1").compute([float(i) for i in range(n)], window=w) == 100.0   # 只漲
    assert ind.get("RSI@1").compute([float(-i) for i in range(n)], window=w) == 0.0    # 只跌


def test_boll_classic_example():
    out = ind.get("BOLL@1").compute([2, 4, 4, 4, 5, 5, 7, 9], window=8, k=2.0)
    assert (out.mid, out.upper, out.lower) == (5.0, 9.0, 1.0)   # 母體標準差 = 2


def test_donchian_excludes_current_bar():
    c = [1.0, 5.0, 3.0, 4.0, 2.0]
    assert ind.get("DONCHIAN_HIGH@1").compute(c, window=3) == 5.0
    assert ind.get("DONCHIAN_LOW@1").compute(c, window=3) == 3.0
    # 當根創新高時，通道不含它：突破才看得出來
    assert ind.get("DONCHIAN_HIGH@1").compute([1.0, 2.0, 3.0, 99.0], window=3) == 3.0


def test_close_is_current_bar_and_nan_is_no_signal():
    assert ind.get("CLOSE@1").compute([1.0, 2.0, 3.0]) == 3.0
    # 當根缺值不得拿前一根頂替（過期價格當現價）
    assert ind.get("CLOSE@1").compute([1.0, 2.0, float("nan")]) is None
    assert ind.get("CLOSE@1").compute([]) is None


# --- 獨立純 Python 參考實作 ----------------------------------------------------

def _clean(xs):
    return [x for x in xs if not math.isnan(x)]


def _ref_ema(xs, w):
    xs = _clean(xs)
    n = 10 * w
    if len(xs) < n:
        return None
    t = xs[-n:]
    a = 2.0 / (w + 1)
    e = sum(t[:w]) / w
    for v in t[w:]:
        e = e + a * (v - e)
    return e


def _ref_rsi(xs, w):
    xs = _clean(xs)
    n = 10 * w
    if len(xs) < n:
        return None
    t = xs[-n:]
    g = [max(t[i + 1] - t[i], 0.0) for i in range(n - 1)]
    l = [max(t[i] - t[i + 1], 0.0) for i in range(n - 1)]
    ag, al = sum(g[:w]) / w, sum(l[:w]) / w
    for i in range(w, n - 1):
        ag = (ag * (w - 1) + g[i]) / w
        al = (al * (w - 1) + l[i]) / w
    if al == 0:
        return 50.0 if ag == 0 else 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def _ref_boll(xs, w, k):
    xs = _clean(xs)
    if len(xs) < w:
        return None
    t = xs[-w:]
    m = sum(t) / w
    sd = math.sqrt(sum((v - m) ** 2 for v in t) / w)
    return m, m + k * sd, m - k * sd


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("w", [3, 14])
def test_ema_rsi_match_independent_reference(seed, w):
    closes = _series(seed, 12 * w + 40, nan_every=29)
    for key, ref in (("EMA@1", _ref_ema), ("RSI@1", _ref_rsi)):
        spec = ind.get(key)
        for t in range(1, len(closes) + 1):
            win = closes[:t][-spec.history_bars(window=w):]
            got, exp = spec.compute(win, window=w), ref(win, w)
            if exp is None:
                assert got is None, f"{key} t={t}"
            else:
                assert got == pytest.approx(exp, rel=1e-9), f"{key} t={t}"


@pytest.mark.parametrize("seed", range(4))
def test_boll_matches_independent_reference(seed):
    closes = _series(seed, 90, nan_every=13)
    spec = ind.get("BOLL@1")
    for t in range(1, len(closes) + 1):
        win = closes[:t][-spec.history_bars(window=20, k=2.0):]
        got, exp = spec.compute(win, window=20, k=2.0), _ref_boll(win, 20, 2.0)
        if exp is None:
            assert got is None
        else:
            assert (got.mid, got.upper, got.lower) == pytest.approx(exp, rel=1e-9)


# --- 歷史長度慣例：prefix 獨立 ---------------------------------------------------

@pytest.mark.parametrize("key", ["EMA@1", "RSI@1"])
def test_infinite_memory_indicators_ignore_older_history(key):
    w = 5
    base = _series(21, 10 * w)            # 剛好 10*w 根
    older = _series(99, 400)              # 一大段更舊的不同歷史
    spec = ind.get(key)
    a = spec.compute(base, window=w)
    b = spec.compute(older + base, window=w)     # 前面多接 400 根
    assert a is not None and bits(a) == bits(b)


@pytest.mark.parametrize("key", ["EMA@1", "RSI@1"])
def test_infinite_memory_indicators_need_full_convention_length(key):
    w = 5
    assert ind.get(key).compute(_series(5, 10 * w - 1), window=w) is None
    assert ind.get(key).compute(_series(5, 10 * w), window=w) is not None


def test_rsi_bounded():
    for seed in range(10):
        v = ind.get("RSI@1").compute(_series(seed, 200), window=14)
        assert 0.0 <= v <= 100.0


def test_boll_requires_positive_k():
    with pytest.raises(ind.IndicatorError, match="k"):
        ind.get("BOLL@1").compute([1.0] * 30, window=20, k=0.0)
