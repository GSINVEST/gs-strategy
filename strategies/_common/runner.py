"""Run a strategy module from a YAML config file.

Each ``strategy.py`` exposes ``initialize`` / ``handle_data`` / (optional)
``before_trading_start`` / ``analyze``. We load the YAML, attach it onto the
algorithm's ``context.params`` so handlers can read parameters, then call
``run_algorithm``.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: pip install pyyaml") from exc

import pandas as pd

from zipline import run_algorithm
from zipline.utils.calendar_utils import get_calendar


# Futures bundle (`tquant_future`) is registered against TEJ_morning_future.
# Using TEJ_XTAI here would cause session-mismatch errors at run_algorithm time.
TAIWAN_CALENDAR_NAME = "TEJ_morning_future"


def load_config(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict):
        raise ValueError(f"{path} did not parse to a mapping")
    return cfg


def _import_strategy(strategy_path: str | Path) -> ModuleType:
    p = Path(strategy_path).resolve()
    sys.path.insert(0, str(p.parent))
    spec = importlib.util.spec_from_file_location(p.stem, p)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _wrap_initialize(
    user_initialize: Callable, params: Dict[str, Any]
) -> Callable:
    def initialize(context):
        # expose params for the user's handlers to read
        context.params = params
        user_initialize(context)
    return initialize


def run_strategy_from_config(
    strategy_path: str | Path,
    config_path: str | Path,
    output_path: str | Path | None = None,
    *,
    n_trials: int | None = None,
    approved_trial_id: str | None = None,
):
    # strategies/_submitted/ 底下的策略只能經提交佇列的核准路徑執行；其他路徑不受影響。
    from strategies._common.submission.guard import require_approved_for_submitted
    require_approved_for_submitted(strategy_path, approved_trial_id)
    cfg = load_config(config_path)

    start = pd.Timestamp(cfg["start"], tz="UTC")
    end = pd.Timestamp(cfg["end"], tz="UTC")
    capital_base = float(cfg.get("capital_base", 10_000_000))
    bundle = cfg.get("bundle", "tquant_future")
    calendar_name = cfg.get("calendar", TAIWAN_CALENDAR_NAME)

    module = _import_strategy(strategy_path)
    if not hasattr(module, "initialize") or not hasattr(module, "handle_data"):
        raise AttributeError(
            f"{strategy_path} must define initialize() and handle_data()"
        )

    params = cfg.get("params", {})

    kwargs: Dict[str, Any] = dict(
        start=start,
        end=end,
        initialize=_wrap_initialize(module.initialize, params),
        handle_data=module.handle_data,
        capital_base=capital_base,
        bundle=bundle,
        trading_calendar=get_calendar(calendar_name),
        data_frequency="daily",
    )
    if hasattr(module, "before_trading_start"):
        kwargs["before_trading_start"] = module.before_trading_start
    if hasattr(module, "analyze"):
        kwargs["analyze"] = module.analyze

    results = run_algorithm(**kwargs)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        results.to_pickle(out)
        # Best-effort verification sidecar (validation-report-v1) so every
        # backtest artifact carries its PSR/DSR alongside raw Sharpe.
        #
        # Both the annualisation factor and the trial count are *derived from
        # the config* rather than hard-coded here: 252 on a monthly strategy
        # inflates the annualised Sharpe by ~4.6x, and an assumed n_trials=1
        # makes the DSR silently equal the PSR while still being labelled a
        # deflated ratio.  The derivation and its provenance both live in
        # validation.report, and both provenances end up in the sidecar.
        #
        # Disable with STRATEGY_VALIDATION=0; trials via config
        # ``validation.n_trials`` or GS_VALIDATION_N_TRIALS.
        if os.environ.get("STRATEGY_VALIDATION", "1") != "0":
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
                from strategies._common.validation import report as _validation

                # Do not pre-resolve periods_per_year here: the annualisation
                # factor is measured from the perf frame's own timestamps, and
                # only write_sidecar_for has the frame loaded.
                if n_trials is not None:
                    # 佇列傳入 ledger 的 selection 數；不是 config/env 自報的數字。
                    n_trials, n_trials_source = int(n_trials), "ledger"
                else:
                    n_trials, n_trials_source = _validation.resolve_n_trials(cfg)
                manifest = Path(strategy_path).resolve().parent / "manifest.yaml"
                sidecar = _validation.write_sidecar_for(
                    out,
                    cfg=cfg,
                    n_trials=n_trials,
                    n_trials_source=n_trials_source,
                    manifest_path=manifest if manifest.exists() else None,
                )
                if sidecar:
                    doc = json.loads(sidecar.read_text(encoding="utf-8"))
                    print(f"[runner] validation sidecar: {sidecar} "
                          f"(periods_per_year={doc['periods_per_year']} from "
                          f"{doc['periods_per_year_source']}, "
                          f"n_trials={n_trials} from {n_trials_source}, "
                          f"dsr_deflated={doc['dsr_deflated']})")
                    for warn in doc.get("warnings", []):
                        print(f"[runner] ⚠ {warn}")
            except Exception as exc:  # never fatal
                print(f"[runner] validation sidecar skipped: {exc}")
    return results


def _cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True, help="Path to strategy.py")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    parser.add_argument(
        "--output", default=None, help="Optional pickle output for results"
    )
    args = parser.parse_args()
    run_strategy_from_config(args.strategy, args.config, args.output)


if __name__ == "__main__":
    _cli()
