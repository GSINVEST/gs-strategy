from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

SCHEMA = "promotion-report/1"

OK = "ok"
PROVISIONAL = "provisional"
BLOCKED = "blocked"
UNAVAILABLE = "unavailable"
STATUSES = (OK, PROVISIONAL, BLOCKED, UNAVAILABLE)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    value: float | None
    threshold: str
    detail: str

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}; expected one of {STATUSES}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verdict_of(checks: Sequence[CheckResult]) -> str:
    """blocked > conditional > eligible。

    ``unavailable`` 與 ``provisional`` 都只能得到 conditional：缺席的檢查不等於通過。
    """
    statuses = {c.status for c in checks}
    if BLOCKED in statuses:
        return "blocked"
    if statuses & {PROVISIONAL, UNAVAILABLE}:
        return "conditional"
    return "eligible"


@dataclass(frozen=True)
class GateReport:
    schema: str
    rules_version: str
    strategy_id: str
    n_trials: int
    n_trials_source: str
    checks: tuple[CheckResult, ...] = field(default_factory=tuple)
    verdict: str = "blocked"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["checks"] = [c.to_dict() for c in self.checks]
        return d
