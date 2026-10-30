"""晉升閘門與 registry（見 docs/spec/promotion-gate.md）。"""
from .gate import EXTERNAL_CHECKS, GateInputs, evaluate
from .registry import PromotionError, latest, promote, record_screening, strategy_sha256
from .schema import GateReport, CheckResult

__all__ = [
    "EXTERNAL_CHECKS", "GateInputs", "evaluate",
    "PromotionError", "latest", "promote", "record_screening", "strategy_sha256",
    "GateReport", "CheckResult",
]
