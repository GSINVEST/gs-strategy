"""Paper -> LLM strategy codegen gates (offline: model and Zipline are faked)."""
import json
import sqlite3

import pytest
import yaml

from quant_crawler.strategy_gen import llm_codegen as cg
from strategies._common import results

GOOD = '''from zipline.api import order_target_percent, record, symbol
import numpy as np

def initialize(context):
    context.assets = [symbol(s) for s in context.params.get("symbols", ["2330"])]
    context.i = 0

def handle_data(context, data):
    context.i += 1
    for a in context.assets:
        order_target_percent(a, 0.5 if context.i % 10 < 5 else 0.0)
    record(i=context.i)
'''


@pytest.mark.parametrize('source, needle', [
    ('import os\ndef initialize(c): pass\ndef handle_data(c, d): pass\n', 'import os'),
    ('from subprocess import run\n', 'subprocess'),
    ('def initialize(c):\n    open("x")\n', 'open'),
    ('def initialize(c):\n    c.__class__\n', '__class__'),
    ('def initialize(c)\n', '語法錯誤'),
])
def test_static_gate_rejects_unsafe_code(source, needle):
    assert any(needle in p for p in cg.static_gate(source))


def test_static_gate_accepts_the_reference_and_symbol_gate_checks_the_bundle():
    assert cg.static_gate(GOOD) == [] and cg.static_gate(cg.REFERENCE) == []
    assert cg.symbol_gate(GOOD, ['2330'], ['2330', '2317']) == []
    assert cg.symbol_gate(GOOD, ['0050'], ['2330']) == ['標的不在本機 bundle：0050']
    assert cg.symbol_gate(GOOD.replace('["2330"]', '["2330"]') + '\nX = symbol("9999")\n', [], ['2330'])
    assert cg.symbol_gate(GOOD, ['0050'], []) == []          # unknown universe: G1 decides


@pytest.fixture
def papers_db(tmp_path):
    db = tmp_path / 'papers.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE papers (source TEXT, source_id TEXT, title TEXT, abstract TEXT, published TEXT, '
                     'url TEXT, categories TEXT, keywords_hit TEXT, fetched_at TEXT)')
        conn.executemany('INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?)', [
            ('arxiv', '1', 'Time-series momentum trading strategy for index futures',
             'We propose a trend following trading strategy with entry and exit rules.', '2025-01-01', 'u1', '[]', '[]', '2026-01-03'),
            ('arxiv', '2', 'A survey of monetary policy', 'Central bank communication.', '2025-01-01', 'u2', '[]', '[]', '2026-01-02'),
            ('arxiv', '3', 'Pairs trading strategy with mean reversion', 'A statistical arbitrage trading strategy.',
             '2025-01-01', 'u3', '[]', '[]', '2026-01-01'),
        ])
    return db


def test_candidates_need_an_explicit_strategy_cue_and_skip_attempts(papers_db):
    keys = [p['key'] for p in cg.candidate_papers(papers_db, set(), 10)]
    assert 'arxiv:2' not in keys and keys[:2] == ['arxiv:1', 'arxiv:3']
    assert [p['key'] for p in cg.candidate_papers(papers_db, {'arxiv:1'}, 10)] == ['arxiv:3']


@pytest.fixture
def faked(tmp_path, monkeypatch, papers_db):
    replies = {}

    def chat(model, messages, *, cache, temperature, max_tokens):
        assert '2330' in messages[-1]['content']                 # the local universe is in the prompt
        title = messages[-1]['content'].split('# ', 1)[1].split('\n', 1)[0]
        return {'content': replies[title], 'reasoning': '', 'cached': False, 'call_key': 'k'}
    from strategies._common.compose import llm
    monkeypatch.setattr(llm, 'chat', chat)
    monkeypatch.setattr(cg, 'paper_text', lambda p: (f"# {p['title']}\n\n{p['abstract']}", False))
    monkeypatch.setattr(cg, 'bundle_symbols', lambda bundle='tquant': ['2317', '2330'])
    smoke = {'value': {'status': 'ok', 'stderr': '', 'trades': 12, 'reductions': 5}}

    def run_zipline(script, marker, args, staging):
        if marker == '__V__':
            return {'ok': True}
        return smoke['value']
    monkeypatch.setattr(cg, '_run_zipline', run_zipline)
    monkeypatch.setattr(cg, 'OUT_DIR', tmp_path / 'pool')
    return {'replies': replies, 'smoke': smoke, 'db': tmp_path / 'backtests.sqlite', 'papers': papers_db}


def spec(sid, source=GOOD, symbols=('2330',)):
    return json.dumps({'id': sid, 'name': sid, 'description': 'd', 'symbols': list(symbols), 'params': {'k': 1},
                       'mapping': 'm', 'rationale': 'r', 'strategy_py': source})


def test_batch_admits_only_gated_bundles_and_records_every_attempt(faked):
    faked['replies'].update({
        'Time-series momentum trading strategy for index futures': spec('tsmom_tw'),
        'Pairs trading strategy with mean reversion': spec('pairs_tw', symbols=('0050',)),
    })
    summary, trials = cg.run(limit=5, papers_db=faked['papers'], results_db=faked['db'])
    assert summary['admitted'] == ['llm_tsmom_tw']
    by_key = {t['trial_id']: t for t in trials}
    assert by_key['arxiv:3']['stage'] == 'G0' and '0050' in by_key['arxiv:3']['metrics']['reason']
    manifest = yaml.safe_load((cg.OUT_DIR / 'llm_tsmom_tw' / 'manifest.yaml').read_text(encoding='utf-8'))
    assert manifest['tags'][:2] == ['llm-generated', 'unreviewed']
    assert manifest['source']['inputs']['paper']['key'] == 'arxiv:1' and manifest['source']['gates']['G2'].startswith('trades=12')
    exp = results.experiments(faked['db'])
    assert len(exp) == 1 and exp[0]['kind'] == 'codegen' and exp[0]['headline'] == {'papers': 2, 'admitted': 1}
    # Attempted papers are never retried, pass or fail.
    summary, trials = cg.run(limit=5, papers_db=faked['papers'], results_db=faked['db'])
    assert summary['papers'] == 0 and trials == []


def test_buy_and_hold_clones_and_broken_runs_stay_out(faked):
    faked['replies']['Time-series momentum trading strategy for index futures'] = spec('hold_tw')
    faked['replies']['Pairs trading strategy with mean reversion'] = 'no json here'
    faked['smoke']['value'] = {'status': 'ok', 'stderr': '', 'trades': 1, 'reductions': 0}
    summary, trials = cg.run(limit=5, papers_db=faked['papers'], results_db=faked['db'])
    assert summary['admitted'] == [] and not cg.OUT_DIR.exists()
    reasons = {t['trial_id']: t['metrics']['reason'] for t in trials}
    assert '疑似買進持有' in reasons['arxiv:1'] and '不是可用的 JSON' in reasons['arxiv:3']


def test_smoke_failure_keeps_only_the_exception_class(faked):
    faked['replies']['Time-series momentum trading strategy for index futures'] = spec('broken_tw')
    # A secret-shaped value built at runtime (not a literal) must never reach the record.
    fake_secret = 'sk-' + 'q7' * 16
    faked['smoke']['value'] = {'status': 'error', 'stderr': f'auth {fake_secret} SymbolNotFound: x'}
    _, trials = cg.run(limit=1, papers_db=faked['papers'], results_db=faked['db'])
    assert trials[0]['metrics']['reason'] == '煙霧測試失敗（SymbolNotFound）'
    assert fake_secret not in json.dumps(trials, ensure_ascii=False)
