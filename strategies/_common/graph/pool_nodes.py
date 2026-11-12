"""Coarse-graph nodes for any strategy in the gs-zipline-tej pool.

A strategy that has not been decomposed into feature/signal nodes still gets
the full pipeline downstream of its returns: one selection trial per new
(bundle content, window, capital) combination, the validation report and the
statistical decision tree.  The run itself goes through gs-zipline-tej's
``run_backtest`` in the backtest interpreter.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd

from strategies._common import pool
from .core import GraphError, NodeType

HERE = Path(__file__).parent


def strategy_bundle(inputs, p, ctx):
    meta = pool.entry(p['strategy_id'])
    if p['bundle_version'] in ('auto', 'initial'):
        raise GraphError('pin bundle_version with prepare_graph before execution')
    if pool.bundle_version(meta) != p['bundle_version']:
        raise GraphError('策略原始碼已變更，請重新執行預判')
    keep = ('id', 'name', 'description', 'asset_class', 'bundle', 'calendar', 'origin', 'tags', 'symbols',
            'params', 'requires_tej_key', 'provenance', 'validation')
    return {'StrategyBundle': {'meta': {k: meta.get(k) for k in keep}, 'window': dict(p),
                               'version': p['bundle_version']}}


def strategy_spec(inputs, p, ctx):
    """What the bundle declares about itself (manifest); documentation, not computation."""
    meta = inputs['StrategyBundle']['meta']
    return {'StrategySpec': {
        'asset_class': meta.get('asset_class'), 'bundle': meta.get('bundle'), 'calendar': meta.get('calendar'),
        'universe': meta.get('symbols') or [], 'params': meta.get('params') or {},
        'tags': meta.get('tags') or [], 'origin': meta.get('origin'),
        'validation': meta.get('validation') or {}, 'source': meta.get('provenance') or {}}}


def pool_zipline(inputs, p, ctx):
    bundle = inputs['StrategyBundle']
    window = bundle['window']
    ctx.progress({'phase': 'backtest', 'completed': 0, 'total': 1})
    result = pool.run_backtest(window['strategy_id'], start=window['start'], end=window['end'],
                               capital_base=window['capital_base'])
    return _outputs(result, ctx)


def pool_factor(inputs, p, ctx):
    """A Factor Pool bundle used as a stock-selection filter (gs-zipline-tej factor harness)."""
    window = inputs['StrategyBundle']['window']
    ctx.progress({'phase': 'backtest', 'completed': 0, 'total': 1})
    result = pool.run_factor_backtest(window['strategy_id'], dict(p), start=window['start'], end=window['end'],
                                      capital_base=window['capital_base'])
    return _outputs(result, ctx)


def _outputs(result, ctx):
    ctx.check_cancelled()
    if result.get('status') != 'ok' or not result.get('parquet_path'):
        raise GraphError('gs-zipline-tej 回測失敗，請在該 repo 的 dashboard 檢視錯誤')
    parquet = Path(result['parquet_path'])
    # The runner reports paths relative to the gs-zipline-tej checkout.
    frame = pd.read_parquet(parquet if parquet.is_absolute() else pool.zipline_root() / parquet)
    index = pd.DatetimeIndex(pd.to_datetime(frame['dt'], utc=True))
    returns = pd.Series(frame['returns'].to_numpy(dtype=float), index=index, name='returns')
    exposure = [c for c in ('gross_leverage', 'net_leverage') if c in frame.columns]
    positions = (frame[exposure].set_axis(index) if exposure
                 else pd.DataFrame({'portfolio_value': frame['portfolio_value'].to_numpy()}, index=index))
    return {'Returns': returns, 'Positions': positions,
            'RunInfo': {'runner_run_id': result.get('run_id'), 'metrics': result.get('metrics') or {},
                        'engine': 'zipline (gs-zipline-tej runner)'}}


def prepare_graph(graph):
    graph = copy.deepcopy(graph)
    for node in graph['nodes']:
        if node['type'] == 'data.pool_strategy':
            params = node.setdefault('params', {})
            if params.get('bundle_version', 'auto') in ('auto', 'initial'):
                params['bundle_version'] = pool.bundle_version(pool.entry(params['strategy_id']))
    return graph


def register_nodes(registry):
    def param(kind, default, **extra):
        return {'type': kind, 'default': default, **extra}
    deps = (HERE / 'pool_nodes.py', Path(pool.__file__))
    registry.register(NodeType(
        'data.pool_strategy', {}, {'StrategyBundle': 'StrategyBundle'},
        {'strategy_id': param('string', ''), 'start': param('string', ''), 'end': param('string', ''),
         'capital_base': param('number', 1_000_000, minimum=1), 'bundle_version': param('string', 'auto')},
        strategy_bundle, dependencies=deps))
    registry.register(NodeType(
        'feature.strategy_spec', {'StrategyBundle': 'StrategyBundle'}, {'StrategySpec': 'StrategySpec'}, {},
        strategy_spec, dependencies=deps))
    registry.register(NodeType(
        'backtest.pool_zipline', {'StrategyBundle': 'StrategyBundle', 'StrategySpec': 'StrategySpec'},
        {'Returns': 'Returns', 'Positions': 'Positions', 'RunInfo': 'RunInfo'}, {},
        pool_zipline, dependencies=deps))
    # Filter fields mirror gs-zipline-tej dashboard/factor_filter.py (validated there).
    registry.register(NodeType(
        'backtest.pool_factor', {'StrategyBundle': 'StrategyBundle', 'StrategySpec': 'StrategySpec'},
        {'Returns': 'Returns', 'Positions': 'Positions', 'RunInfo': 'RunInfo'},
        {'mode': param('string', 'long_only_topn', enum=['long_only_topn', 'long_short', 'threshold']),
         'direction': param('string', 'high', enum=['high', 'low']),
         'n': param('integer', 20, minimum=1, maximum=500),
         'weighting': param('string', 'equal', enum=['equal', 'factor_proportional']),
         'rebalance': param('string', 'monthly', enum=['daily', 'weekly', 'monthly'])},
        pool_factor, dependencies=deps))
