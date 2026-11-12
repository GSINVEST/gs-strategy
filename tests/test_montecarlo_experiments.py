"""Monte Carlo robustness, gs-MINT ledger import (by contract) and the experiments store."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategies._common import mint_ledger, montecarlo, pool, results
from strategies._common.graph.service import GraphService
from tests.test_strategy_pool_results import fake_pool, run, service  # noqa: F401  (fixture reuse)


def test_bootstrap_is_reproducible_and_ordered():
    r = pd.Series(np.random.default_rng(3).normal(0.0008, 0.01, 400), index=pd.bdate_range('2024-01-01', periods=400))
    a = montecarlo.bootstrap(r, n_paths=500, mean_block=10, seed=1)
    b = montecarlo.bootstrap(r, n_paths=500, mean_block=10, seed=1)
    assert a['sharpe'] == b['sharpe'] and len(a['paths']) == 500
    q = a['sharpe']
    assert q['p05'] <= q['p25'] <= q['p50'] <= q['p75'] <= q['p95']
    observed = r.mean() / r.std(ddof=1) * np.sqrt(252)
    assert a['observed']['sharpe'] == pytest.approx(observed)
    # Resampling preserves the mean, so the bootstrap centre sits near the observed Sharpe.
    assert abs(q['p50'] - observed) < 0.5
    assert 0 <= a['prob_sharpe_le_0'] <= 1 and len(a['fan']['dates']) == len(a['fan']['p50'])
    with pytest.raises(ValueError):
        montecarlo.bootstrap(r[:10])


def test_monte_carlo_runs_after_backtest_stores_paths_and_adds_no_trial(tmp_path, fake_pool):
    svc = service(tmp_path)
    svc.select_strategy('demo_ma')
    assert run(svc)['status'] == 'complete'
    assert svc.ledger.summary()['selection_n'] == 1
    node = svc.node('montecarlo')
    assert node['status'] == 'recomputed' and '_paths' not in node['outputs']['MonteCarlo']
    exps = svc.experiments('demo_ma')['experiments']
    assert len(exps) == 1 and exps[0]['kind'] == 'monte_carlo' and exps[0]['n_trials'] == 2000
    detail = svc.experiment(exps[0]['experiment_id'])
    assert detail['summary']['sharpe']['p05'] <= detail['summary']['sharpe']['p95']
    assert len(detail['top_trials']) == 50 and detail['top_trials'][0]['stage'] == 'bootstrap'
    # More paths is a different experiment of the same backtest, still no new trial.
    svc.parameters('montecarlo', {'n_paths': 500})
    assert run(svc)['status'] == 'complete'
    assert svc.ledger.summary()['selection_n'] == 1
    assert sorted(e['n_trials'] for e in svc.experiments('demo_ma')['experiments']) == [500, 2000]


def _ledger(tmp_path, with_returns=True):
    base = tmp_path / 'mint_run'
    (base / 'trial_returns' / 'ingest_x').mkdir(parents=True, exist_ok=True)
    months = pd.date_range('2015-01-31', periods=60, freq='ME')
    rng = np.random.default_rng(11)
    rows = [{'event_type': 'criteria_change_trial', 'run_id': 'ingest_x', 'trial_id': 'criteria_change:v2',
             'stage': 'criteria_change', 'score': 0.0, 'change_id': 'v2'}]
    for i in range(6):
        stage = 'explore_coarse' if i < 4 else 'explore_deep'
        trial_id = f'coarse_{i:03d}' if i < 4 else f'ingest_x:coarse_00{i - 4}:D10-D1:EW:size_neutral=False'
        rel = f'trial_returns/ingest_x/t{i}.parquet'
        if with_returns:
            pd.DataFrame({'ret': rng.normal(0.003 * (i - 2), 0.02, 60)}, index=months).to_parquet(base / rel)
        rows.append({'event_type': 'trial', 'run_id': 'ingest_x', 'trial_id': trial_id, 'stage': stage,
                     'score': float(i - 2), 'score_oos_net_t': float(i - 2), 'pool_id': f'pool_{i % 3}',
                     'scheme': 'equal', 'signal_neutralize': 'none', 'min_train': 36, 'frequency': 'monthly',
                     'returns_path': rel, 'ts': '2026-07-28T18:20:23'})
    rows.append({'event_type': 'selection', 'run_id': 'ingest_x', 'trial_id': 'selection', 'stage': 'selection',
                 'score': 3.0, 'champion_trial_id': rows[-1]['trial_id']})
    path = base / 'trial_ledger.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')
    return path


def test_mint_ledger_follows_the_contract(tmp_path):
    summary = mint_ledger.summarize(_ledger(tmp_path))[0]['summary']
    assert summary['n_trials'] == 6 and summary['criteria_changes'] == 1 and summary['n_for_dsr'] == 7
    assert summary['stages'] == {'explore_coarse': 4, 'explore_deep': 2}
    assert summary['best_trial']['trial_id'].startswith('ingest_x:coarse_001:')     # colons kept intact
    assert summary['champion']['champion_trial_id'] == summary['best_trial']['trial_id']
    assert summary['pbo'] is not None and 0 <= summary['pbo'] <= 1
    no_returns = mint_ledger.summarize(_ledger(tmp_path / 'b', with_returns=False))[0]['summary']
    assert no_returns['pbo'] is None and '不在本機' in no_returns['pbo_note']


def test_mint_import_shows_up_as_an_experiment(tmp_path):
    db = tmp_path / 'backtests.sqlite'
    assert mint_ledger.import_ledger(_ledger(tmp_path), db) == ['mint:ingest_x']
    assert mint_ledger.import_ledger(_ledger(tmp_path), db) == ['mint:ingest_x']   # idempotent
    rows = results.experiments(db)
    assert len(rows) == 1 and rows[0]['n_trials'] == 6 and rows[0]['headline']['n_for_dsr'] == 7
    detail = results.experiment_detail(db, 'mint:ingest_x')
    assert [t['score'] for t in detail['top_trials']][:2] == [3.0, 2.0]


def test_factor_pool_entries_open_a_factor_graph(tmp_path, fake_pool, monkeypatch):
    factor = {**fake_pool['meta'], 'id': 'demo_factor', 'pool': 'factor', 'provenance': {'inputs': {'direction': 'short'}}}
    listing = {'strategies': [], 'factors': [factor], 'import_errors': 0, 'pool_error': None}
    monkeypatch.setattr(pool, 'pool', lambda refresh=False: listing)
    calls = []

    def run_factor(fid, cfg, **kw):
        calls.append(cfg)
        return pool.run_backtest(fid, **kw)
    monkeypatch.setattr(pool, 'run_factor_backtest', run_factor)
    svc = service(tmp_path)
    doc = svc.select_strategy('demo_factor')
    backtest = next(n for n in doc['graph']['nodes'] if n['id'] == 'backtest')
    assert backtest['type'] == 'backtest.pool_factor' and backtest['params']['direction'] == 'low'
    assert run(svc)['status'] == 'complete'
    assert calls == [{'mode': 'long_only_topn', 'direction': 'low', 'n': 20, 'weighting': 'equal', 'rebalance': 'monthly'}]


def test_coarse_graph_is_regenerated_but_keeps_saved_params(tmp_path, fake_pool):
    svc = service(tmp_path)
    svc.select_strategy('demo_ma')
    target = svc.root / 'strategies/_pool/demo_ma/graph.json'
    old = json.loads(target.read_text(encoding='utf-8'))
    old['nodes'] = [n for n in old['nodes'] if n['id'] != 'montecarlo']   # an older template
    for n in old['nodes']:
        if n['id'] == 'bundle':
            n['params']['capital_base'] = 5_000_000
    old['edges'] = [e for e in old['edges'] if e['to'][0] != 'montecarlo']
    target.write_text(json.dumps(old), encoding='utf-8')
    doc = svc.select_strategy('demo_ma')
    ids = {n['id']: n for n in doc['graph']['nodes']}
    assert 'montecarlo' in ids and ids['bundle']['params']['capital_base'] == 5_000_000
