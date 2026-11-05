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
