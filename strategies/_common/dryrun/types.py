"""dry run 專用型別。

``SimulatedOrder`` 與實單路徑的 ``Order`` 是不同型別，這個模組不 import 也不定義任何
``Order``，也沒有任何轉換函式（規格 §10 第 6 項）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 規格 §4.1 的延伸；刻意沒有 live 這個狀態，也沒有任何通往它的轉移（§10 第 7 項）。
LIFECYCLE_STATES = ("promoted", "dryrun_active", "paused", "retired")
LIFECYCLE_TRANSITIONS = frozenset({
    ("promoted", "dryrun_active"),
    ("dryrun_active", "paused"),
    ("paused", "dryrun_active"),
    ("dryrun_active", "retired"),
    ("paused", "retired"),
})


@dataclass(frozen=True)
class SimulatedOrder:
    strategy_id: str
    session: str
    symbol: str
    side: int          # +1 買 / -1 賣
    qty: int           # 要求口數（未必全成交）


@dataclass(frozen=True)
class SimulatedFill:
    order: SimulatedOrder
    root_symbol: str
    multiplier: float
    filled_qty: int
    ref_price: Optional[float]
    fill_price: Optional[float]
    spread_points: float
    impact_points: float
    commission_ntd: float
    reason: Optional[str] = None      # 拒絕／部分成交原因（NAN_VOLUME 等）
    flags: tuple = ()


@dataclass(frozen=True)
class DailyRow:
    session: str
    equity: float
    position: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SimResult:
    daily: list
    fills: list
