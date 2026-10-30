"""成本壓力：把策略 config 的成本乘以倍數後重跑回測，產生 ``--stress`` 要的報酬序列。

「成本乘二」的定義（docs/spec/promotion-gate.md §8）：``per_contract_cost`` 與
``spread_points`` 同時乘以倍數。config 沒寫成本時 zipline 會用預設值，所以預設值也要展開再乘，
否則「乘二」會悄悄變成沒乘。
"""
from __future__ import annotations

import copy
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml


def _default_costs() -> tuple[dict[str, float], float]:
    # 延遲匯入：futures_setup 在模組層級就 import zipline，純函式測試不需要它。
    from strategies._common.futures_setup import DEFAULT_PER_CONTRACT, DEFAULT_SPREAD_POINTS
    return dict(DEFAULT_PER_CONTRACT), float(DEFAULT_SPREAD_POINTS)


def scaled_config(
    cfg: Mapping[str, Any],
    factor: float = 2.0,
    *,
    defaults: tuple[Mapping[str, float], float] | None = None,
) -> dict[str, Any]:
    """回傳成本乘以 ``factor`` 的新 config（不改動輸入）。``factor`` 必須 >= 1。"""
    if not (isinstance(factor, (int, float)) and math.isfinite(factor) and factor >= 1.0):
        raise ValueError(f"factor 必須是 >= 1 的有限數（壓力只准加重成本），收到 {factor!r}")
    default_cost, default_spread = defaults if defaults is not None else _default_costs()
    out = copy.deepcopy(dict(cfg))
    params = dict(out.get("params") or {})
    cost = dict(default_cost)
    cost.update(params.get("per_contract_cost") or {})
    params["per_contract_cost"] = {k: v * factor for k, v in cost.items()}
    spread = params.get("spread_points")
    params["spread_points"] = float(default_spread if spread is None else spread) * factor
    out["params"] = params
    return out


def _run_strategy(strategy_path, config_path, output_path=None, **kwargs):
    from strategies._common.runner import run_strategy_from_config
    return run_strategy_from_config(strategy_path, config_path, output_path, **kwargs)


def run_cost_stress(strategy_dir: Path, out_path: Path, factor: float = 2.0) -> dict[str, Any]:
    """用乘上 ``factor`` 的成本重跑 ``strategy_dir`` 並把報酬序列寫成 parquet（``returns`` 欄）。

    原 config 不會被修改；縮放後的 config 只寫在暫存目錄。
    """
    sdir = Path(strategy_dir)
    cfg = yaml.safe_load((sdir / "config.yaml").read_text(encoding="utf-8")) or {}
    scaled = scaled_config(cfg, factor)
    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "config.yaml"
        cfg_path.write_text(yaml.safe_dump(scaled, allow_unicode=True, sort_keys=False), encoding="utf-8")
        perf_path = Path(td) / "perf.pkl"
        prior = os.environ.get("STRATEGY_VALIDATION")
        os.environ["STRATEGY_VALIDATION"] = "0"     # 壓力回測不產生 validation sidecar
        try:
            _run_strategy(sdir / "strategy.py", cfg_path, perf_path)
        finally:
            if prior is None:
                os.environ.pop("STRATEGY_VALIDATION", None)
            else:
                os.environ["STRATEGY_VALIDATION"] = prior
        perf = pd.read_pickle(perf_path)
    if "returns" not in getattr(perf, "columns", []):
        raise ValueError("回測結果沒有 returns 欄，無法產生壓力序列")
    returns = perf["returns"].astype(float).reset_index(drop=True)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"returns": returns}).to_parquet(out)
    return {"factor": float(factor), "n_obs": int(returns.size), "out": str(out),
            "per_contract_cost": scaled["params"]["per_contract_cost"],
            "spread_points": scaled["params"]["spread_points"]}
