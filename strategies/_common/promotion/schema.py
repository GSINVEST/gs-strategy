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
    # 數值由呼叫者手填（SPA p 值、--external 結束碼），閘門無法驗證來源。
    self_declared: bool = False

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
    # 自行申報的數字閘門驗不了來源：即使全 ok 也最多 conditional，須人工簽註。
    if any(c.self_declared for c in checks):
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
    # 輸入陣列的 sha256（把報告綁到實際輸入；None 表示未提供）。
    inputs_sha256: dict[str, str | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["checks"] = [c.to_dict() for c in self.checks]
        return d
