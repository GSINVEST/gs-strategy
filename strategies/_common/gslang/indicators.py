"""Gslang 指標標準庫（G1a）。

Gslang 的 `指標 x = NAME@N(參數=...)` 一律解析到這裡。每個詞綁定唯一實作與版本，
這是「同一份原始碼永遠得到同一份結果」的前提——歧義（含不含當根、NaN 怎麼處理、
資料不足怎麼辦）在這一層消除，不留給使用者或 LLM。

約定（所有指標共通）：
  * 輸入是**最近 ``history_bars(**params)`` 根**收盤價（舊到新，最後一個是決策當根）。
    可含 NaN，指標自行丟棄；呼叫端不需預處理。
  * 資料不足、非有限值等無法計算的情況回傳 ``None``＝NO_SIGNAL，該 bar 不動作。
    絕不回傳 0 或預設值代替。
  * 參數必須全數明給：缺少或多給都是錯誤，沒有預設值。
  * 版本必填；未知版本不降級（沒有「最新版」這個東西）。

VGRSI@1、CUBIC@1 的數值與 ``strategies/vgrsi_tx``、``strategies/cubic_momentum_tx`` 的
``strategy.py`` 逐位元一致，由 ``tests/test_gslang_indicators.py`` 載入真策略的
``_evaluate`` 比對。演算法本身逐字取自該兩檔（含迴圈順序），不得為了「更快」改寫，
否則浮點加總順序改變會破壞逐位元一致。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, NamedTuple, Sequence

import numpy as np


class IndicatorError(Exception):
    """指標標準庫的錯誤：未知名稱／版本、參數不合法、輸入不合法。"""


class CubicOut(NamedTuple):
    z: float
    score: float


@dataclass(frozen=True)
class IndicatorSpec:
    name: str
    version: int
    params: tuple[str, ...]
    formula: str
    inputs: str
    window_semantics: str
    no_signal: str
    source: str
    _history: Callable[..., int]
    _compute: Callable[..., Any]

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def _check(self, params: dict) -> None:
        missing = [p for p in self.params if p not in params]
        if missing:
            raise IndicatorError(f"{self.key} 缺少參數 {', '.join(missing)}（標準庫不提供預設值）")
        extra = [p for p in params if p not in self.params]
        if extra:
            raise IndicatorError(f"{self.key} 不認得的參數 {', '.join(extra)}；合法參數：{', '.join(self.params)}")

    def history_bars(self, **params: Any) -> int:
        """資料層該備多少根歷史收盤價（含決策當根與餘裕）。"""
        self._check(params)
        return self._history(**params)

    def compute(self, closes: Sequence[float], **params: Any):
        """回傳指標值；無法計算回傳 ``None``（NO_SIGNAL）。"""
        self._check(params)
        arr = np.asarray(closes, dtype=float)
        if arr.ndim != 1:
            raise IndicatorError(f"{self.key} 的輸入必須是一維收盤價序列")
        return self._compute(arr[~np.isnan(arr)], **params)


def _pos_int(name: str, v: Any, minimum: int = 1) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise IndicatorError(f"參數 {name} 必須是 >= {minimum} 的整數，收到 {v!r}")
    return v


# ---------------------------------------------------------------------------
# VGRSI@1 —— 逐字取自 strategies/vgrsi_tx/strategy.py
# ---------------------------------------------------------------------------

def _backward_visibility_degrees(prices: np.ndarray) -> np.ndarray:
    n = len(prices)
    degs = np.zeros((n, 2), dtype=np.int64)
    if n < 2:
        return degs
    for t in range(1, n):
        pt = prices[t]
        for s in range(t - 1, -1, -1):
            ps = prices[s]
            if t - s == 1:
                visible = True
            else:
                ks = np.arange(s + 1, t)
                line = ps + (pt - ps) * (ks - s) / (t - s)
                visible = bool(np.all(prices[ks] < line))
            if visible:
                if ps < pt:
                    degs[t, 0] += 1
                elif ps > pt:
                    degs[t, 1] += 1
                # ps == pt contributes nothing
    return degs


def _vgrsi_value(window_closes: np.ndarray) -> float:
    degs = _backward_visibility_degrees(np.asarray(window_closes, dtype=float))
    up_t, down_t = degs[-1]
    total = up_t + down_t
    if total == 0:
        return 50.0
    return 100.0 * up_t / total


def _vgrsi_history(window: int) -> int:
    _pos_int("window", window)
    return window + 5


def _vgrsi_compute(closes: np.ndarray, window: int):
    _pos_int("window", window)
    tail = closes[-window:]
    if len(tail) < window:
        return None
    return _vgrsi_value(tail)


# ---------------------------------------------------------------------------
# CUBIC@1 —— 逐字取自 strategies/cubic_momentum_tx/strategy.py
# ---------------------------------------------------------------------------

def _cubic_signal(z: float, z_crit: float) -> float:
    if z_crit <= 0:
        return float(np.tanh(z))
    a = 1.0
    b = 1.0 / (3.0 * z_crit ** 2)
    f_z = a * z - b * z ** 3
    f_peak = a * z_crit - b * z_crit ** 3  # = (2/3) * z_crit
    return float(f_z / f_peak) if f_peak != 0 else 0.0


def _cubic_history(lookback: int, sigma_window: int, z_crit: float) -> int:
    _pos_int("lookback", lookback)
    _pos_int("sigma_window", sigma_window, 2)
    return max(lookback, sigma_window) + 5


def _cubic_compute(closes: np.ndarray, lookback: int, sigma_window: int, z_crit: float):
    _pos_int("lookback", lookback)
    _pos_int("sigma_window", sigma_window, 2)
    if len(closes) < max(lookback, sigma_window):
        return None
    diffs = np.diff(closes)
    if len(diffs) < sigma_window:
        return None
    momentum = diffs[-lookback:].sum()
    sigma = diffs[-sigma_window:].std(ddof=1)
    if sigma <= 0 or not np.isfinite(sigma):
        return None
    z = momentum / (sigma * np.sqrt(lookback))
    return CubicOut(z=float(z), score=_cubic_signal(z, z_crit))


# ---------------------------------------------------------------------------
# SMA@1 —— 簡單移動平均；沒有舊策略可對照，改以獨立純 Python 參考實作測試
# ---------------------------------------------------------------------------

def _sma_history(window: int) -> int:
    _pos_int("window", window)
    return window + 5


def _sma_compute(closes: np.ndarray, window: int):
    _pos_int("window", window)
    tail = closes[-window:]
    if len(tail) < window:
        return None
    return float(np.mean(tail))


# ---------------------------------------------------------------------------
# 註冊表
# ---------------------------------------------------------------------------

_SPECS = [
    IndicatorSpec(
        name="VGRSI", version=1, params=("window",),
        formula=("對最近 window 根收盤價建後向可視圖（visibility graph）；對最後一根 t 計算與較低收盤價的可視邊數 up、"
                 "與較高收盤價的可視邊數 down（等價不計）；值 = 100 * up / (up + down)，up+down=0 時為 50。"),
        inputs="收盤價（連續月、add 調整）",
        window_semantics="最後 window 根（含決策當根）；歷史備 window+5 根後丟 NaN，再取最後 window 根",
        no_signal="丟 NaN 後不足 window 根",
        source="strategies/vgrsi_tx/strategy.py:vgrsi_value（Rak 2026, arXiv:2605.01300）",
        _history=_vgrsi_history, _compute=_vgrsi_compute,
    ),
    IndicatorSpec(
        name="CUBIC", version=1, params=("lookback", "sigma_window", "z_crit"),
        formula=("diffs = 收盤價一階差；momentum = 最後 lookback 個 diffs 之和；sigma = 最後 sigma_window 個 diffs 的"
                 "樣本標準差(ddof=1)；z = momentum / (sigma * sqrt(lookback))；score = f(z)/f(z_crit)，"
                 "f(z) = z - z^3/(3 z_crit^2)；z_crit <= 0 時 score = tanh(z)。回傳 (z, score)。"),
        inputs="收盤價（連續月、add 調整）",
        window_semantics="歷史備 max(lookback, sigma_window)+5 根後丟 NaN；diffs 取最後 lookback／sigma_window 個",
        no_signal="丟 NaN 後不足 max(lookback, sigma_window) 根、diffs 不足 sigma_window 個、或 sigma <= 0／非有限",
        source="strategies/cubic_momentum_tx/strategy.py:_evaluate／_cubic_signal",
        _history=_cubic_history, _compute=_cubic_compute,
    ),
    IndicatorSpec(
        name="SMA", version=1, params=("window",),
        formula="最後 window 根收盤價的算術平均。",
        inputs="收盤價",
        window_semantics="最後 window 根（含決策當根）；歷史備 window+5 根後丟 NaN",
        no_signal="丟 NaN 後不足 window 根",
        source="G1a 新增；無對應舊策略，測試對獨立純 Python 參考實作",
        _history=_sma_history, _compute=_sma_compute,
    ),
]

INDICATORS: dict[str, IndicatorSpec] = {s.key: s for s in _SPECS}


def get(key: str) -> IndicatorSpec:
    if "@" not in key:
        raise IndicatorError(f"指標 {key!r} 必須帶版本（例：{key}@1）；標準庫沒有「最新版」")
    if key not in INDICATORS:
        known = ", ".join(sorted(INDICATORS))
        raise IndicatorError(f"未知指標或版本 {key!r}；已註冊：{known}（不會降級到其他版本）")
    return INDICATORS[key]


# ---------------------------------------------------------------------------
# 訊號輔助：分區與取整夾限（Gslang 的 `分區`、`取整夾限`）
# ---------------------------------------------------------------------------

def zone(value: float | None, *, below: float, above: float) -> str:
    """NO_SIGNAL／LOW／MID／HIGH。邊界嚴格：恰等於 below 或 above 歸 MID。"""
    if not below < above:
        raise IndicatorError(f"分區要求 低於 < 高於，收到 below={below}, above={above}")
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NO_SIGNAL"
    if value < below:
        return "LOW"
    if value > above:
        return "HIGH"
    return "MID"


def round_clamp(x: float, lo: int, hi: int) -> int:
    """``int(round(x))`` 再夾在 [lo, hi]。沿用 Python round（銀行家捨入），與原策略一致。"""
    if not math.isfinite(x):
        raise IndicatorError(f"取整夾限的輸入必須是有限值，收到 {x!r}")
    if lo > hi:
        raise IndicatorError(f"取整夾限要求 下限 <= 上限，收到 {lo}, {hi}")
    return max(lo, min(hi, int(round(x))))
