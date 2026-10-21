"""Verification report builder — turn a zipline perf frame into the
``validation:`` block promised by strategy-import-spec v1.2 §2.

Reuses the self-written statistics in this package (sharpe.py: PSR/DSR;
see also pbo.py / cpcv.py). The point: a strategy's Sharpe should never be
shown without its *deflated* counterpart once multiple trials were tried.

Two honesty rules drive the extra bookkeeping fields in the report:

* **Frequency is derived, never guessed.**  Annualising a monthly strategy
  with 252 inflates its Sharpe by ~4.6x.  We derive periods-per-year from the
  strategy config and record *where the number came from* in
  ``periods_per_year_source``, so a 252 fallback stays visible instead of
  silent.
* **A DSR computed with an unknown trial count is not a DSR.**
  At ``n_trials=1`` the expected-max-SR benchmark in ``sharpe.py`` degenerates
  (``norm.ppf(1 - 1/1) == -inf``) and ``deflated_sharpe_ratio`` returns
  **exactly 1.0 for every input series** — measured, not assumed.  Shown
  unqualified next to a Sharpe, that constant reads as a perfect score from a
  multiple-testing correction the strategy never actually faced.
  ``n_trials_source`` / ``dsr_deflated`` / ``warnings`` mark the case loudly.
  The remedy is a truthful N, not a prettier number, so the maths in
  ``sharpe.py`` is left untouched here.

CLI::

    python -m strategies._common.validation.report PERF.parquet|.pkl \
        [--n-trials N] [--periods-per-year 252 | --frequency monthly] \
        [-o out.json] [--manifest strategies/foo/manifest.yaml]

Accepts dashboard runner parquet output or a raw zipline perf pickle.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .sharpe import (
    annualized_sharpe,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)

SCHEMA = "validation-report-v1"

#: Environment fallback for the trial count (lowest priority above default).
N_TRIALS_ENV_VAR = "GS_VALIDATION_N_TRIALS"

DEFAULT_PERIODS_PER_YEAR = 252

#: Frequency label -> periods per year.
#:
#: ``minute`` is deliberately mapped to the *daily* 252 rather than to a count
#: of minute bars: zipline's ``data_frequency`` describes the bar size fed to
#: ``handle_data``, but the perf frame this module consumes always carries
#: **daily** ``returns`` rows regardless of that setting.  Annualising those
#: daily rows with a minute-bar count would be wrong by two orders of
#: magnitude, so minute inherits the daily 252.
FREQUENCY_PERIODS_PER_YEAR: dict[str, int] = {
    "daily": 252,
    "weekly": 52,
    "monthly": 12,
    "minute": 252,
}


def periods_per_year_for(freq: str | None) -> int | None:
    """Map a frequency declaration to periods-per-year.

    Returns ``None`` when *freq* is missing or is not a frequency we
    recognise — callers must then decide what to do, and are expected to
    record that the number they used was a fallback, not a derivation.
    """
    if not isinstance(freq, str):
        return None
    return FREQUENCY_PERIODS_PER_YEAR.get(freq.strip().lower())


def infer_periods_per_year(perf: "pd.DataFrame") -> tuple[int, str] | tuple[None, None]:
    """Measure the observation frequency of *perf* from its own timestamps.

    This is the only source that cannot be wrong by construction: annualising
    a return series depends on how often it is *observed*, not on how often
    the strategy rebalances. A monthly-rebalanced zipline algo still emits one
    perf row per trading day, and annualising those daily rows at 12/year
    understates CAGR by more than an order of magnitude.

    Looks for a dt column, then a DatetimeIndex. Returns
    (periods_per_year, "inferred:<label>") or (None, None) when the
    frame carries no usable timestamps.
    """
    stamps = None
    if "dt" in getattr(perf, "columns", ()):
        stamps = pd.to_datetime(perf["dt"], errors="coerce")
    elif isinstance(getattr(perf, "index", None), pd.DatetimeIndex):
        stamps = pd.Series(perf.index)
    if stamps is None:
        return None, None
    stamps = stamps.dropna().sort_values()
    if len(stamps) < 3:
        return None, None
    gap_days = stamps.diff().dt.total_seconds().dropna().median() / 86400.0
    if not gap_days or gap_days <= 0:
        return None, None
    # Snap to the nearest canonical calendar, in log space so the choice is
    # scale-free (a 3-day median gap is "daily with weekends", not "weekly").
    best = min(FREQUENCY_PERIODS_PER_YEAR.items(),
               key=lambda kv: abs(math.log(365.25 / gap_days) - math.log(kv[1])))
    return best[1], f"inferred:{best[0]}"


def resolve_periods_per_year(cfg: Mapping[str, Any] | None,
                             perf: "pd.DataFrame | None" = None,
                             ) -> tuple[int, str]:
    """Decide the annualisation factor for a return series, with provenance.

    Priority:

    1. ``validation.periods_per_year`` in the config -- an outright
       statement, for a caller feeding in a series this module cannot
       measure.
    2. Inferred from the perf frame's own timestamps (see
       ``infer_periods_per_year``) -- measured, not declared.
    3. 252 with source "default", which the report flags as an
       assumption rather than a derivation.

    Deliberately **not** consulted: the manifest's rebalance key. Rebalance
    cadence is a strategy decision frequency and says nothing about how often
    the resulting return series is sampled; wiring it in here produced a CAGR
    error of 17.66% -> 0.78% on a one-year daily backtest.
    """
    if isinstance(cfg, Mapping):
        validation = cfg.get("validation")
        if isinstance(validation, Mapping):
            explicit = validation.get("periods_per_year")
            try:
                if explicit is not None and int(explicit) > 0:
                    return int(explicit), "validation.periods_per_year"
            except (TypeError, ValueError):
                pass
    if perf is not None:
        ppy, src = infer_periods_per_year(perf)
        if ppy is not None:
            return ppy, src
    return DEFAULT_PERIODS_PER_YEAR, "default"


def resolve_n_trials(cfg: Mapping[str, Any] | None = None,
                     env: Mapping[str, str] | None = None) -> tuple[int, str]:
    """Resolve the number of trials behind this backtest, with provenance.

    Priority: ``validation.n_trials`` in the config > ``GS_VALIDATION_N_TRIALS``
    in the environment > ``1``.  Returns ``(n_trials, source)`` with *source*
    in ``{"config", "env", "default"}``.

    We never invent a plausible-looking N to make the DSR column respectable;
    an unknown trial count is reported as unknown.
    """
    if isinstance(cfg, Mapping):
        validation = cfg.get("validation")
        if isinstance(validation, Mapping):
            raw = validation.get("n_trials")
            try:
                if raw is not None and int(raw) >= 1:
                    return int(raw), "config"
            except (TypeError, ValueError):
                pass
    env = os.environ if env is None else env
    raw_env = env.get(N_TRIALS_ENV_VAR)
    if raw_env is not None:
        try:
            if int(raw_env) >= 1:
                return int(raw_env), "env"
        except (TypeError, ValueError):
            pass
    return 1, "default"


UNDEFLATED_DSR_WARNING = (
    "n_trials = 1，DSR 未經 deflate：deflation benchmark 為 0，因此 DSR 等於 "
    "PSR，未做任何多重檢定校正，不得當作策略未過擬合的依據。請在 "
    "config.yaml 的 validation.n_trials 或環境變數 GS_VALIDATION_N_TRIALS "
    "誠實填入實際試過的參數組數（含被丟棄的那些）。"
)

ASSUMED_FREQUENCY_WARNING = (
    "無法從 perf 的時間戳量出觀測頻率（缺 dt 欄與 DatetimeIndex，或列數 < 3），"
    "年化係數退回預設 252（日頻）。若這份報酬序列不是日頻，年化 Sharpe 與 "
    "CAGR 都會錯；請在 config.yaml 的 validation.periods_per_year 直接指定。"
)


def load_perf(path: Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"perf file not found: {path}")
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    return pd.read_pickle(path)


def max_drawdown(returns: pd.Series) -> float:
    equity = (1 + returns.fillna(0)).cumprod()
    return float((equity / equity.cummax() - 1).min())


def cagr(returns: pd.Series,
         periods_per_year: int = DEFAULT_PERIODS_PER_YEAR) -> float:
    r = returns.fillna(0)
    years = len(r) / periods_per_year
    total = float((1 + r).prod())
    if years <= 0 or total <= 0:
        return float("nan")
    return total ** (1 / years) - 1


def build_report(perf: pd.DataFrame, *, n_trials: int = 1,
                 n_trials_source: str = "default",
                 periods_per_year: int | None = None,
                 periods_per_year_source: str | None = None,
                 returns_col: str = "returns") -> dict:
    if returns_col not in perf.columns:
        raise ValueError(
            f"perf frame has no {returns_col!r} column "
            f"(has: {list(perf.columns)[:12]}...)")
    returns = pd.Series(perf[returns_col]).astype(float).replace(
        [np.inf, -np.inf], np.nan).dropna()
    if len(returns) < 20:
        raise ValueError(
            f"too few return observations ({len(returns)}) for validation")

    n_trials = max(int(n_trials), 1)

    # Measure the annualisation factor from the frame we were handed unless
    # the caller stated one. build_report() holds the timestamps, so making
    # the caller supply this was a footgun: forgetting it silently annualised
    # a monthly series at 252 and reported a 296% CAGR.
    if periods_per_year is None:
        measured, measured_src = infer_periods_per_year(perf)
        if measured is None:
            periods_per_year = DEFAULT_PERIODS_PER_YEAR
            periods_per_year_source = periods_per_year_source or "default"
        else:
            periods_per_year = measured
            periods_per_year_source = periods_per_year_source or measured_src
    elif periods_per_year_source is None:
        periods_per_year_source = "explicit"
    periods_per_year = max(int(periods_per_year), 1)

    # dsr_deflated answers one question only: was the Sharpe actually
    # deflated against a non-zero benchmark? That takes N >= 2 — regardless of
    # where the N came from. At N == 1 the benchmark is 0 by construction (see
    # sharpe.deflated_sharpe_ratio) so DSR == PSR and no multiple-testing
    # correction has been applied. Deriving this from n_trials_source
    # instead would let anyone write validation: {n_trials: 1} and earn a
    # "deflated" label for an undeflated number.
    dsr_deflated = n_trials >= 2

    warnings: list[str] = []
    if not dsr_deflated:
        warnings.append(UNDEFLATED_DSR_WARNING)
    if periods_per_year_source == "default":
        warnings.append(ASSUMED_FREQUENCY_WARNING)

    return {
        "schema": SCHEMA,
        "n_days": int(len(returns)),
        "total_return": float((1 + returns).prod() - 1),
        "cagr": cagr(returns, periods_per_year),
        "annualized_sharpe": annualized_sharpe(returns, periods_per_year),
        "max_drawdown": max_drawdown(returns),
        "psr": probabilistic_sharpe_ratio(returns),
        "dsr": deflated_sharpe_ratio(returns, n_trials=n_trials),
        "n_trials": n_trials,
        "n_trials_source": n_trials_source,
        "dsr_deflated": dsr_deflated,
        "periods_per_year": periods_per_year,
        "periods_per_year_source": periods_per_year_source,
        "warnings": warnings,
    }


def write_sidecar_for(perf_path: Path, *,
                      cfg: Mapping[str, Any] | None = None,
                      n_trials: int | None = None,
                      n_trials_source: str | None = None,
                      periods_per_year: int | None = None,
                      periods_per_year_source: str | None = None,
                      manifest_path: Path | None = None,
                      graph_hash: str | None = None,
                      graph_snapshot: Mapping[str, Any] | None = None) -> Path | None:
    """Best-effort: write ``<perf>.validation.json`` next to a perf file.

    Callers may either hand over already-derived numbers (what the runner
    does) or a *cfg* mapping to derive them from here.  Explicit arguments win
    over anything derived from *cfg*.

    Validation must never break a completed backtest, so this swallows all
    errors and returns the sidecar path on success / None on failure.
    """
    try:
        perf = load_perf(Path(perf_path))

        if n_trials is None:
            resolved_trials, trials_src = resolve_n_trials(cfg)
        else:
            resolved_trials, trials_src = int(n_trials), "explicit"
        if n_trials_source:
            trials_src = n_trials_source

        if periods_per_year is None:
            # Pass the loaded frame so the annualisation factor is measured
            # from the return series itself rather than assumed.
            resolved_ppy, ppy_src = resolve_periods_per_year(cfg, perf)
        else:
            resolved_ppy, ppy_src = int(periods_per_year), "explicit"
        if periods_per_year_source:
            ppy_src = periods_per_year_source

        report = build_report(perf,
                              n_trials=resolved_trials,
                              n_trials_source=trials_src,
                              periods_per_year=resolved_ppy,
                              periods_per_year_source=ppy_src)
        if graph_hash is not None:
            report['graph_hash'] = graph_hash
            report['graph_snapshot'] = graph_snapshot
        sidecar = Path(perf_path).with_name(
            Path(perf_path).name + ".validation.json")
        sidecar.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        if manifest_path is not None:
            apply_to_manifest(Path(manifest_path), report)
        return sidecar
    except Exception:
        return None


# --------------------------------------------------------------------------
# manifest write-back (strategy-import-spec v1.2 §2)
# --------------------------------------------------------------------------

def _fmt_float(value: Any, digits: int = 4) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not np.isfinite(f):
        return "nan"
    return f"{f:.{digits}f}"


def _fmt_int(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


#: report key -> (manifest key, formatter).  Only the externally meaningful
#: numbers make the cut; the sidecar JSON stays the full record.
_MANIFEST_FIELDS: tuple[tuple[str, str, Any], ...] = (
    ("schema", "schema", str),
    ("graph_hash", "graph_hash", str),
    ("annualized_sharpe", "annualized_sharpe", lambda v: _fmt_float(v, 4)),
    ("psr", "psr", lambda v: _fmt_float(v, 4)),
    ("dsr", "dsr", lambda v: _fmt_float(v, 4)),
    ("max_drawdown", "max_drawdown", lambda v: _fmt_float(v, 4)),
    ("cagr", "cagr", lambda v: _fmt_float(v, 4)),
    ("total_return", "total_return", lambda v: _fmt_float(v, 4)),
    ("n_days", "n_days", _fmt_int),
    ("n_trials", "n_trials", _fmt_int),
    ("n_trials_source", "n_trials_source", str),
    ("dsr_deflated", "dsr_deflated", lambda v: "true" if v else "false"),
    ("periods_per_year", "periods_per_year", _fmt_int),
    ("periods_per_year_source", "periods_per_year_source", str),
)


def manifest_validation_block(report: Mapping[str, Any]) -> dict[str, str]:
    """Format a report into the string->string mapping the importer demands.

    ``dashboard/strategies/importer.py`` rejects a manifest whose
    ``validation:`` block holds anything but strings
    (``"validation must be a string->string mapping"``), so every number is
    rendered here to a fixed number of digits rather than left as a float —
    ``str(0.8412345678901234)`` is both illegal and unreadable.
    """
    block: dict[str, str] = {}
    for report_key, manifest_key, fmt in _MANIFEST_FIELDS:
        if report.get(report_key) is not None:
            block[manifest_key] = fmt(report[report_key])
    warnings = report.get("warnings")
    if isinstance(warnings, (list, tuple)) and warnings:
        block["warnings"] = " / ".join(str(w) for w in warnings)
    return block


def apply_to_manifest(manifest_path: Path, report: Mapping[str, Any]) -> bool:
    """Write *report* back into a strategy manifest's ``validation:`` key.

    Keeps every other key of the manifest untouched and replaces only
    ``validation``.  Returns True on success, False when the file is missing,
    unreadable, not YAML, or does not parse to a mapping — never raises, on
    the same best-effort principle as :func:`write_sidecar_for` (validation
    must never break a backtest, and must certainly never corrupt a manifest).
    """
    try:
        import yaml  # local import: report.py stays importable without PyYAML
    except ImportError:
        return False
    try:
        path = Path(manifest_path)
        if not path.is_file():
            return False
        with path.open("r", encoding="utf-8") as fh:
            manifest = yaml.safe_load(fh)
        if not isinstance(manifest, dict):
            return False
        block = manifest_validation_block(report)
        if not block:
            return False
        manifest["validation"] = block
        text = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False,
                              default_flow_style=False)
        path.write_text(text, encoding="utf-8")
        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="validation.report")
    parser.add_argument("perf", type=Path)
    parser.add_argument("--n-trials", type=int, default=None,
                        help="how many trials produced this backtest "
                             "(deflates SR); omit to fall back to "
                             f"${N_TRIALS_ENV_VAR} then to 1")
    parser.add_argument("--periods-per-year", type=int, default=None)
    parser.add_argument("--frequency", default=None,
                        choices=sorted(FREQUENCY_PERIODS_PER_YEAR),
                        help="derive --periods-per-year from a frequency "
                             "label instead of stating the number")
    parser.add_argument("-o", "--out", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None,
                        help="also write the report back into this "
                             "manifest.yaml's validation: block")
    args = parser.parse_args(argv)

    if args.n_trials is None:
        n_trials, n_trials_source = resolve_n_trials()
    else:
        n_trials, n_trials_source = args.n_trials, "cli"

    if args.periods_per_year is not None:
        periods_per_year, ppy_source = args.periods_per_year, "cli"
    elif args.frequency is not None:
        periods_per_year = periods_per_year_for(args.frequency)
        ppy_source = "cli:frequency"
    else:
        # None, not DEFAULT_PERIODS_PER_YEAR: hand the decision to
        # build_report, which measures the annualisation factor from the perf
        # frame's own timestamps. Passing the default here short-circuited
        # that -- the CLI annualised every series at 252 and then emitted a
        # warning claiming the timestamps could not be read, when it had
        # never looked. The runner path was unaffected (it goes through
        # write_sidecar_for, which passes the loaded frame down).
        periods_per_year, ppy_source = None, None

    try:
        report = build_report(load_perf(args.perf),
                              n_trials=n_trials,
                              n_trials_source=n_trials_source,
                              periods_per_year=periods_per_year,
                              periods_per_year_source=ppy_source)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    if args.manifest:
        ok = apply_to_manifest(args.manifest, report)
        state = "ok" if ok else "skipped"
        print(f"[report] manifest write-back {state}: {args.manifest}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
