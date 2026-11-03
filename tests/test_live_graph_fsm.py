"""形式化驗證（FSM）檢視：資料來源、白名單、唯讀、路徑與主機邊界。"""
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from strategies._common.graph import Registry
from strategies._common.graph.api import GraphHTTPServer
from strategies._common.graph.fsm import FIXTURE_ATLAS, FsmView, clean_target, read_atlas, target_from_ir
from strategies._common.graph.service import GraphService, write_json

REPO = Path(__file__).resolve().parents[1]


def atlas_doc(**extra):
    return {'schema': 'strategy-fsm-atlas/1', 'generated_at': '2026-11-03T00:00:00Z', 'tlc_ran': True, 'targets': [{
        'id': 'demo', 'title_zh': 'd', 'status': 'pass_with_bounds', 'graph': {'nodes': [{'id': 'A', 'label_zh': 'a'}], 'edges': []},
        'invariants': [{'id': 'I', 'label_zh': 'i', 'verdict': 'pass_with_bounds', 'origin': 'code_derived',
                        'detail': {'cfg': 'C:\\Users\\boss\\secret\\core.cfg', 'distinct_states': 3}, 'prompt': 'LEAK', 'host_path': '/home/x'}],
        'secret_field': 'LEAK', **extra}]}


def serve(tmp_path, atlas=None, fixture=False):
    s = GraphService(tmp_path, registry=Registry())
    path = tmp_path / 'strategies/tsmom_tx_mtx/graph.json'
    write_json(path, {'schema': 'live-strategy-graph/1', 'strategy': 'tsmom_tx_mtx', 'nodes': [], 'edges': []})
    s.load('strategies/tsmom_tx_mtx/graph.json')
    s.fsm_provider = FsmView(tmp_path, atlas, fixture=fixture)
    server = GraphHTTPServer(('127.0.0.1', 0), s)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return s, server


def get(server, path='/api/fsm', method='GET', host=None):
    req = Request(f'http://127.0.0.1:{server.server_port}{path}', method=method, data=b'{}' if method == 'POST' else None,
                  headers={'Host': host} if host else {'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def test_clean_target_whitelists_fields_and_strips_host_paths(tmp_path):
    p = tmp_path / 'a.json'
    p.write_text(json.dumps(atlas_doc()), encoding='utf-8')
    status, atlas = read_atlas(p)
    assert status == 'ok'
    out = clean_target(atlas['targets'][0])
    text = json.dumps(out, ensure_ascii=False)
    assert 'LEAK' not in text and 'secret' not in text and '/home/x' not in text
    assert out['invariants'][0]['cfg'] == 'core.cfg'


def test_read_atlas_missing_bad_and_wrong_schema(tmp_path):
    assert read_atlas(tmp_path / 'none.json') == ('missing', None)
    for body in ('{not json', json.dumps({'schema': 'other/1', 'targets': []}), json.dumps({'schema': 'strategy-fsm-atlas/1'})):
        (tmp_path / 'b.json').write_text(body, encoding='utf-8')
        assert read_atlas(tmp_path / 'b.json') == ('bad', None)


def test_ir_fallback_marks_everything_unchecked():
    ir = json.loads((REPO / 'strategies/tsmom_tx_mtx/fsm.ir.json').read_text(encoding='utf-8'))
    t = target_from_ir(ir)
    assert t['id'] == 'tsmom_tx_mtx' and t['status'] == 'unchecked'
    assert t['graph']['nodes'] and t['graph']['edges']
    assert t['invariants'] and all(i['verdict'] == 'unchecked' for i in t['invariants'])
    assert not any(i['counterexample'] for i in t['invariants'])


def test_endpoint_without_atlas_uses_ir_for_current_strategy(tmp_path):
    (tmp_path / 'strategies/tsmom_tx_mtx').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'strategies/tsmom_tx_mtx/fsm.ir.json').write_bytes((REPO / 'strategies/tsmom_tx_mtx/fsm.ir.json').read_bytes())
    s, server = serve(tmp_path)
    try:
        code, body = get(server)
        assert code == 200 and body['schema'] == 'strategy-fsm-view/1'
        assert body['source'] == 'ir' and body['atlas_state'] == 'missing' and body['current'] == 'tsmom_tx_mtx'
        assert [t['id'] for t in body['targets']] == ['tsmom_tx_mtx']
    finally:
        server.shutdown()


def test_endpoint_with_atlas_is_read_only_and_local_only(tmp_path):
    atlas = tmp_path / 'atlas.json'
    atlas.write_text(json.dumps(atlas_doc()), encoding='utf-8')
    before = atlas.read_bytes()
    s, server = serve(tmp_path, atlas)
    try:
        code, body = get(server)
        assert code == 200 and body['source'] == 'atlas' and body['tlc_ran'] is True
        assert [t['id'] for t in body['targets']] == ['demo']
        assert 'LEAK' not in json.dumps(body)
        assert get(server, method='POST')[0] in (400, 404, 415)  # 沒有任何寫入端點
        assert get(server, host='evil.example:80')[0] == 403
        assert atlas.read_bytes() == before
        assert not (tmp_path / 'log').exists() or not any((tmp_path / 'log').glob('*'))  # 不碰 ledger
    finally:
        server.shutdown()


def test_fixture_mode_ignores_configured_atlas_and_env(tmp_path, monkeypatch):
    other = tmp_path / 'real.json'
    other.write_text(json.dumps(atlas_doc()), encoding='utf-8')
    monkeypatch.setenv('GS_FSM_ATLAS', str(other))
    view = FsmView(tmp_path, other, fixture=True)
    body = view.payload(None)
    assert view.atlas_path == FIXTURE_ATLAS and body['source'] == 'fixture'
    assert [t['id'] for t in body['targets']] == ['fsm_demo']


def test_fixture_atlas_counterexample_has_three_fixed_sections():
    body = FsmView(REPO, fixture=True).payload(None)
    cex = next(i['counterexample'] for i in body['targets'][0]['invariants'] if i['counterexample'])
    for title in ('【這個反例用到的模型數字與來源】', '【它代表什麼、不代表什麼】'):
        assert title in cex['text_zh']
    text = json.dumps(body, ensure_ascii=False)
    assert '已證明' not in text and '證明正確' not in text


def test_strategy_id_only_from_strategies_graph(tmp_path):
    s, server = serve(tmp_path)
    try:
        assert s.strategy_id() == 'tsmom_tx_mtx'
        s.path = tmp_path / 'elsewhere' / 'graph.json'
        assert s.strategy_id() is None
    finally:
        server.shutdown()


def test_strategy_id_cannot_escape_strategies_dir(tmp_path):
    view = FsmView(tmp_path)
    assert view.ir_target('../../etc') is None and view.ir_target('a/b') is None
