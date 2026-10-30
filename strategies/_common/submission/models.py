"""提交佇列的資料型別。字串值與 genesis-deck 的 Queue.tla／參考實作逐字對齊。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    PENDING = "pending"      # 已提交，未核准；ledger 上沒有對應 trial
    APPROVED = "approved"    # ledger 有 delta_n=1 的 trial 且帶 approval 區塊
    RETIRED = "retired"      # 退出池；trial 不減


class Outcome(str, Enum):
    QUEUED = "queued"
    NOOP = "noop"
    REJECTED = "rejected"
    APPROVED = "approved"
    RETIRED = "retired"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    submission_id: str
    reason: str | None = None        # REJECTED 時指出是哪個守衛擋的


@dataclass(frozen=True)
class Submission:
    """交給 backtest 的資訊。n_trials 是核准當下 ledger 的 selection 總數（含本筆）。"""
    sid: str
    trial_id: str
    bundle_dir: Path
    prereg_id: str
    n_trials: int


@dataclass(frozen=True)
class BacktestRun:
    p_value: float | None = None
    p_value_source: str | None = None
    summary: dict = field(default_factory=dict)


@dataclass(frozen=True)
class QueueSnapshot:
    """唯讀快照，回放 adapter 與 CLI list 共用；全部由 append-only 檔案摺出來。"""
    status: dict[str, str]
    runs: dict[str, int]             # run_started 事件數；不是從 ledger 推得
    trials: dict[str, int]           # ledger 上 t-sub-<sid> 的 delta_n（同 id 取最後一筆）
    approved_by: dict[str, str]
    signed: frozenset[str]
    pool: frozenset[str]             # 已核准、已簽核、未退出
    prereg_ids: frozenset[str]       # git HEAD 上存在的登記檔
