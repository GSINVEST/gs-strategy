"""Serving the UI through a trusted LAN reverse proxy (e.g. strategy.gsinvest.com)."""
import http.client
import json
import threading

import pytest

from strategies._common import results
from strategies._common.graph.api import GraphHTTPServer
from strategies._common.graph.core import Context
from tests.test_live_graph_api import make_service


@pytest.fixture
def served(tmp_path):
    service = make_service(tmp_path)
    server = GraphHTTPServer(('127.0.0.1', 0), service, public_hosts=['strategy.gsinvest.com'])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    calls = []
    original = service.start

    def start(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    service.start = start
    yield server, calls
    server.shutdown()
    server.server_close()
    thread.join()


def request(server, method, path, headers, body=None):
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers = {**headers, 'Content-Type': 'application/json'}
    conn.request(method, path, body=data, headers=headers)
    response = conn.getresponse()
    return response.status, response.read()


def test_public_host_is_accepted_and_others_are_not(served):
    server, _ = served
    assert request(server, 'GET', '/api/session', {'Host': 'strategy.gsinvest.com'})[0] == 200
    assert request(server, 'GET', '/api/session', {'Host': 'strategy.gsinvest.com:80'})[0] == 200
    assert request(server, 'GET', '/api/session', {'Host': f'127.0.0.1:{server.server_port}'})[0] == 200
    assert request(server, 'GET', '/api/session', {'Host': 'evil.gsinvest.com'})[0] == 403
    assert request(server, 'GET', '/api/session', {'Host': 'strategy.gsinvest.com.evil.io'})[0] == 403


def test_origin_must_match_the_public_host(served):
    server, _ = served
    good = {'Host': 'strategy.gsinvest.com', 'Origin': 'http://strategy.gsinvest.com'}
    assert request(server, 'POST', '/api/preview', good, {})[0] == 202
    server.service._thread.join(10)
    for origin in ('http://evil.example', 'https://strategy.gsinvest.com.evil.io', f'http://127.0.0.1:{server.server_port}'):
        bad = {'Host': 'strategy.gsinvest.com', 'Origin': origin}
        assert request(server, 'POST', '/api/preview', bad, {})[0] == 403


def test_identity_header_is_only_trusted_through_the_proxy(served):
    server, calls = served
    est = json.loads(request(server, 'GET', '/api/run-estimate', {'Host': 'strategy.gsinvest.com'})[1])
    body = {'expected_revision': est['revision'], 'expected_backtest_key': est['backtest_key']}
    proxied = {'Host': 'strategy.gsinvest.com', 'X-Auth-Request-Email': 'alice@gsinvest.com.tw'}
    assert request(server, 'POST', '/api/run', proxied, body)[0] == 202
    server.service._thread.join(10)
    assert calls[-1]['actor'] == 'alice@gsinvest.com.tw'
    est = json.loads(request(server, 'GET', '/api/run-estimate', {'Host': f'127.0.0.1:{server.server_port}'})[1])
    local = {'Host': f'127.0.0.1:{server.server_port}', 'X-Auth-Request-Email': 'mallory@gsinvest.com.tw'}
    body = {'expected_revision': est['revision'], 'expected_backtest_key': est['backtest_key']}
    request(server, 'POST', '/api/run', local, body)
    server.service._thread.join(10)
    assert calls[-1]['actor'] is None


def test_runs_record_the_actor_and_old_stores_migrate(tmp_path):
    db = tmp_path / 'backtests.sqlite'
    with results.connect(db) as conn:
        conn.execute('ALTER TABLE runs DROP COLUMN actor')          # simulate a store from before attribution
    ctx = Context(services={'actor': 'alice@gsinvest.com.tw'})
    results.record(db, run_id='r1', status='error', message=None, context=ctx, values={}, states={},
                   selection_n=0, new_trial=False)
    assert results.runs(db)[0]['actor'] == 'alice@gsinvest.com.tw'


def test_cli_rejects_malformed_public_hosts(capsys):
    from strategies._common.graph.__main__ import main
    with pytest.raises(SystemExit):
        main(['types', '--public-host', 'http://strategy.gsinvest.com'])
    assert '主機名稱' in capsys.readouterr().err
