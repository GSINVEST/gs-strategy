"""Monte Carlo robustness of one return series (stationary block bootstrap).

Resampling the *observed* returns adds no new performance information, so it
never changes N: it answers "how much of this Sharpe / drawdown is luck of
the ordering?", not "which configuration is best?".  Blocks (Politis &
Romano 1994, mean length ``mean_block``) keep the serial dependence that an
i.i.d. shuffle would destroy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies._common.validation.reality_check import stationary_bootstrap_indices

QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)


def _path_stats(r, periods):
    mean = r.mean(axis=1)
    std = r.std(axis=1, ddof=1)
    sharpe = np.where(std > 0, mean / np.where(std > 0, std, 1) * np.sqrt(periods), 0.0)
    equity = np.cumprod(1 + r, axis=1)
    peak = np.maximum.accumulate(equity, axis=1)
    mdd = (equity / peak - 1).min(axis=1)
    years = r.shape[1] / periods
    cagr = equity[:, -1] ** (1 / years) - 1 if years > 0 else equity[:, -1] - 1
    return sharpe, mdd, cagr, equity


def bootstrap(returns, *, n_paths=2000, mean_block=10.0, seed=7, periods_per_year=252, fan_points=120):
    series = pd.Series(returns).replace([np.inf, -np.inf], np.nan).dropna()
    values = series.to_numpy(dtype=float)
    if len(values) < 20:
        raise ValueError('need at least 20 observations for a bootstrap')
    rng = np.random.default_rng(seed)
    idx = stationary_bootstrap_indices(len(values), float(mean_block), int(n_paths), rng)
    paths = values[idx]
    sharpe, mdd, cagr, equity = _path_stats(paths, periods_per_year)
    observed = _path_stats(values[None, :], periods_per_year)

    def dist(x):
        return {f'p{int(q * 100):02d}': float(np.quantile(x, q)) for q in QUANTILES} | {
            'mean': float(np.mean(x)), 'std': float(np.std(x, ddof=1))}
    step = max(1, len(values) // fan_points)
    cols = list(range(0, len(values), step)) + ([len(values) - 1] if (len(values) - 1) % step else [])
    fan = {f'p{int(q * 100):02d}': np.quantile(equity[:, cols], q, axis=0).round(6).tolist() for q in QUANTILES}
    dates = [pd.Timestamp(series.index[c]).date().isoformat() if isinstance(series.index, pd.DatetimeIndex)
             else int(c) for c in cols]
    return {
        'method': 'stationary block bootstrap (Politis-Romano 1994)',
        'n_paths': int(n_paths), 'mean_block': float(mean_block), 'seed': int(seed), 'n_obs': int(len(values)),
        'observed': {'sharpe': float(observed[0][0]), 'max_drawdown': float(observed[1][0]), 'cagr': float(observed[2][0])},
        'sharpe': dist(sharpe), 'max_drawdown': dist(mdd), 'cagr': dist(cagr),
        'prob_sharpe_le_0': float(np.mean(sharpe <= 0)),
        'prob_loss': float(np.mean(equity[:, -1] < 1)),
        'fan': {'dates': dates, **fan},
        'paths': np.column_stack([sharpe, mdd, cagr]).round(6).tolist(),
    }
