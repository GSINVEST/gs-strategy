"""Agent 交易員分頁：六畫面渲染、空狀態、stale、HTML 跳脫、核准端點的權限與注入防護。"""
import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from strategies._common.graph import agent_trader as at
from strategies._common.graph.api import GraphHTTPServer

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)
XSS = '<script>alert(1)</script>'
DRAFT_ID = 'skill-0123456789'


def base(**kw):
    return {'schema_version': 1, 'generated_at': '2026-11-09T08:00:00+08:00', 'as_of_date': '2026-11-09',
            'data_as_of': '2026-11-09', 'stale': False, 'stale_reasons': [], 'stale_after_hours': 36,
            'warnings': [], **kw}


def latest_doc(x=''):
    series = [{'date': f'2026-11-0{i}', 'agent_version': 'v1' if i < 4 else 'v2' + x, 'nav': 1e6, 'nav_level': 1 + i / 100,
               'index_level': 1 + i / 200, 'counterfactual_level': None if i == 2 else 1.0} for i in range(1, 6)]
    return base(
        overview={'series': series, 'series_base': 1.0, 'days': 5, 'insufficient': True, 'caveat': '樣本少' + x,
                  'stats_note': '統計說明' + x, 'latest_nav': {'date': '2026-11-05', 'nav': 1050000.0, 'nav_biased': True},
                  'total_return': 0.05,
                  'segments': [{'agent_version': 'v1' + x, 'first_date': '2026-11-01', 'last_date': '2026-11-03', 'days': 3,
                                'cumulative_return': 0.02, 'vs_index': 0.01, 'vs_counterfactual': None, 'insufficient': True,
                                'caveat': 'c' + x}]},
        positions={'count': 1, 'max_positions': 15, 'open_slots': 14, 'cash': 500000.0,
                   'valuation': {'date': '2026-11-05', 'nav': 1050000.0, 'nav_biased': True, 'stale_symbols': ['9999' + x]},
                   'positions': [{'symbol': '2330' + x, 'shares': 1000, 'avg_cost': 600.0, 'last_price': None,
                                  'unrealized_pnl': -1200.0, 'unrealized_pnl_pct': -0.02, 'stop_price': 580.0,
                                  'target_price': 700.0, 'planned_hold_days': 5}]},
        alerts={'items': [{'code': 'AUDIT_FAILED' + x, 'date': '2026-11-05', 'severity': 'warning', 'message': 'm' + x}]},
        summary_card=None)


def day_doc(d, x=''):
    return base(
        as_of_date=d, date=d,
        decision={'date': d, 'status': 'valid', 'agent_version': 'v2' + x, 'action_counts': {'buy': 1, 'sell': 0, 'hold': 2},
                  'has_citation_issues': True, 'detail': 'd' + x},
        reasoning={'date': d, 'status': 'valid', 'lock_hash': 'a' * 64, 'agent_version': 'v2',
                   'theses': [{'id': 't1', 'content': '論點' + x, 'direction': 'long', 'theory_type': 'mom', 'weight': 0.5,
                               'valid': False, 'recorded_issues': ['NOT_IN_QUERY_LOG'],
                               'citations': [{'index': 0, 'source_type': 'db', 'source_id': 'prices', 'published_at': d,
                                              'citation_ok': False, 'reason': 'NOT_IN_QUERY_LOG'},
                                             {'index': 1, 'source_type': 'db', 'source_id': 'chips', 'published_at': d,
                                              'citation_ok': None, 'reason': 'QUERY_LOG_MISSING'},
                                             {'index': 2, 'source_type': 'db', 'source_id': 'news', 'published_at': d,
                                              'citation_ok': True}],
                               'used_by': [{'idx': 0, 'symbol': '2330' + x, 'kind': 'buy'}]}],
                   'queries': {'count': 3, 'by_tool': {'sql': 3}, 'chain_ok': True}, 'agent_reply_tail': '回覆' + x},
        review={'date': d, 'agent_version': 'v2', 'lock_status': 'valid', 'review_status': 'done', 'stats_note': '註' + x,
                'scorecard': {'performance': {'day_return': 0.01 }, 'calibration': {'insufficient': True, 'buckets': [{'b': '0.5' + x, 'n': 2}]},
                              'attribution': {'note': '簡化' + x}, 'actions': [], 'tracked_buys': [], 'thesis_issues': [], 'warnings': []},
                'report': {'report_md': '# 報告' + x, 'citation_failures': [], 'per_action': [],
                           'lessons': [{'kind': 'observation', 'text': '教訓' + x, 'tracking': {'type': 'memory', 'status': 'candidate', 'samples': 2}},
                                       {'kind': 'skill', 'text': 's', 'tracking': None}]}})


def drafts_doc(x='', status='pending_approval'):
    return base(drafts={'pending_count': 1, 'items': [
        {'id': DRAFT_ID, 'kind': 'skill', 'topic': '主題' + x, 'status': status,
         'diff': {'proposed_text': '提案' + x, 'baseline_text': None, 'baseline_note': '無基準' + x},
         'evidence_dates': ['2026-11-04', '2026-11-05'], 'evidence_days': 2, 'min_days': 10, 'remaining_days': 8,
         'decision': None, 'history': [{'event': 'created' + x}], 'stats_note': '不足' + x}]})


def write_state(root, x='', status='pending_approval'):
    (root / 'days').mkdir(parents=True)
    (root / 'latest.json').write_text(json.dumps(latest_doc(x)), encoding='utf-8')
    (root / 'drafts.json').write_text(json.dumps(drafts_doc(x, status)), encoding='utf-8')
    for d in ('2026-11-04', '2026-11-05'):
        (root / 'days' / f'{d}.json').write_text(json.dumps(day_doc(d, x)), encoding='utf-8')


def page(root, screen, date=None, headers=None, env=None, now=NOW):
    e = {at.ENV_UI_DIR: str(root), **(env or {})}
    return at.render_page(screen, date, headers or {}, e, now)


@pytest.fixture
def state(tmp_path):
    write_state(tmp_path / 's')
    return tmp_path / 's'


# ---------------------------------------------------------------- 渲染
def test_empty_states_do_not_crash(tmp_path):
    assert '尚無資料' in at.render_page('overview', None, {}, {}, NOW)
    assert '尚無資料' in at.render_page('overview', None, {}, {at.ENV_UI_DIR: str(tmp_path / 'nope')}, NOW)
    (tmp_path / 'empty').mkdir()
    for screen, _ in at.SCREENS:
        out = page(tmp_path / 'empty', screen)
        assert '<html' in out
        assert '尚無資料' in out or '無資料' in out


def test_overview(state):
    out = page(state, 'overview')
    assert '樣本天數' in out and '統計上尚不足' in out and '樣本少' in out
    assert '<svg' in out and 'l-nav' in out and 'l-cf' in out and 'seg1' in out  # 版本分段
    assert 'NAV 有缺價' in out and '5.00%' in out
    assert 'AUDIT_FAILED' in out


def test_positions(state):
    out = page(state, 'positions')
    assert '1 / 15' in out and '2330' in out and '14' in out
    assert '580.00' in out and '700.00' in out
    assert '—' in out  # last_price null 不是 0
    assert '缺價' in out


def test_decisions_and_reasoning_and_review(state):
    out = page(state, 'decisions')
    assert 'valid' in out and '買 1' in out and '2026-11-05' in out and '引用有問題' in out
    out = page(state, 'reasoning', '2026-11-04')
    assert 'class="badge bad">失效' in out and 'class="badge na">無法驗證' in out and 'class="badge ok">有效' in out
    assert 'cite bad' in out and 'cite na' in out and 'NOT_IN_QUERY_LOG' in out
    assert '論點' in out and '2330' in out and '2026-11-04' in out
    out = page(state, 'review', '2026-11-05')
    assert '覆盤狀態' in out and '校準桶' in out and '歸因' in out and '教訓清單' in out and '未追蹤' in out
    assert '# 報告' in out  # 純文字 <pre>
    assert '<pre># 報告' in out


def test_drafts_view_disabled_by_default(state):
    out = page(state, 'drafts')
    assert '提案' in out and '無基準' in out and '2026-11-04' in out and '還差 8' in out and '不足' in out
    assert '功能未啟用' in out and 'decide-btn' not in out


def test_drafts_buttons_only_when_enabled_and_identity(state):
    env = {at.ENV_APPROVE_CMD: 'x ui-decide', at.ENV_IDENTITY_HEADER: 'X-Auth-Request-Email'}
    assert 'decide-btn' not in page(state, 'drafts', env=env)  # 沒身分
    out = page(state, 'drafts', env=env, headers={'X-Auth-Request-Email': 'kevin@gsinvest.com.tw'})
    assert 'data-action="approve"' in out and 'data-action="reject"' in out
    assert 'decide-btn' not in page(state, 'drafts', env=env, headers={'X-Auth-Request-Email': '"><x'})
    s2 = state.parent / 's2'
    write_state(s2, status='approved')
    assert 'decide-btn' not in page(s2, 'drafts', env=env, headers={'X-Auth-Request-Email': 'kevin'})


# ---------------------------------------------------------------- stale
def test_stale_banner(tmp_path):
    write_state(tmp_path / 's')
    assert '資料已過期' not in page(tmp_path / 's', 'overview')
    assert 'AGE_EXCEEDED' in page(tmp_path / 's', 'overview', now=NOW + timedelta(hours=40))
    doc = latest_doc()
    doc.update(stale=True, stale_reasons=['DATA_LAG'])
    (tmp_path / 's' / 'latest.json').write_text(json.dumps(doc), encoding='utf-8')
    out = page(tmp_path / 's', 'positions')
    assert 'role="alert"' in out and 'DATA_LAG' in out and '不是最新資料' in out
    doc.update(stale=True, stale_reasons=[], schema_version=2)
    (tmp_path / 's' / 'latest.json').write_text(json.dumps(doc), encoding='utf-8')
    out = page(tmp_path / 's', 'overview')
    assert 'STALE' in out and 'schema_version' in out


# ---------------------------------------------------------------- 跳脫
def test_html_escaped_on_every_screen(tmp_path):
    write_state(tmp_path / 's', x=XSS)
    for screen, _ in at.SCREENS:
        out = page(tmp_path / 's', screen, '2026-11-05',
                   env={at.ENV_APPROVE_CMD: 'x', at.ENV_IDENTITY_HEADER: 'X-U'}, headers={'X-U': 'kevin'})
        assert '<script>alert' not in out, screen
        assert out.count('<script') == 1  # 只有我們自己的外部腳本標籤
        assert 'onerror' not in out.lower().replace('&lt;', '')
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page(tmp_path / 's', 'reasoning', '2026-11-05')
    assert '&lt;script&gt;' in page(tmp_path / 's', 'review', '2026-11-05')
    assert '&lt;script&gt;' in page(tmp_path / 's', 'drafts')


def test_corrupt_files_and_bad_dates(tmp_path):
    s = tmp_path / 's'
    write_state(s)
    (s / 'latest.json').write_text('{not json', encoding='utf-8')
    (s / 'days' / '2026-11-05.json').write_text('[]', encoding='utf-8')
    assert '損毀' in page(s, 'overview')
    assert page(s, 'reasoning', '../../etc/passwd')  # 非法日期被忽略
    assert '2026-11-05' in page(s, 'decisions')  # 損毀的逐日檔只是該列無資料
    (s / 'days' / '..x.json').write_text('{}', encoding='utf-8')
    assert at.list_days(s) == ['2026-11-05', '2026-11-04']


# ---------------------------------------------------------------- 核准
ENV = {at.ENV_APPROVE_CMD: '/opt/py -m agent_trader.ops ui-decide', at.ENV_IDENTITY_HEADER: 'X-Auth-Request-Email',
       at.ENV_APPROVE_ARGS: '--state-dir /st'}
HDR = {'X-Auth-Request-Email': 'kevin@gsinvest.com.tw'}


class Runner:
    def __init__(self, out=None, rc=0, exc=None):
        self.calls, self.out, self.rc, self.exc = [], out, rc, exc

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.rc, self.out or '', '')


OK_OUT = json.dumps({'status': 'ok', 'exit_code': 0})


def decide(state, body, env=None, headers=HDR, runner=None):
    runner = runner or Runner(OK_OUT)
    e = {at.ENV_UI_DIR: str(state), **ENV, **(env or {})}
    return at.handle_decide(body, headers, e, runner), runner


def test_decide_disabled_without_config(state):
    body = {'draft_id': DRAFT_ID, 'action': 'approve'}
    r = Runner(OK_OUT)
    for env in ({}, {at.ENV_UI_DIR: str(state)}, {at.ENV_UI_DIR: str(state), at.ENV_APPROVE_CMD: 'x ui-decide'},
                {at.ENV_UI_DIR: str(state), at.ENV_IDENTITY_HEADER: 'X-U'}):
        status, data = at.handle_decide(body, {'X-U': 'kevin'}, env, r)
        assert status == 503 and data['ok'] is False
    assert r.calls == []


def test_decide_requires_identity(state):
    for headers in ({}, {'X-Auth-Request-Email': ''}, {'X-Auth-Request-Email': '-rm'}, {'X-Auth-Request-Email': 'a b'},
                    {'X-Auth-Request-Email': 'a\nb'}, {'X-Auth-Request-Email': 'x' * 200}):
        (status, data), r = decide(state, {'draft_id': DRAFT_ID, 'action': 'approve'}, headers=headers)
        assert status == 403 and data['code'] == 'NO_IDENTITY'
        assert r.calls == []


def test_by_comes_from_server_identity_not_body(state):
    body = {'draft_id': DRAFT_ID, 'action': 'approve', 'by': 'evil', 'actor': 'evil', 'note': 'ok'}
    (status, data), r = decide(state, body)
    assert status == 200 and data['ok'] and data['by'] == 'kevin@gsinvest.com.tw'
    argv, kw = r.calls[0]
    assert argv == ['/opt/py', '-m', 'agent_trader.ops', 'ui-decide', 'approve', DRAFT_ID,
                    '--by=kevin@gsinvest.com.tw', '--note=ok', '--state-dir', '/st']
    assert not any('evil' in a for a in argv)
    assert kw['shell'] is False and isinstance(argv, list)


@pytest.mark.parametrize('bad', ['x; rm -rf /', '--by=evil', '-h', 'skill-01234567\n', '../skill-0123456789', 'SKILL-0123456789',
                                 'skill-0123456789 --by=evil', '', None, 5, ['skill-0123456789'], 'skill-xyz'])
def test_decide_rejects_malformed_draft_id(state, bad):
    (status, data), r = decide(state, {'draft_id': bad, 'action': 'approve'})
    assert status == 400 and not data['ok']
    assert r.calls == []


def test_decide_rejects_bad_action_and_note(state):
    for body in ({'draft_id': DRAFT_ID, 'action': 'delete'}, {'draft_id': DRAFT_ID, 'action': None},
                 {'draft_id': DRAFT_ID, 'action': 'approve', 'note': 'a\nb'}, {'draft_id': DRAFT_ID, 'action': 'approve', 'note': 'x' * 501},
                 {'draft_id': DRAFT_ID, 'action': 'approve', 'note': ['x']}):
        (status, data), r = decide(state, body)
        assert status == 400 and not data['ok'] and r.calls == []


def test_note_with_flag_text_stays_a_single_argv_item(state):
    (status, _), r = decide(state, {'draft_id': DRAFT_ID, 'action': 'reject', 'note': '--by=evil; $(x) `y`'})
    assert status == 200
    argv = r.calls[0][0]
    assert '--note=--by=evil; $(x) `y`' in argv and '--by=kevin@gsinvest.com.tw' in argv and len(argv) == 10


def test_decide_checks_snapshot_state_before_running(state, tmp_path):
    (status, data), r = decide(state, {'draft_id': 'skill-ffffffffff', 'action': 'approve'})
    assert status == 404 and r.calls == []
    s2 = tmp_path / 's2'
    write_state(s2, status='approved')
    (status, data), r = decide(s2, {'draft_id': DRAFT_ID, 'action': 'approve'})
    assert status == 409 and r.calls == []


def test_decide_maps_cli_denial_and_failures(state):
    denied = json.dumps({'status': 'denied', 'code': 'NOT_AUTHORIZED', 'error': 'secret detail', 'exit_code': 2})
    (status, data), _ = decide(state, {'draft_id': DRAFT_ID, 'action': 'approve'}, runner=Runner('log\n' + denied, rc=2))
    assert status == 403 and data['code'] == 'NOT_AUTHORIZED' and 'secret' not in json.dumps(data, ensure_ascii=False)
    denied = json.dumps({'status': 'denied', 'code': 'NO_APPROVERS_CONFIGURED'})
    (status, data), _ = decide(state, {'draft_id': DRAFT_ID, 'action': 'approve'}, runner=Runner(denied, rc=2))
    assert status == 403 and data['code'] == 'NO_APPROVERS_CONFIGURED'
    (status, data), _ = decide(state, {'draft_id': DRAFT_ID, 'action': 'approve'}, runner=Runner('garbage', rc=1))
    assert status == 502 and data['code'] == 'DECIDE_FAILED'
    (status, data), _ = decide(state, {'draft_id': DRAFT_ID, 'action': 'approve'}, runner=Runner(exc=subprocess.TimeoutExpired('x', 1)))
    assert status == 504
    (status, data), _ = decide(state, {'draft_id': DRAFT_ID, 'action': 'approve'}, runner=Runner(exc=FileNotFoundError()))
    assert status == 502
    # rc 0 但沒有 status ok 也不算成功
    (status, data), _ = decide(state, {'draft_id': DRAFT_ID, 'action': 'approve'}, runner=Runner('{}', rc=0))
    assert status == 502


def test_decide_with_real_subprocess_and_fake_cli(state, tmp_path):
    fake = tmp_path / 'fake_cli.py'
    fake.write_text(
        'import sys, json\n'
        'a = sys.argv[1:]\n'
        'by = [x for x in a if x.startswith("--by=")][0][5:]\n'
        'ok = by == "kevin@gsinvest.com.tw"\n'
        'print(json.dumps({"status": "ok"} if ok else {"status": "denied", "code": "NOT_AUTHORIZED"}))\n'
        'sys.exit(0 if ok else 2)\n', encoding='utf-8')
    env = {at.ENV_UI_DIR: str(state), at.ENV_APPROVE_CMD: f'"{sys.executable}" "{fake}"', at.ENV_IDENTITY_HEADER: 'X-U'}
    status, data = at.handle_decide({'draft_id': DRAFT_ID, 'action': 'approve'}, {'X-U': 'kevin@gsinvest.com.tw'}, env)
    assert status == 200 and data['ok']
    status, data = at.handle_decide({'draft_id': DRAFT_ID, 'action': 'approve'}, {'X-U': 'mallory'}, env)
    assert status == 403 and data['code'] == 'NOT_AUTHORIZED'


# ---------------------------------------------------------------- HTTP
@pytest.fixture
def server(state, monkeypatch):
    monkeypatch.setenv(at.ENV_UI_DIR, str(state))
    for k in (at.ENV_APPROVE_CMD, at.ENV_IDENTITY_HEADER, at.ENV_APPROVE_ARGS):
        monkeypatch.delenv(k, raising=False)
    srv = GraphHTTPServer(('127.0.0.1', 0), object())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def http(srv, path, data=None, headers=None):
    req = Request(f'http://127.0.0.1:{srv.server_port}{path}', data=None if data is None else json.dumps(data).encode(),
                  headers=headers if headers is not None else ({'Content-Type': 'application/json'} if data is not None else {}))
    try:
        with urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_http_pages_and_security_headers(server):
    for screen, _ in at.SCREENS:
        status, hdr, body = http(server, f'/agent-trader?screen={screen}')
        assert status == 200 and hdr['Content-Type'].startswith('text/html')
        assert "script-src 'self'" in hdr['Content-Security-Policy'] and hdr['X-Frame-Options'] == 'DENY'
        assert '<title>Agent 交易員</title>'.encode() in body
    status, hdr, body = http(server, '/agent-trader/app.js')
    assert status == 200 and 'javascript' in hdr['Content-Type'] and b'/api/agent-trader/decide' in body
    assert http(server, '/agent-trader/other')[0] == 404


def test_http_decide_disabled_by_default(server):
    status, _, body = http(server, '/api/agent-trader/decide', {'draft_id': DRAFT_ID, 'action': 'approve'})
    assert status == 503 and json.loads(body)['code'] == 'APPROVAL_DISABLED'
    status, _, _ = http(server, '/api/agent-trader/decide', {'draft_id': DRAFT_ID, 'action': 'approve'}, headers={'Content-Type': 'text/plain'})
    assert status == 415
    status, _, _ = http(server, '/api/agent-trader/decide', {'draft_id': DRAFT_ID, 'action': 'approve'},
                        headers={'Content-Type': 'application/json', 'Origin': 'https://evil.example'})
    assert status == 403
    assert http(server, '/api/agent-trader/decide')[0] == 404  # GET 不能核准


def test_http_decide_uses_header_identity(server, monkeypatch, tmp_path):
    fake = tmp_path / 'cli.py'
    fake.write_text('import sys,json\nprint(json.dumps({"status":"ok","argv":sys.argv[1:]}))\n', encoding='utf-8')
    monkeypatch.setenv(at.ENV_APPROVE_CMD, f'"{sys.executable}" "{fake}"')
    monkeypatch.setenv(at.ENV_IDENTITY_HEADER, 'X-Auth-Request-Email')
    body = {'draft_id': DRAFT_ID, 'action': 'approve', 'by': 'evil'}
    status, _, raw = http(server, '/api/agent-trader/decide', body)
    assert status == 403 and json.loads(raw)['code'] == 'NO_IDENTITY'
    status, _, raw = http(server, '/api/agent-trader/decide', body,
                          headers={'Content-Type': 'application/json', 'X-Auth-Request-Email': 'kevin@gsinvest.com.tw'})
    assert status == 200 and json.loads(raw)['by'] == 'kevin@gsinvest.com.tw'
