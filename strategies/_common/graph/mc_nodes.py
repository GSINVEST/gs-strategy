"""Monte Carlo robustness node (validation zone: runs after the backtest, adds no N)."""
from __future__ import annotations

from pathlib import Path

from strategies._common import montecarlo
from .core import GraphError, NodeType
from .stat_nodes import returns_frame


def monte_carlo(inputs, p, ctx):
    series = returns_frame(inputs['Returns'])['returns']
    try:
        out = montecarlo.bootstrap(series, n_paths=p['n_paths'], mean_block=p['mean_block'], seed=p['seed'],
                                   periods_per_year=p['periods_per_year'])
    except ValueError as exc:
        raise GraphError('樣本不足，無法做蒙地卡羅重抽（至少 20 筆報酬）') from exc
    # Per-path rows feed the result store's trials table; "_" keys never reach the API.
    paths = out.pop('paths')
    return {'MonteCarlo': {**out, '_paths': paths}}


def register_nodes(registry):
    registry.register(NodeType(
        'validation.monte_carlo', {'Returns': 'Returns'}, {'MonteCarlo': 'MonteCarlo'},
        {'n_paths': {'type': 'integer', 'default': 2000, 'minimum': 100, 'maximum': 20000},
         'mean_block': {'type': 'number', 'default': 10.0, 'minimum': 1, 'maximum': 250},
         'seed': {'type': 'integer', 'default': 7},
         'periods_per_year': {'type': 'integer', 'default': 252, 'minimum': 1}},
        monte_carlo, dependencies=(Path(__file__), Path(montecarlo.__file__))))
