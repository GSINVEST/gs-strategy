import copy
import json
from pathlib import Path
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import numpy as np
import pandas as pd
import pytest

from strategies._common.graph import Context, GraphError, NodeType, Registry
from strategies._common.graph.api import GraphHTTPServer
from strategies._common.graph.service import GraphService, restore_sidecar, write_json
from strategies._common.graph.stat_nodes import register_nodes
from strategies._common.validation import decision, report


def feature(inputs,params,ctx):
    return {'Weights':params['value']}
def backtest(inputs,params,ctx):
    return {'Returns': pd.Series(np.random.default_rng(10).normal(0, .01, 150)*inputs['Weights'],
                                index=pd.bdate_range('2020-01-01',periods=150))}
def failing(inputs,params,ctx):
    raise RuntimeError('private content must not escape')
def make_service(tmp_path, slow=False):
    r=Registry()
    r.register(NodeType('feature.test',{}, {'Weights':'Weights'}, {'value':{'type':'number','default':1}},feature))
    r.register(NodeType('backtest.test',{'Weights':'Weights'},{'Returns':'Returns'}, {},backtest))
    register_nodes(r)
    graph={'schema':'live-strategy-graph/1','strategy':'tsmom_tx_mtx','nodes':[
        {'id':'feature','type':'feature.test'}, {'id':'backtest','type':'backtest.test'},
        {'id':'ledger','type':'ledger.selection_n'}, {'id':'report','type':'validation.report'},
        {'id':'facts','type':'stat.facts'}, {'id':'resolve','type':'stat.resolve'}],
        'edges':[{'from':['feature','Weights'],'to':['backtest','Weights']},
                 {'from':['backtest','Returns'],'to':['report','Returns']},
                 {'from':['ledger','LedgerN'],'to':['report','LedgerN']},
                 {'from':['backtest','Returns'],'to':['facts','Returns']},
                 {'from':['facts','Facts'],'to':['resolve','Facts']},
                 {'from':['ledger','LedgerN'],'to':['resolve','LedgerN']}]}
    path=tmp_path/'strategies/tsmom_tx_mtx/graph.json'; write_json(path,graph)
    path.with_name('manifest.yaml').write_text('id: tsmom_tx_mtx\n',encoding='utf-8')
    s=GraphService(tmp_path,registry=r)
    s.load('strategies/tsmom_tx_mtx/graph.json')
    return s

def finish(s,preview=False):
    job=s.start(preview=preview); s._thread.join(20)
    assert not s._thread.is_alive()
    return s.jobs[job['id']]

def test_shared_lifecycle_sidecar_manifest_and_restart(tmp_path):
    s=make_service(tmp_path)
    assert not s.document()['dirty']
    assert finish(s,True)['status']=='complete'
    assert not s.ledger.path.exists()
    job=finish(s)
    assert job['status']=='complete'
    assert s.ledger.summary()['selection_n']==1
    assert s.engine.values['report']['Report']['n_trials']==1
    doc=json.loads((tmp_path/job['sidecar']).read_text(encoding='utf-8'))
    restored=restore_sidecar(doc,s.registry)
    assert restored['graph_hash']==s.document()['graph_hash']
    assert restored['graph']==s.graph and not restored['warnings']
    import yaml
    assert 'validation' not in yaml.safe_load((s.path.parent/'manifest.yaml').read_text(encoding='utf-8'))
    s.save()
    manifest=yaml.safe_load((s.path.parent/'manifest.yaml').read_text(encoding='utf-8'))
    assert manifest['validation']['graph_hash']==restored['graph_hash']
    s.parameters('feature',{'value':2})
    assert s.document()['dirty'] and s.node('backtest')['status']=='stale'
    assert 'equity' in s.node('backtest')
    assert finish(s)['status']=='complete'
    assert s.ledger.summary()['selection_n']==2
    s.parameters('feature',{'value':1}); finish(s)
    assert s.engine.states['backtest']['status']=='cached'
    assert s.ledger.summary()['selection_n']==2
    s.save()
    assert not s.document()['dirty']
    s.registry.types['feature.test'].function=failing
    assert '節點實作已變更' in restore_sidecar(doc,s.registry)['warnings'][0]
    assert finish(s)['status']=='error'
    assert 'private' not in json.dumps(s.engine.states)
    assert s.ledger.summary()['selection_n']==2
    records=[json.loads(line) for line in s.ledger.path.read_text().splitlines()]
    assert decision.audit_ledger(records)['auditable']

def test_sidecar_tamper_and_old_report_compatibility(tmp_path):
    s=make_service(tmp_path); job=finish(s)
    doc=json.loads((tmp_path/job['sidecar']).read_text())
    doc['graph_snapshot']['graph']['nodes'][0]['params']['new']=10
    with pytest.raises(GraphError,match='mismatch'): restore_sidecar(doc,s.registry)
    perf=tmp_path/'perf.pkl'
    pd.DataFrame({'returns':np.random.default_rng(1).normal(0,.01,100)},index=pd.bdate_range('2020-01-01',periods=100)).to_pickle(perf)
    sidecar=report.write_sidecar_for(perf,graph_hash='known',graph_snapshot={'graph':{}})
    assert json.loads(sidecar.read_text())['graph_hash']=='known'
    assert report.write_sidecar_for(perf) is not None

def test_http_endpoints_and_atomic_rejection(tmp_path):
    s=make_service(tmp_path)
    server=GraphHTTPServer(('127.0.0.1',0),s)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    def call(path,data=None,headers=None):
        if data is not None:
            data = {**data, 'expected_revision': s.document()['revision']}
            if path == '/api/run':
                data['expected_backtest_key'] = s.run_estimate()['backtest_key']
        req=Request(f'http://127.0.0.1:{server.server_port}'+path,
            data=None if data is None else json.dumps(data).encode(),
            headers=headers or {'Content-Type':'application/json'})
        try:
            with urlopen(req,timeout=10) as response: return response.status,json.load(response)
        except HTTPError as exc: return exc.code,json.load(exc)
    try:
        assert call('/api/node-types')[0]==200
        initial=call('/api/graph')[1]
        bad=copy.deepcopy(initial['graph']); bad['edges'][0]['to']=['report','LedgerN']
        assert call('/api/graph',{'graph':bad})[0]==400
        assert call('/api/graph')[1]==initial
        assert call('/api/graph/load',{'path':'../graph.json'})[0]==400
        assert call('/api/graph/load',{'path':'strategies/tsmom_tx_mtx/graph.json'})[0]==200
        assert call('/api/preview',{})[0]==202; s._thread.join(20)
        assert call('/api/ledger')[1]['selection_n']==0
        assert call('/api/nodes/feature')[1]['outputs']['Weights']==1
        assert call('/api/nodes/feature/params',{'params':{'value':2}})[0]==200
        assert call('/api/graph/save',{})[1]['dirty'] is False
        status,job=call('/api/run',{}); assert status==202; s._thread.join(20)
        status,job=call('/api/jobs/'+job['id']); assert job['status']=='complete'
        assert call('/api/nodes/backtest?limit=10&offset=0')[1]['outputs']['Returns']['total']==150
        assert call('/api/nodes')[0]==200
        assert call('/api/graph/from-sidecar',{'path':job['sidecar']})[0]==200
        assert call('/api/run',{}, {'Content-Type':'application/json','Origin':'https://other.example'})[0]==403
        assert call('/api/run',{}, {'Content-Type':'text/plain'})[0]==415
        assert call('/api/no-such-route')[0]==404
    finally:
        server.shutdown(); server.server_close(); thread.join()

def test_api_worker_cancel_has_no_ledger(tmp_path):
    entered=threading.Event()
    def slow(inputs,params,ctx):
        entered.set()
        while not ctx.token.cancelled:
            entered.wait(.001)
        ctx.check_cancelled()
    s=make_service(tmp_path); s.registry.types['backtest.test'].function=slow
    job=s.start(); assert entered.wait(10)
    with pytest.raises(GraphError,match='active'): s.parameters('feature',{'value':3})
    assert s.cancel(job['id'])['accepted']
    s._thread.join(10)
    assert s.jobs[job['id']]['status']=='cancelled'
    assert s.ledger.summary()['selection_n']==0 and not s.ledger.path.exists()

def test_cli_types_starts_without_bundle_or_key():
    import subprocess
    import sys
    result = subprocess.run([sys.executable,'-m','strategies._common.graph','types'],capture_output=True,text=True,check=True)
    ids={t['id'] for t in json.loads(result.stdout)}
    assert {'data.futures_bars','data.continuous','feature.signed_momentum','feature.ewma_vol','signal.direction','sizing.vol_target','sizing.gross_cap','cost.futures','backtest.zipline','ledger.selection_n','validation.report','stat.facts','stat.resolve'} <= ids and {'data.pool_strategy','feature.strategy_spec','backtest.pool_zipline','backtest.pool_factor','validation.monte_carlo'} <= ids and len(ids)==24

def test_invalid_save_path_does_not_replace_current_graph(tmp_path):
    s=make_service(tmp_path)
    before=s.document()
    changed=copy.deepcopy(s.graph)
    next(n for n in changed['nodes'] if n['id']=='feature')['params']['value']=2
    with pytest.raises(GraphError): s.set_graph(changed,'../graph.json')
    assert s.document()==before

def test_external_console_output_and_errors_are_not_exposed(tmp_path,capsys):
    def external_failure(inputs,params,ctx):
        print('sensitive console fixture')
        raise RuntimeError('sensitive exception fixture')
    s=make_service(tmp_path); s.registry.types['backtest.test'].function=external_failure
    assert finish(s)['status']=='error'
    captured=capsys.readouterr()
    assert 'sensitive' not in captured.out+captured.err+json.dumps(s.engine.states)
    assert not s.ledger.path.exists()

def test_cli_server_defaults_loopback_and_serves_json(tmp_path):
    import queue
    import subprocess
    import sys
    from urllib.parse import urlsplit
    process=subprocess.Popen([sys.executable,'-m','strategies._common.graph','serve',
        '--root',str(tmp_path),'--port','0'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    lines=queue.Queue()
    reader=threading.Thread(target=lambda:lines.put(process.stdout.readline()),daemon=True)
    reader.start()
    try:
        address=lines.get(timeout=20).strip().split()[-1]
        assert urlsplit(address).hostname=='127.0.0.1'
        with urlopen(address+'/api/node-types',timeout=10) as response:
            assert response.status==200
            ids={t['id'] for t in json.load(response)['node_types']}
            assert {'data.futures_bars','data.continuous','feature.signed_momentum','feature.ewma_vol','signal.direction','sizing.vol_target','sizing.gross_cap','cost.futures','backtest.zipline','ledger.selection_n','validation.report','stat.facts','stat.resolve'} <= ids and {'data.pool_strategy','feature.strategy_spec','backtest.pool_zipline','backtest.pool_factor','validation.monte_carlo'} <= ids and len(ids)==24
        assert not (tmp_path/'log/trials.jsonl').exists()
    finally:
        process.terminate()
        process.communicate(timeout=10)
        reader.join(timeout=1)

def test_generated_cache_and_console_logs_are_gitignored():
    import subprocess
    paths=['.cache/live-strategy-graph/probe.pkl','.graph-runs/probe.validation.json','log/codex-probe.txt']
    result=subprocess.run(['git','check-ignore',*paths],text=True,capture_output=True,check=True)
    assert result.stdout.splitlines()==paths


def test_downstream_family_cached_without_new_selection_and_config_ref(tmp_path):
    from scripts.triage_generated import Candidate
    s = make_service(tmp_path)
    first = finish(s)
    records = lambda: [json.loads(line) for line in s.ledger.path.read_text().splitlines()]
    original = records()[0]
    s.parameters('facts', {'family': 'bounded_multiple'})
    second = finish(s)
    assert second['graph_hash'] != first['graph_hash']
    assert s.engine.states['backtest']['status'] == 'cached'
    assert s.ledger.summary()['selection_n'] == 1
    assert records() == [original]
    s.parameters('feature', {'value': 2})
    finish(s)
    assert s.ledger.summary()['selection_n'] == 2
    refs = [record['config_ref'] for record in records()]
    assert len(set(refs)) == 2
    for record in records():
        path = tmp_path / record['config_ref']
        snapshot = json.loads(path.read_text())
        assert snapshot['graph_hash'] == record['graph_hash']
        assert snapshot['graph_snapshot'] == record['graph_ref']['snapshot']
        candidate = Candidate('', path, {}, '', '', '', [], '', '', rel_base=tmp_path)
        assert candidate.config_ref in refs
    assert json.loads((tmp_path/original['config_ref']).read_text())['graph_hash'] == first['graph_hash']


def test_manifest_changes_only_on_save_of_completed_graph(tmp_path):
    s = make_service(tmp_path)
    manifest = s.path.with_name('manifest.yaml')
    before = manifest.read_bytes()
    first = finish(s)
    assert manifest.read_bytes() == before
    s.parameters('feature', {'value': 2})
    s.save()
    assert manifest.read_bytes() == before
    finish(s, preview=True)
    s.save()
    assert manifest.read_bytes() == before
    second = finish(s)
    assert manifest.read_bytes() == before
    s.save()
    import yaml
    assert yaml.safe_load(manifest.read_text())['validation']['graph_hash'] == second['graph_hash']
    after = manifest.read_bytes()
    s.parameters('feature', {'value': 1})
    assert finish(s)['graph_hash'] == first['graph_hash']
    assert manifest.read_bytes() == after
    s.save()
    assert yaml.safe_load(manifest.read_text())['validation']['graph_hash'] == first['graph_hash']


def test_worker_output_isolation_preserves_other_thread_errors(tmp_path, capsys, caplog):
    import logging
    import sys
    entered, release = threading.Event(), threading.Event()
    def noisy(inputs, params, ctx):
        print('sensitive fixture output')
        print('sensitive fixture error', file=sys.stderr)
        logging.error('sensitive fixture log')
        entered.set()
        assert release.wait(10)
        raise GraphError('controlled graph failure')
    s = make_service(tmp_path)
    s.registry.types['backtest.test'].function = noisy
    disabled = logging.root.manager.disable
    s.start()
    try:
        assert entered.wait(10)
        print('server remains visible', file=sys.stderr)
        logging.error('server error remains visible')
        assert logging.root.manager.disable == disabled
    finally:
        release.set()
        s._thread.join(20)
    captured = capsys.readouterr()
    assert 'server remains visible' in captured.err
    assert 'server error remains visible' in caplog.text
    assert 'sensitive' not in captured.out + captured.err + caplog.text
    assert 'controlled graph failure' in s.jobs[s.active]['message']


@pytest.mark.parametrize('domain', [True, False, 'underdetermined'])
def test_prepare_errors_reach_job_and_cli_only_when_controlled(tmp_path, monkeypatch, capsys, domain):
    from strategies.tsmom_tx_mtx import graph_nodes
    from strategies._common.graph import __main__ as cli
    s = make_service(tmp_path)
    s.registry.register(NodeType('data.futures_bars', {}, {'Bars': 'Bars'}, {}, feature))
    graph = copy.deepcopy(s.graph)
    graph['nodes'].append({'id': 'bars', 'type': 'data.futures_bars'})
    s.set_graph(graph)
    def fail(graph):
        if domain == 'underdetermined':
            raise decision.UnderdeterminedError('bundle has no ingestions; supply observations')
        raise (GraphError('bundle has no ingestions') if domain else RuntimeError('sensitive fixture'))
    monkeypatch.setattr(graph_nodes, 'prepare_graph', fail)
    job = finish(s)
    assert job['status'] == 'error'
    assert ('bundle has no ingestions' in job['message']) == bool(domain)
    assert 'sensitive' not in json.dumps(job)
    monkeypatch.setattr(cli, 'GraphService', lambda *a, **kw: s)
    monkeypatch.setattr(s, 'load', lambda path: None)
    assert cli.main(['run', '--root', str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert ('資料集沒有可用的匯入版本' in output) == bool(domain)
    assert 'sensitive' not in output
    monkeypatch.setattr(s, 'load', fail)
    assert cli.main(['run', '--root', str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert ('資料集沒有可用的匯入版本' in output) == bool(domain)
    assert 'sensitive' not in output


def test_run_pins_runtime_version_without_dirtying_document(tmp_path, monkeypatch):
    from strategies.tsmom_tx_mtx import graph_nodes
    s = make_service(tmp_path)
    def source(inputs, params, ctx):
        return {'Bars': params}
    s.registry.register(NodeType('data.futures_bars', {}, {'Bars': 'Bars'},
        {'data_version': {'type': 'string', 'default': 'auto'}}, source))
    # Use a source dependency so its pinned version participates in Backtest identity.
    s.registry.types['feature.test'].inputs = {'Bars': 'Bars'}
    graph = copy.deepcopy(s.graph)
    graph['nodes'].append({'id': 'bars', 'type': 'data.futures_bars'})
    graph['edges'].append({'from': ['bars','Bars'], 'to': ['feature','Bars']})
    write_json(s.path, s.registry.normalize(graph)[0])
    s.load('strategies/tsmom_tx_mtx/graph.json')
    before, document = s.path.read_bytes(), s.document()
    version = ['2020-01-01T00:00:00']
    def prepare(graph):
        result = copy.deepcopy(graph)
        next(n for n in result['nodes'] if n['id'] == 'bars')['params']['data_version'] = version[0]
        return result
    monkeypatch.setattr(graph_nodes, 'prepare_graph', prepare)
    first = finish(s)
    assert first['status'] == 'complete'
    assert s.document() == document and not s.document()['dirty']
    assert s.path.read_bytes() == before
    sidecar = json.loads((tmp_path/first['sidecar']).read_text())
    assert next(n for n in sidecar['graph_snapshot']['graph']['nodes'] if n['id']=='bars')['params']['data_version'] == version[0]
    finish(s)
    assert s.engine.states['backtest']['status'] == 'cached'
    assert s.ledger.summary()['selection_n'] == 1
    version[0] = '2020-01-02T00:00:00'
    second = finish(s)
    assert second['backtest_key'] != first['backtest_key']
    assert s.ledger.summary()['selection_n'] == 2
    s.save()
    assert s.path.read_bytes() == before
    assert version[0] not in s.path.read_text()


def test_ci_documents_optional_secret_and_fixture_coverage():
    text = (Path(__file__).parents[1]/'.github/workflows/ci.yml').read_text()
    assert 'Secrets and variables > Actions' in text
    assert 'TEJAPI_KEY' in text and 'GS_TEST_REAL_BUNDLE=1' in text
    assert 'ZIPLINE_ROOT' in text and 'never mocked accounting' in text


def test_save_after_restart_uses_matching_completed_sidecar(tmp_path):
    s = make_service(tmp_path)
    first = finish(s)
    other = GraphService(tmp_path, registry=s.registry)
    other.load('strategies/tsmom_tx_mtx/graph.json')
    other.save()
    import yaml
    manifest = other.path.with_name('manifest.yaml')
    assert yaml.safe_load(manifest.read_text())['validation']['graph_hash'] == first['graph_hash']
