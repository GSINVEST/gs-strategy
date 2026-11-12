"""Phase B additive API, key prediction and fixture isolation contracts."""
import copy
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests.test_live_graph_api import make_service, finish
from strategies._common.graph.core import GraphError
from strategies._common.graph.api import GraphHTTPServer


def test_estimate_matches_execution_without_running(tmp_path):
    service = make_service(tmp_path)
    before = copy.deepcopy(service.engine.states)
    estimate = service.run_estimate()
    assert estimate['delta_n'] == 1 and estimate['next_selection_n'] == 1
    assert service.engine.states == before and not service.engine.values
    assert not service.ledger.path.exists()
    assert finish(service)['backtest_key'] == estimate['backtest_key']
    after = service.run_estimate()
    assert after['already_recorded'] and after['delta_n'] == 0
    service.parameters('facts', {'family': 'bounded_multiple'})
    assert service.run_estimate()['backtest_key'] == estimate['backtest_key']
    service.parameters('feature', {'value': 2})
    assert service.run_estimate()['backtest_key'] != estimate['backtest_key']
    assert service.run_estimate()['next_selection_n'] == 2


def test_estimate_rejects_missing_and_mutable_upstream(tmp_path):
    service = make_service(tmp_path)
    graph = copy.deepcopy(service.graph)
    graph['edges'] = [e for e in graph['edges'] if e['to'][0] != 'backtest']
    service.set_graph(graph)
    with pytest.raises(GraphError, match='missing inputs'):
        service.run_estimate()
    service.load('strategies/tsmom_tx_mtx/graph.json')
    service.registry.types['feature.test'].cacheable = False
    with pytest.raises(GraphError, match='cacheable'):
        service.run_estimate()


def test_layout_is_separate_atomic_and_hash_invariant(tmp_path):
    service = make_service(tmp_path)
    document, estimate = service.document(), service.run_estimate()
    states = copy.deepcopy(service.engine.states)
    assert service.layout()['positions'] == {}
    valid = {'feature': {'x': 12.5, 'y': -42}}
    assert service.layout(valid)['positions'] == valid
    path = service.path.with_name('graph.layout.json')
    before = path.read_bytes()
    for invalid in ({'missing': {'x': 1, 'y': 2}}, {'feature': {'x': float('nan'), 'y': 2}},
                    {'feature': {'x': True, 'y': 2}}, {'feature': {'x': 1}}):
        with pytest.raises(GraphError):
            service.layout(invalid)
        assert path.read_bytes() == before
    assert service.document() == document
    assert service.run_estimate() == estimate
    assert service.engine.states == states


def test_new_http_routes_and_static_boundaries(tmp_path):
    service = make_service(tmp_path)
    dist = tmp_path / 'dist'
    (dist / 'assets').mkdir(parents=True)
    (dist / 'index.html').write_text('<html>fixture</html>')
    (dist / 'assets' / 'app.js').write_text('true')
    (tmp_path / 'secret.txt').write_text('never serve')
    server = GraphHTTPServer(('127.0.0.1', 0), service, ui_dist=dist)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def call(path, data=None, headers=None):
        request = Request(f'http://127.0.0.1:{server.server_port}{path}',
                          data=json.dumps(data).encode() if data is not None else None,
                          headers=headers or {'Content-Type': 'application/json'})
        try:
            with urlopen(request) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()
    try:
        assert call('/')[0] == 200
        assert call('/assets/app.js')[0] == 200
        assert call('/assets/../../secret.txt')[0] == 404
        assert call('/assets/%2e%2e/%2e%2e/secret.txt')[0] == 404
        assert call('/secret.txt')[0] == 404
        assert call('/', headers={'Host': 'evil.invalid'})[0] == 403
        assert call('/', headers={'Origin': 'https://evil.invalid'})[0] == 403
        assert json.loads(call('/api/session')[1]) == {'fixture': False, 'label': '', 'active_job': None, 'strategy': 'tsmom_tx_mtx'}
        assert call('/api/run-estimate')[0] == 200
        assert call('/api/layout')[0] == 200
        assert call('/api/layout', {'positions': {'feature': {'x': 1, 'y': 2}}})[0] == 200
        assert call('/api/layout', {'positions': {'unknown': {'x': 1, 'y': 2}}})[0] == 400
        assert call('/api/layout', {'positions': None})[0] == 400
        service.fixture = True
        assert json.loads(call('/api/session')[1])['label'] == 'FIXTURE 資料・獨立 ledger'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_fixture_isolation_real_key_and_cancellation():
    from strategies._common.graph.fixture import fixture_service, close_fixture
    source = Path(__file__).resolve().parents[1]
    service = fixture_service(source)
    try:
        assert not service.root.is_relative_to(source)
        assert service.ledger.path.is_relative_to(service.root)
        assert service.engine.cache_dir.is_relative_to(service.root)
        assert service.path.is_relative_to(service.root)
        estimate = service.run_estimate()
        assert estimate['delta_n'] == 1
        job = service.start(preview=True)
        service._thread.join(120)
        assert service.jobs[job['id']]['status'] == 'complete'
        assert service.ledger.summary()['selection_n'] == 0
        job = service.start()
        import time
        deadline = time.monotonic() + 60
        while service.jobs[job['id']]['progress'].get('phase') != 'backtest' and time.monotonic() < deadline:
            time.sleep(.02)
        assert service.cancel(job['id'])['accepted']
        service._thread.join(60)
        assert service.jobs[job['id']]['status'] == 'cancelled'
        assert service.ledger.summary()['selection_n'] == 0
        job = service.start()
        service._thread.join(120)
        assert service.jobs[job['id']]['status'] == 'complete', service.engine.states
        assert service.jobs[job['id']]['backtest_key'] == estimate['backtest_key']
        assert service.run_estimate()['delta_n'] == 0
        assert (service.root / service.jobs[job['id']]['sidecar']).is_file()
        assert all(service.engine.values[i] for i in ('report', 'facts', 'resolve'))
    finally:
        close_fixture(service)


@pytest.mark.parametrize('flag', ['--root', '--cache-dir', '--ledger'])
def test_fixture_rejects_storage_overrides(flag):
    from strategies._common.graph.__main__ import main
    with pytest.raises(SystemExit):
        main(['ui', '--fixture', flag, 'override'])


def test_ui_bind_failure_cleans_fixture_and_redacts(tmp_path, monkeypatch, capsys):
    from strategies._common.graph import fixture, __main__ as cli
    import socket
    service = make_service(tmp_path)
    closed = []
    monkeypatch.setattr(fixture, 'fixture_service', lambda *args: service)
    monkeypatch.setattr(fixture, 'close_fixture', lambda value: closed.append(value))
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        assert cli.main(['ui', '--fixture', '--port', str(occupied.getsockname()[1])]) == 1
    assert closed == [service]
    assert json.loads(capsys.readouterr().out) == {'error': '策略圖介面無法啟動或繼續執行'}


def test_ui_initialization_failure_does_not_expose_external_text(monkeypatch, capsys):
    from strategies._common.graph import fixture, __main__ as cli
    def unavailable(*args):
        print('external console excluded')
        raise RuntimeError('external exception excluded')
    monkeypatch.setattr(fixture, 'fixture_service', unavailable)
    assert cli.main(['ui', '--fixture']) == 1
    captured = capsys.readouterr()
    assert 'excluded' not in captured.out + captured.err
    assert json.loads(captured.out) == {'error': '策略圖介面無法啟動或繼續執行'}

def test_ui_invalid_edge_still_serves_controlled_load_error(tmp_path, monkeypatch):
    from strategies._common.graph import __main__ as cli
    from strategies._common.graph.service import write_json
    service = make_service(tmp_path)
    graph = copy.deepcopy(service.graph)
    graph['edges'][0]['to'] = ['report', 'LedgerN']
    graph_path = 'strategies/custom_choice/graph.json'
    write_json(tmp_path / graph_path, graph)
    service.graph = None
    service.path = None
    monkeypatch.setattr(cli, 'GraphService', lambda *args, **kwargs: service)
    original = GraphHTTPServer.serve_forever
    observed = []
    def inspect(server):
        worker = threading.Thread(target=original, args=(server,), daemon=True)
        worker.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            with urlopen(base + '/api/session') as response:
                assert json.load(response)['graph_path'] == graph_path
            with urlopen(base + '/api/graph') as response:
                assert json.load(response)['graph'] is None
            request = Request(base + '/api/graph/load',
                              data=json.dumps({'path': graph_path, 'expected_revision': service.revision}).encode(),
                              headers={'Content-Type': 'application/json'})
            with pytest.raises(HTTPError) as caught:
                urlopen(request)
            assert caught.value.code == 400
            assert '接線索引 0 無效' in json.load(caught.value)['error']
            observed.append(True)
        finally:
            server.shutdown()
            worker.join()
    monkeypatch.setattr(GraphHTTPServer, 'serve_forever', inspect)
    assert cli.main(['ui', '--root', str(tmp_path), '--graph', graph_path, '--port', '0']) == 0
    assert observed == [True]


def test_session_graph_path_is_opt_in(tmp_path):
    service = make_service(tmp_path)
    assert 'graph_path' not in service.session()
    service.initial_graph_path = 'strategies/custom_choice/graph.json'
    assert service.session()['graph_path'] == 'strategies/custom_choice/graph.json'
