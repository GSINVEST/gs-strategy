"""Strategy pool switching (gs-zipline-tej as an interface) and the result store."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategies._common import pool, results
from strategies._common.graph.core import GraphError
from strategies._common.graph.service import GraphService


@pytest.fixture
def fake_pool(tmp_path, monkeypatch):
    """A pool with one external bundle; the runner writes a parquet like gs-zipline-tej's."""
    bundle = tmp_path / 'ext' / 'demo_ma'
    bundle.mkdir(parents=True)
    (bundle / 'strategy.py').write_text('def initialize(context):\n    pass\n', encoding='utf-8')
    (bundle / 'manifest.yaml').write_text('id: demo_ma\n', encoding='utf-8')
    meta = {'id': 'demo_ma', 'name': 'Demo MA', 'origin': 'external', 'pool': 'strategy', 'asset_class': 'equity',
            'bundle': 'tquant', 'start': '2023-01-02', 'end': '2023-06-30', 'capital_base': 1_000_000,
            'bundle_dir': str(bundle), 'graph': 'coarse', 'graph_path': None, 'selectable': True, 'tags': []}
    listing = {'strategies': [meta], 'factors': [], 'import_errors': 0, 'pool_error': None, 'zipline_root': 'fake'}
    monkeypatch.setattr(pool, 'pool', lambda refresh=False: listing)
    monkeypatch.setattr(pool, 'zipline_root', lambda: tmp_path)
    calls = []

    def run_backtest(strategy_id, *, start, end, capital_base, timeout=900):
        calls.append(strategy_id)
        days = pd.bdate_range(start, end, tz='UTC')
        rng = np.random.default_rng(len(calls))
        frame = pd.DataFrame({'dt': days, 'returns': rng.normal(0.0005, 0.01, len(days)),
                              'portfolio_value': capital_base, 'gross_leverage': 1.0})
        (tmp_path / 'reports').mkdir(exist_ok=True)
        frame.to_parquet(tmp_path / 'reports' / 'run.parquet', index=False)
        return {'status': 'ok', 'run_id': 'r1', 'parquet_path': 'reports/run.parquet', 'metrics': {'sharpe': 1.0}}
    monkeypatch.setattr(pool, 'run_backtest', run_backtest)
    return {'meta': meta, 'bundle': bundle, 'calls': calls}


def service(tmp_path):
    return GraphService(tmp_path / 'ws', ledger_path=tmp_path / 'ws' / 'log' / 'trials.jsonl',
                        cache_dir=tmp_path / 'ws' / 'cache')


def run(svc):
    job = svc.start()
    svc._thread.join()
    return svc.job(job['id'])


def test_select_builds_a_coarse_graph_and_every_run_is_stored(tmp_path, fake_pool):
    svc = service(tmp_path)
    doc = svc.select_strategy('demo_ma')
    assert doc['path'] == 'strategies/_pool/demo_ma/graph.json'
    assert {n['type'] for n in doc['graph']['nodes']} >= {'data.pool_strategy', 'feature.strategy_spec',
                                                          'backtest.pool_zipline', 'stat.resolve'}
    first = run(svc)
    assert first['status'] == 'complete', first.get('message')
    assert svc.ledger.summary()['selection_n'] == 1
    again = run(svc)                                   # cached replay: a run, not a trial
    assert again['status'] == 'complete' and fake_pool['calls'] == ['demo_ma']
    rows = svc.results('demo_ma')['runs']
    assert [(r['new_trial'], r['selection_n']) for r in rows] == [(0, 1), (1, 1)]
    assert rows[0]['backtest_key'] == rows[1]['backtest_key'] and rows[1]['sharpe'] is not None
    detail = svc.result(rows[1]['run_id'])
    assert len(detail['equity']) == len(pd.bdate_range('2023-01-02', '2023-06-30'))
    assert {'report', 'run_info'} <= set(detail['details'])
    with results.connect(results.default_path(svc.root)) as conn:   # returns stored once per key
        assert conn.execute('SELECT COUNT(*) FROM returns').fetchone()[0] == len(detail['equity'])


def test_editing_the_bundle_source_is_a_new_trial(tmp_path, fake_pool):
    svc = service(tmp_path)
    svc.select_strategy('demo_ma')
    assert run(svc)['status'] == 'complete'
    (fake_pool['bundle'] / 'strategy.py').write_text('def initialize(context):\n    context.x = 1\n', encoding='utf-8')
    assert run(svc)['status'] == 'complete'
    assert svc.ledger.summary()['selection_n'] == 2 and fake_pool['calls'] == ['demo_ma', 'demo_ma']


def test_failed_runs_are_recorded_without_a_trial(tmp_path, fake_pool, monkeypatch):
    monkeypatch.setattr(pool, 'run_backtest', lambda *a, **k: {'status': 'error', 'error': 'secret-ish text'})
    svc = service(tmp_path)
    svc.select_strategy('demo_ma')
    job = run(svc)
    assert job['status'] == 'error'
    row = svc.results('demo_ma')['runs'][0]
    assert row['status'] == 'error' and row['new_trial'] == 0 and row['backtest_key'] is None
    assert 'secret-ish' not in (row['message'] or '') and 'gs-zipline-tej 回測失敗' in row['message']


def test_composed_graphs_open_directly_and_fixture_mode_cannot_switch(tmp_path, fake_pool, monkeypatch):
    composed = {**fake_pool['meta'], 'id': 'llm_view_tx', 'graph': 'composed',
                'graph_path': 'strategies/llm_view_tx/graph.json'}
    listing = {'strategies': [composed], 'factors': [], 'import_errors': 0, 'pool_error': None}
    monkeypatch.setattr(pool, 'pool', lambda refresh=False: listing)
    root = tmp_path / 'ws'
    target = root / 'strategies' / 'llm_view_tx' / 'graph.json'
    target.parent.mkdir(parents=True)
    target.write_text((Path(__file__).resolve().parents[1] / 'strategies/llm_view_tx/graph.json').read_text(encoding='utf-8'),
                      encoding='utf-8')
    svc = service(tmp_path)
    assert svc.select_strategy('llm_view_tx')['graph']['strategy'] == 'llm_view_tx'
    svc.fixture = True
    with pytest.raises(GraphError, match='測試資料模式'):
        svc.select_strategy('llm_view_tx')
    with pytest.raises(GraphError, match='策略池中沒有'):
        pool.entry('nope')


def test_bundle_version_tracks_source_files(tmp_path):
    d = tmp_path / 'b'
    d.mkdir()
    (d / 'strategy.py').write_text('a = 1\n', encoding='utf-8')
    (d / 'graph.json').write_text('{}', encoding='utf-8')
    v1 = pool.bundle_version({'bundle_dir': str(d)})
    (d / 'graph.json').write_text('{"x": 1}', encoding='utf-8')     # generated graph is not source
    assert pool.bundle_version({'bundle_dir': str(d)}) == v1
    (d / 'strategy.py').write_text('a = 2\n', encoding='utf-8')
    assert pool.bundle_version({'bundle_dir': str(d)}) != v1
