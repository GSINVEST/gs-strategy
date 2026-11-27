"""Single-round adversarial regressions, isolated from real market data."""
import copy
import json
import shutil
import threading

import numpy as np
import pandas as pd
import pytest

from tests.test_live_graph_api import make_service, finish, feature
from strategies._common.graph import core
from strategies._common.graph.stat_nodes import derive_facts, resolve_payload
from strategies.tsmom_tx_mtx import graph_nodes


@pytest.fixture(autouse=True)
def offline_import():
    from strategies.tsmom_tx_mtx.fixture_bundle import import_zipline_offline
    import_zipline_offline()


def test_a1_fresh_cost_helper_and_fingerprint(tmp_path, monkeypatch):
    for name in ('strategy.py', 'futures_setup.py'):
        shutil.copy(graph_nodes.HERE / name, tmp_path / name)
    monkeypatch.setattr(graph_nodes, 'HERE', tmp_path)
    first = graph_nodes._legacy()
    before = core.code_fingerprint(tmp_path / 'futures_setup.py')
    path = tmp_path / 'futures_setup.py'
    path.write_text(path.read_text().replace('FixedSlippage(spread=spread)',
                    'FixedSlippage(spread=2 * spread)'), encoding='utf-8')
    second = graph_nodes._legacy()
    assert core.code_fingerprint(path) != before
    spreads = []
    for module in (first, second):
        env = module.apply_taiwan_futures_costs.__globals__
        monkeypatch.setitem(env, 'set_commission', lambda **kw: None)
        monkeypatch.setitem(env, 'set_slippage', lambda **kw: spreads.append(kw['futures'].spread))
        module.apply_taiwan_futures_costs()
    assert spreads == [6.0, 12.0]


def pinned_service(tmp_path, monkeypatch):
    from zipline.data.bundles import core as bundles
    monkeypatch.setattr(bundles, 'ingestions_for_bundle', lambda name: [pd.Timestamp('2026-01-01')])
    service = make_service(tmp_path)
    service.registry.register(core.NodeType('data.futures_bars', {}, {'Weights': 'Weights'},
        {'value': {'type': 'number', 'default': 1}, 'data_version': {'type': 'string', 'default': 'initial'}}, feature))
    graph = copy.deepcopy(service.graph)
    next(n for n in graph['nodes'] if n['id'] == 'feature')['type'] = 'data.futures_bars'
    service.set_graph(graph)
    return service


def test_a2_equivalent_ingestions_count_once(tmp_path, monkeypatch):
    service = pinned_service(tmp_path, monkeypatch)
    keys = []
    for version in ('2026-01-01T00:00:00', '2026-01-01 00:00:00', '2026-01-01T00:00:00.000000'):
        service.parameters('feature', {'data_version': version})
        estimate = service.run_estimate()
        job = finish(service)
        assert job['status'] == 'complete'
        assert estimate['backtest_key'] == job['backtest_key']
        keys.append(job['backtest_key'])
    assert len(set(keys)) == 1
    assert service.ledger.summary()['selection_n'] == 1


def test_a3_monthly_dependence_uses_non_iid_and_full_provenance():
    e = np.random.default_rng(0).normal(size=1021)
    payload = derive_facts(.01 * (e[21:] + e[:-21]))
    assert payload['facts']['autocorr'] == 'yes'
    assert resolve_payload(payload, {'selection_n': 1})['se_correction'] != 'iid'
    evidence = payload['provenance']['autocorr']
    assert 21 in evidence['tested_lags']
    assert max(evidence['tested_lags']) == payload['provenance']['n_eff']['lags']
    assert len(evidence['p_values']) == len(evidence['tested_lags'])


def test_a4_stale_returns_keep_original_graph_hash(tmp_path):
    service = make_service(tmp_path)
    original = finish(service)['graph_hash']
    service.parameters('feature', {'value': 2})
    assert service.node('backtest')['graph_hash'] == original
    assert service.node('backtest')['status'] == 'stale'


def test_b1_b4_active_job_snapshot_and_safe_chinese(tmp_path):
    service = make_service(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def slow(inputs, params, ctx):
        entered.set()
        release.wait(10)
        return feature(inputs, params, ctx)
    service.registry.types['feature.test'].function = slow
    job = service.start(preview=True)
    try:
        assert entered.wait(5)
        active = service.session()['active_job']
        assert active['id'] == job['id'] and active['node_states']['feature'] == 'running'
        active['progress']['tampered'] = True
        assert 'tampered' not in service.jobs[job['id']]['progress']
        assert 'outputs' not in json.dumps(service.job(job['id']))
        assert service.cancel(job['id'])['reason'] == '已要求取消執行'
    finally:
        release.set()
        service._thread.join(10)
    assert service.session()['active_job'] is None


def test_b2_estimate_and_ledger_share_key_function(tmp_path, monkeypatch):
    original = getattr(core, 'node_cache_key', None)
    assert callable(original)
    calls = []
    def tracked(*args, **kwargs):
        key = original(*args, **kwargs)
        calls.append(key)
        return key
    monkeypatch.setattr(core, 'node_cache_key', tracked)
    service = make_service(tmp_path)
    estimate = service.run_estimate()
    assert estimate['backtest_key'] in calls
    calls.clear()
    finish(service)
    assert estimate['backtest_key'] in calls
    records = [json.loads(line) for line in service.ledger.path.read_text().splitlines()]
    assert records[0]['backtest_key'] == estimate['backtest_key']


def test_b5_estimate_releases_lock_and_keeps_snapshot(tmp_path, monkeypatch):
    service = pinned_service(tmp_path, monkeypatch)
    before = service.document()['graph_hash']
    prepare = graph_nodes.prepare_graph
    entered, release, acquired = threading.Event(), threading.Event(), threading.Event()
    result = []
    def slow(graph):
        entered.set()
        release.wait(10)
        return prepare(graph)
    monkeypatch.setattr(graph_nodes, 'prepare_graph', slow)
    worker = threading.Thread(target=lambda: result.append(service.run_estimate()))
    worker.start()
    assert entered.wait(5)
    def edit():
        service.parameters('feature', {'value': 2})
        acquired.set()
    editor = threading.Thread(target=edit)
    editor.start()
    try:
        assert acquired.wait(1)
    finally:
        release.set()
        worker.join(10)
        editor.join(10)
    assert result[0]['graph_hash'] == before


def test_b1_http_and_node_messages_are_safe_chinese(tmp_path):
    from strategies._common.graph.service import user_message
    assert user_message('execution active; cancel or wait before editing') == '目前有工作正在執行，請取消或等待完成後再編輯'
    assert user_message('invalid edge at index 3: incompatible or unknown ports') == '接線索引 3 無效：接點不相容或不存在'
    assert user_message('unrecognized external failure') == '操作失敗，請檢查設定後重試'
    service = make_service(tmp_path)
    service.engine.states['feature'] = {'status': 'error', 'message': 'unrecognized external failure'}
    assert service.node('feature')['message'] == '操作失敗，請檢查設定後重試'


def test_a1_implementation_change_during_execution_cannot_commit(tmp_path):
    service = make_service(tmp_path)
    dependency = tmp_path / 'helper.py'
    dependency.write_text('VALUE = 1\n')
    original = service.registry.types['backtest.test'].function
    def changed_during_run(inputs, params, ctx):
        dependency.write_text('VALUE = 2\n')
        return original(inputs, params, ctx)
    kind = service.registry.types['backtest.test']
    kind.function = changed_during_run
    kind.dependencies = (dependency,)
    estimate = service.run_estimate()
    assert finish(service)['status'] == 'error'
    assert service.ledger.summary()['selection_n'] == 0
    assert not (service.engine.cache_dir / (estimate['backtest_key'] + '.pkl')).exists()
