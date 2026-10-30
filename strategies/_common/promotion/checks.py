"""晉升閘門的各項檢查。全是純函式：同樣輸入必得同樣輸出，不讀檔、不連網。"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from strategies._common.validation.pbo import pbo
from strategies._common.validation.sharpe import deflated_sharpe_ratio

from . import rules
from .schema import BLOCKED, OK, PROVISIONAL, UNAVAILABLE, CheckResult

SELF_DECLARED_NOTE = "（自行申報，閘門未驗證來源）"
MIN_RETURNS_FOR_DSR = 20   # 與 validation/report.py::build_report 的下限一致


def clean_returns(returns: Sequence[float]) -> np.ndarray:
    arr = np.asarray(list(returns), dtype=float)
    return arr[np.isfinite(arr)]


def check_sample(returns: Sequence[float]) -> CheckResult:
    n = int(clean_returns(returns).size)
    if n >= rules.N_OK:
        status = OK
    elif n >= rules.N_PROVISIONAL:
        status = PROVISIONAL
    else:
        status = BLOCKED
    return CheckResult(
        name="sample", status=status, value=float(n),
        threshold=f"n>={rules.N_OK} ok; n>={rules.N_PROVISIONAL} provisional",
        detail=f"有效觀測 {n} 筆",
    )


def check_dsr(returns: Sequence[float], n_trials: int) -> CheckResult:
    r = clean_returns(returns)
    threshold = f"DSR>{rules.DSR_MIN}"
    if r.size < MIN_RETURNS_FOR_DSR:
        return CheckResult("dsr", UNAVAILABLE, None, threshold,
                           f"觀測不足 {MIN_RETURNS_FOR_DSR} 筆，無法計算")
    value = float(deflated_sharpe_ratio(r, n_trials=max(int(n_trials), 1)))
    if not math.isfinite(value):
        return CheckResult("dsr", UNAVAILABLE, None, threshold, "DSR 非有限值")
    if n_trials < 2:
        return CheckResult("dsr", PROVISIONAL, value, threshold,
                           "n_trials=1，DSR 等於 PSR，未 deflate，不得當作未過擬合的依據")
    status = OK if value > rules.DSR_MIN else BLOCKED
    return CheckResult("dsr", status, value, threshold, f"n_trials={n_trials}")


def check_pbo(candidates: np.ndarray | None) -> CheckResult:
    threshold = f"PBO<{rules.PBO_MAX}"
    if candidates is None:
        return CheckResult("pbo", UNAVAILABLE, None, threshold, "未提供候選矩陣")
    m = np.asarray(candidates, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2:
        return CheckResult("pbo", UNAVAILABLE, None, threshold,
                           "PBO 需要至少 2 個候選設定（T×N，N>=2）")
    value = float(pbo(m))
    if not math.isfinite(value):
        return CheckResult("pbo", UNAVAILABLE, None, threshold, "PBO 為 NaN")
    status = OK if value < rules.PBO_MAX else BLOCKED
    return CheckResult("pbo", status, value, threshold, f"候選數 {m.shape[1]}")


def check_cost_stress(stress_returns: Sequence[float] | None) -> CheckResult:
    threshold = "成本乘二後總報酬>0"
    if stress_returns is None:
        return CheckResult("cost_stress", UNAVAILABLE, None, threshold,
                           "未提供成本乘二的報酬序列")
    r = clean_returns(stress_returns)
    if r.size == 0:
        return CheckResult("cost_stress", UNAVAILABLE, None, threshold, "序列為空")
    total = float(np.prod(1.0 + r) - 1.0)
    status = OK if total > 0 else BLOCKED
    return CheckResult("cost_stress", status, total, threshold, "")


def check_spa(p_value: float | None) -> CheckResult:
    threshold = f"SPA p<{rules.SPA_ALPHA}"
    if p_value is None:
        return CheckResult("spa", UNAVAILABLE, None, threshold, "SPA／RC 未跑")
    p = float(p_value)
    if not (0.0 <= p <= 1.0):      # 含 NaN
        return CheckResult("spa", UNAVAILABLE, None, threshold,
                           f"p 值 {p_value!r} 不在 [0,1]，視為未提供" + SELF_DECLARED_NOTE, True)
    status = OK if p < rules.SPA_ALPHA else BLOCKED
    return CheckResult("spa", status, p, threshold, SELF_DECLARED_NOTE.strip(), True)


_EXIT_CODE_STATUS = {0: OK, 1: PROVISIONAL, 2: BLOCKED, 3: UNAVAILABLE}


def check_external(name: str, exit_code: int | None) -> CheckResult:
    """quant-backt-tools 的結束碼：0=PASS、1=WARN／INCONCLUSIVE、2=FAIL、3=輸入錯誤。"""
    threshold = "exit code 0"
    if exit_code is None:
        return CheckResult(name, UNAVAILABLE, None, threshold, "未執行")
    status = _EXIT_CODE_STATUS.get(int(exit_code))
    if status is None:
        return CheckResult(name, UNAVAILABLE, float(exit_code), threshold,
                           f"未知結束碼 {exit_code}" + SELF_DECLARED_NOTE, True)
    return CheckResult(name, status, float(exit_code), threshold,
                       SELF_DECLARED_NOTE.strip(), True)
