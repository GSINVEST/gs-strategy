"""Strategy pool: gs-zipline-tej's registry is the single source of truth.

The pool is read through gs-zipline-tej's public API only
(``dashboard.strategies.registry.refresh / list_strategies / list_factors``
and ``dashboard.runner.run_backtest``); that repository, gs-FORGE and gs-MINT
are never modified.  Calls run in a subprocess with the backtest interpreter
(``.venv-bt``: zipline, the TEJ calendar), so this process never imports
zipline and an external strategy can never crash the graph server.

Selection rules:

* a gs-strategy strategy that ships ``strategies/<id>/graph.json`` opens that
  graph (e.g. ``tsmom_tx_mtx``, ``llm_view_tx``);
* every other pool entry opens a generated *coarse graph*
  (``strategies/_pool/<id>/graph.json``): bundle -> declared spec -> Zipline
  run through the gs-zipline-tej runner -> ledger / report / statistics.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from strategies._common.graph.core import GraphError

REPO = Path(__file__).resolve().parents[2]
_ID = re.compile(r'^[a-z][a-z0-9_]*$')
_CACHE = {'at': 0.0, 'value': None}
_LOCK = threading.Lock()
TTL_SECONDS = 60

_LIST_SCRIPT = r'''
import json, sys
from dashboard.strategies import registry
registry.refresh()
def row(s, pool):
    d = s.to_dict()
    keep = ("id", "name", "description", "asset_class", "bundle", "start", "end", "capital_base",
            "tags", "requires_tej_key", "is_external", "bundle_dir", "origin", "pool", "path",
            "calendar", "provenance", "validation", "params", "symbols", "qualified")
    return {k: d.get(k) for k in keep}
out = {"strategies": [row(s, "strategy") for s in registry.list_strategies()],
       "factors": [row(s, "factor") for s in registry.list_factors()],
       "errors": [e if isinstance(e, dict) else getattr(e, "to_dict", lambda: str(e))()
                  for e in registry.list_import_errors()]}
sys.stdout.write("\n__POOL__" + json.dumps(out, default=str))
'''

_FACTOR_SCRIPT = r'''
import json, sys
from dashboard.strategies import registry
from dashboard.runner import run_factor_backtest
registry.refresh()
args = json.loads(sys.argv[1])
result = run_factor_backtest(args["factor_id"], args["filter_config"], start=args["start"], end=args["end"],
                             capital_base=args["capital_base"], timeout=args["timeout"])
sys.stdout.write("\n__RESULT__" + json.dumps(result.to_dict(), default=str))
'''

_RUN_SCRIPT = r'''
import json, sys
from dashboard.strategies import registry
from dashboard.runner import run_backtest
registry.refresh()
args = json.loads(sys.argv[1])
result = run_backtest(args["strategy_id"], start=args["start"], end=args["end"],
                      capital_base=args["capital_base"], timeout=args["timeout"])
sys.stdout.write("\n__RESULT__" + json.dumps(result.to_dict(), default=str))
'''


def zipline_root():
    return Path(os.environ.get('GS_ZIPLINE_TEJ_ROOT', Path.home() / 'gs-zipline-tej')).expanduser()


def bt_python():
    override = os.environ.get('GS_BT_PYTHON')
    if override:
        return Path(override)
    for candidate in (REPO / '.venv-bt' / 'bin' / 'python', REPO / '.venv-bt' / 'Scripts' / 'python.exe',
                      REPO.parent / 'gs-strategy' / '.venv-bt' / 'bin' / 'python'):
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


def _env():
    env = dict(os.environ)
    # LLM-generated bundles that passed the codegen gates live one level down.
    dirs = [str(REPO / 'strategies'), str(REPO / 'strategies' / '_llm_generated')] + \
        [d for d in env.get('DASHBOARD_STRATEGY_DIRS', '').split(':') if d]
    env['DASHBOARD_STRATEGY_DIRS'] = ':'.join(dict.fromkeys(dirs))
    env.setdefault('GS_STRATEGY_ROOT', str(REPO / 'strategies'))
    # Built-in examples run through the zipline CLI of the backtest interpreter.
    env['PATH'] = str(bt_python().parent) + os.pathsep + env.get('PATH', '')
    # Worktrees share the main checkout's .env (secrets are never copied around).
    dotenv = next((p for p in (REPO / '.env', REPO.parent / 'gs-strategy' / '.env') if p.is_file()), None)
    if dotenv is not None and not env.get('TEJAPI_KEY'):
        for line in dotenv.read_text(encoding='utf-8').splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() in ('TEJAPI_KEY', 'TEJAPI_BASE') and value.strip():
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _call(script, marker, *args, timeout):
    root = zipline_root()
    if not (root / 'dashboard' / 'strategies' / 'registry.py').is_file():
        raise GraphError('找不到 gs-zipline-tej 策略池（設定 GS_ZIPLINE_TEJ_ROOT）')
    try:
        proc = subprocess.run([str(bt_python()), '-c', script, *args], cwd=root, env=_env(),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise GraphError('策略池程序逾時') from exc
    head, sep, payload = proc.stdout.rpartition(marker)
    if not sep:
        # Third-party output may carry secrets: never echo it.
        raise GraphError('策略池程序執行失敗')
    return json.loads(payload)


def _local_graphs():
    """gs-strategy strategies that ship a hand-built or composed graph."""
    out = {}
    for path in sorted((REPO / 'strategies').glob('*/graph.json')):
        sid = path.parent.name
        if sid.startswith('_') or not _ID.match(sid):
            continue
        try:
            graph = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        out[sid] = {'graph_path': path.relative_to(REPO).as_posix(), 'nodes': len(graph.get('nodes', []))}
    return out


def pool(refresh=False):
    """Pool listing for the dashboard menu (cached briefly)."""
    with _LOCK:
        if not refresh and _CACHE['value'] is not None and time.time() - _CACHE['at'] < TTL_SECONDS:
            return _CACHE['value']
        local = _local_graphs()
        error = None
        try:
            raw = _call(_LIST_SCRIPT, '__POOL__', timeout=120)
        except GraphError as exc:
            raw, error = {'strategies': [], 'factors': [], 'errors': []}, str(exc)
        entries = []
        seen = set()
        for s in raw['strategies']:
            sid = s['id']
            seen.add(sid)
            entries.append({**s, 'graph': 'composed' if sid in local else 'coarse',
                            'graph_path': local.get(sid, {}).get('graph_path'), 'selectable': bool(_ID.match(sid))})
        for sid, info in local.items():
            if sid not in seen:
                entries.append({'id': sid, 'name': sid, 'description': '', 'origin': 'gs-strategy-graph',
                                'pool': 'strategy', 'asset_class': 'future', 'graph': 'composed',
                                'graph_path': info['graph_path'], 'selectable': True, 'tags': []})
        factors = [{**f, 'graph': 'coarse', 'graph_path': None, 'selectable': bool(_ID.match(f['id']))}
                   for f in raw['factors']]
        value = {'strategies': entries, 'factors': factors, 'import_errors': len(raw['errors']),
                 'pool_error': error, 'zipline_root': zipline_root().name}
        _CACHE.update(at=time.time(), value=value)
        return value


def entry(strategy_id):
    listing = pool()
    for s in listing['strategies'] + listing['factors']:
        if s['id'] == strategy_id:
            return s
    raise GraphError('策略池中沒有這個策略')


def bundle_version(meta):
    """Content hash of the files that define the strategy (pins N and caches)."""
    digest = hashlib.sha256()
    if meta.get('bundle_dir'):
        base = Path(meta['bundle_dir'])
        files = sorted(p for p in base.rglob('*') if p.is_file() and p.suffix in ('.py', '.yaml', '.yml', '.md', '.json')
                       and '__pycache__' not in p.parts and p.name != 'graph.json')
    else:
        base = zipline_root()
        files = [base / meta['path']] if meta.get('path') else []
    if not files:
        raise GraphError('找不到策略原始碼')
    for path in files:
        digest.update(path.relative_to(base).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def default_filter(meta):
    """Factor-pool filter: the qualified config when the factor has one, else a top-N long book."""
    qualified = (meta.get('qualified') or {}).get('filter_config') if isinstance(meta.get('qualified'), dict) else None
    if isinstance(qualified, dict):
        return {k: qualified[k] for k in ('mode', 'direction', 'n', 'weighting', 'rebalance') if k in qualified}
    inputs = ((meta.get('provenance') or {}).get('inputs') or {})
    return {'mode': 'long_only_topn', 'direction': 'low' if inputs.get('direction') == 'short' else 'high',
            'n': 20, 'weighting': 'equal', 'rebalance': 'monthly'}


def coarse_graph(meta):
    """bundle -> declared spec -> Zipline (gs-zipline-tej runner) -> statistics."""
    sid = meta['id']
    factor = meta.get('pool') == 'factor'
    backtest = ({'id': 'backtest', 'type': 'backtest.pool_factor', 'params': default_filter(meta)} if factor
                else {'id': 'backtest', 'type': 'backtest.pool_zipline', 'params': {}})
    return {
        'schema': 'live-strategy-graph/1', 'strategy': sid,
        'nodes': [
            {'id': 'bundle', 'type': 'data.pool_strategy',
             'params': {'strategy_id': sid, 'start': str(meta.get('start') or ''), 'end': str(meta.get('end') or ''),
                        'capital_base': float(meta.get('capital_base') or 1_000_000), 'bundle_version': 'auto'}},
            {'id': 'spec', 'type': 'feature.strategy_spec', 'params': {}},
            backtest,
            {'id': 'ledger', 'type': 'ledger.selection_n', 'params': {}},
            {'id': 'montecarlo', 'type': 'validation.monte_carlo', 'params': {}},
            {'id': 'report', 'type': 'validation.report', 'params': {}},
            {'id': 'facts', 'type': 'stat.facts', 'params': {}},
            {'id': 'resolve', 'type': 'stat.resolve', 'params': {}},
        ],
        'edges': [
            {'from': ['bundle', 'StrategyBundle'], 'to': ['spec', 'StrategyBundle']},
            {'from': ['bundle', 'StrategyBundle'], 'to': ['backtest', 'StrategyBundle']},
            {'from': ['spec', 'StrategySpec'], 'to': ['backtest', 'StrategySpec']},
            {'from': ['backtest', 'Returns'], 'to': ['report', 'Returns']},
            {'from': ['backtest', 'Returns'], 'to': ['facts', 'Returns']},
            {'from': ['ledger', 'LedgerN'], 'to': ['report', 'LedgerN']},
            {'from': ['ledger', 'LedgerN'], 'to': ['resolve', 'LedgerN']},
            {'from': ['facts', 'Facts'], 'to': ['resolve', 'Facts']},
            {'from': ['backtest', 'Returns'], 'to': ['montecarlo', 'Returns']},
        ],
    }


def run_backtest(strategy_id, *, start, end, capital_base, timeout=900):
    """Run one pool strategy through gs-zipline-tej's runner; returns its result dict."""
    args = json.dumps({'strategy_id': strategy_id, 'start': start or None, 'end': end or None,
                       'capital_base': capital_base, 'timeout': timeout})
    return _call(_RUN_SCRIPT, '__RESULT__', args, timeout=timeout + 60)


def run_factor_backtest(factor_id, filter_config, *, start, end, capital_base, timeout=900):
    """Backtest a Factor Pool bundle as a stock-selection filter via gs-zipline-tej."""
    args = json.dumps({'factor_id': factor_id, 'filter_config': filter_config, 'start': start or None,
                       'end': end or None, 'capital_base': capital_base, 'timeout': timeout})
    return _call(_FACTOR_SCRIPT, '__RESULT__', args, timeout=timeout + 60)
