"""D3 L1 成本模型（純函式，不 import zipline）。

依 docs/experiments/2026-10-slippage-volatility-volume-share.md（#53、#56）：
  * 價差項為主：完整價差 ``spread_points`` 點，每邊 ``spread/2``；基準價用收盤價。
  * 成交量上限：每筆最多成交 ``volume_limit * bar_volume`` 口，其餘不成交。
  * 衝擊項只是選配（``impact_enabled``），eta 必須由呼叫端明確給，不得沿用 zipline
    預設 0.049（TX 上等於零，且該類在 fork 原樣丟 TypeError）。
  * NaN 價格／NaN 成交量／σ 或 ADV 缺值：明確拒絕或套保守成本並寫下原因，不靠 zipline 預設。
個股期貨不適用（價差單位是點，不是百分比），v1 不納入。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

COST_MODEL_VERSION = "L1-1"
SCENARIOS = ("optimistic", "base", "pessimistic")
SENSITIVITY_SPREADS = (2.0, 4.0, 6.0)        # 情境分析用的完整價差檔位（§6.2）
ZIPLINE_DEFAULT_ETA = 0.049018                # 只用來拒絕，不用來計算
OPTIMISTIC_SPREAD = 2.0                       # Roll 代理上緣附近（#53：0.7–2.3 點）


@dataclass(frozen=True)
class L1Params:
    spread_points: float = 6.0
    commission_mult: float = 1.0
    volume_limit: float = 0.05
    impact_enabled: bool = False
    eta: Optional[float] = None
    nodata_fallback_bp: float = 7.5           # 與 zipline NO_DATA 後備同量級，但這裡明寫並標記

    def __post_init__(self) -> None:
        if not (self.spread_points >= 0):
            raise ValueError("spread_points must be >= 0")
        if not (0 < self.volume_limit <= 1):
            raise ValueError("volume_limit must be in (0, 1]")
        if not (self.commission_mult > 0):
            raise ValueError("commission_mult must be > 0")
        if self.impact_enabled:
            if self.eta is None or not (self.eta > 0):
                raise ValueError("impact_enabled requires an explicit calibrated eta > 0")
            if abs(self.eta - ZIPLINE_DEFAULT_ETA) < 1e-9:
                raise ValueError("zipline default eta is not allowed (uncalibrated for TX)")

    def to_dict(self) -> dict:
        return {"spread_points": self.spread_points, "commission_mult": self.commission_mult,
                "volume_limit": self.volume_limit, "impact_enabled": self.impact_enabled,
                "eta": self.eta, "nodata_fallback_bp": self.nodata_fallback_bp}


def scenario_params(name: str, base_spread: float = 6.0, **overrides) -> L1Params:
    """三情境：樂觀（2 點）／基準（預設 6 點）／悲觀（基準成本 ×2，含手續費）。"""
    if name == "optimistic":
        return L1Params(spread_points=min(OPTIMISTIC_SPREAD, base_spread), **overrides)
    if name == "base":
        return L1Params(spread_points=base_spread, **overrides)
    if name == "pessimistic":
        return L1Params(spread_points=2 * base_spread, commission_mult=2.0, **overrides)
    raise KeyError(f"unknown scenario {name!r}; expected one of {SCENARIOS}")


@dataclass(frozen=True)
class FillDecision:
    filled_qty: int
    price: Optional[float]
    spread_points: float = 0.0     # 每口每邊的價差點數（成交價相對基準價）
    impact_points: float = 0.0
    reason: Optional[str] = None   # 拒絕或部分成交的原因
    flags: tuple = ()


def _bad_number(x) -> bool:
    return x is None or x != x


def decide_fill(params: L1Params, *, side: int, qty: int, ref_price, bar_volume,
                adv=None, sigma=None) -> FillDecision:
    if side not in (1, -1):
        raise ValueError("side must be +1 or -1")
    if int(qty) < 1:
        raise ValueError("qty must be >= 1")
    qty = int(qty)

    if _bad_number(ref_price) or ref_price <= 0:
        return FillDecision(0, None, reason="NAN_PRICE")
    if _bad_number(bar_volume):
        return FillDecision(0, None, reason="NAN_VOLUME")
    if bar_volume <= 0:
        return FillDecision(0, None, reason="ZERO_VOLUME")

    cap = int(math.floor(params.volume_limit * bar_volume))
    if cap < 1:
        return FillDecision(0, None, reason="VOLUME_CAP_ZERO")
    filled = min(qty, cap)
    reason = "PARTIAL_VOLUME_CAP" if filled < qty else None

    half = params.spread_points / 2.0
    impact = 0.0
    flags: tuple = ()
    if params.impact_enabled:
        if _bad_number(adv) or adv <= 0 or _bad_number(sigma) or sigma < 0:
            bps = params.nodata_fallback_bp
            flags = ("NO_DATA_FALLBACK",)
        else:
            bps = params.eta * sigma * math.sqrt(filled / adv)
        impact = ref_price * bps / 1e4
    price = ref_price + side * (half + impact)
    return FillDecision(filled, float(price), spread_points=half, impact_points=impact,
                        reason=reason, flags=flags)
