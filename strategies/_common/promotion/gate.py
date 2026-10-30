"""晉升閘門：把既有的統計檢查合成一份可重算的報告。純函式，不寫檔、不呼叫 LLM。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from scripts.triage_generated import count_existing_selection_trials

from . import checks, rules
from .schema import SCHEMA, GateReport, verdict_of

# 對應 quant-backt-tools 的 skills：param-robustness、tail-scenarios、repro-audit、engine-parity
EXTERNAL_CHECKS = ("param_robustness", "tail_scenarios", "repro_audit", "engine_parity")


def _digest(arr) -> str | None:
    """把輸入陣列綁進報告：同樣的數字得到同樣的 sha256，換了輸入就對不上。"""
    if arr is None:
        return None
    a = np.ascontiguousarray(np.asarray(list(arr) if not isinstance(arr, np.ndarray) else arr, dtype="<f8"))
    return hashlib.sha256(str(a.shape).encode("ascii") + b"\0" + a.tobytes()).hexdigest()


@dataclass(frozen=True)
class GateInputs:
    strategy_id: str
    returns: Sequence[float]
    ledger: Path
    candidates: np.ndarray | None = None
    stress_returns: Sequence[float] | None = None
    spa_p: float | None = None
    external: Mapping[str, int | None] = field(default_factory=dict)


def evaluate(inputs: GateInputs) -> GateReport:
    # N 一律讀 ledger；呼叫者沒有任何欄位可以覆寫它。
    n_trials = count_existing_selection_trials(Path(inputs.ledger))
    results = [
        checks.check_sample(inputs.returns),
        checks.check_dsr(inputs.returns, n_trials),
        checks.check_pbo(inputs.candidates),
        checks.check_cost_stress(inputs.stress_returns),
        checks.check_spa(inputs.spa_p),
    ]
    for name in EXTERNAL_CHECKS:
        results.append(checks.check_external(name, inputs.external.get(name)))
    return GateReport(
        schema=SCHEMA,
        rules_version=rules.RULES_VERSION,
        strategy_id=inputs.strategy_id,
        n_trials=n_trials,
        n_trials_source="ledger",
        checks=tuple(results),
        verdict=verdict_of(results),
        inputs_sha256={
            "returns": _digest(inputs.returns),
            "candidates": _digest(inputs.candidates),
            "stress_returns": _digest(inputs.stress_returns),
        },
    )
