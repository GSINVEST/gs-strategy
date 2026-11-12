"""Backtest result store (SQLite, ``data/backtests.sqlite``).

Every non-preview graph execution is recorded, including refusals (e.g. the
point-in-time guard) and cached replays.  The ledger (``log/trials.jsonl``)
stays the only source of N; this store answers "what did each run produce",
keyed by the same ``backtest_key``:

* ``runs``     one row per execution: strategy, graph/backtest identity,
               status, whether it added a selection trial, N afterwards,
               model/engine, headline metrics, the node states;
* ``returns``  the daily return series, once per ``backtest_key``
               (re-running a cached configuration adds a run, not new rows);
* ``details``  JSON blobs per ``backtest_key`` (folds, run info, report);
* ``experiments`` / ``trials``  multi-trial evidence: Monte Carlo paths of
               a run (one trial per path) and imported tournament ledgers
               (gs-MINT), each with its aggregated summary.

Stdlib SQLite in WAL mode: no new dependency, safe for the graph server's
single writer plus readers; DuckDB can ``ATTACH`` the file for analysis.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, strategy TEXT, status TEXT NOT NULL,
  message TEXT, graph_hash TEXT, document_graph_hash TEXT, backtest_key TEXT,
  new_trial INTEGER NOT NULL DEFAULT 0, selection_n INTEGER, engine TEXT, model TEXT,
  sharpe REAL, psr REAL, dsr REAL, max_drawdown REAL, cagr REAL, n_days INTEGER,
  states TEXT, params TEXT
);
CREATE INDEX IF NOT EXISTS runs_strategy ON runs(strategy, recorded_at);
CREATE INDEX IF NOT EXISTS runs_key ON runs(backtest_key);
CREATE TABLE IF NOT EXISTS returns (
  backtest_key TEXT NOT NULL, date TEXT NOT NULL, ret REAL, PRIMARY KEY (backtest_key, date)
);
CREATE TABLE IF NOT EXISTS details (
  backtest_key TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (backtest_key, kind)
);
CREATE TABLE IF NOT EXISTS experiments (
  experiment_id TEXT PRIMARY KEY, kind TEXT NOT NULL, strategy TEXT, source TEXT, backtest_key TEXT,
  created_at TEXT NOT NULL, n_trials INTEGER NOT NULL, config TEXT, summary TEXT
);
CREATE INDEX IF NOT EXISTS experiments_strategy ON experiments(strategy, created_at);
CREATE TABLE IF NOT EXISTS trials (
  experiment_id TEXT NOT NULL, trial_id TEXT NOT NULL, stage TEXT, score REAL, metrics TEXT,
  PRIMARY KEY (experiment_id, trial_id)
);
"""


def default_path(root):
    return Path(root) / 'data' / 'backtests.sqlite'


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(SCHEMA)
    # Additive migration for stores created before per-user attribution existed.
    if 'actor' not in {r[1] for r in conn.execute('PRAGMA table_info(runs)')}:
        conn.execute('ALTER TABLE runs ADD COLUMN actor TEXT')
    return conn


def _num(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _find(values, port):
    for outputs in values.values():
        if isinstance(outputs, dict) and port in outputs:
            return outputs[port]
    return None


def _model(graph):
    for node in (graph or {}).get('nodes', []):
        if node.get('type') == 'agent.llm_view':
            return node.get('params', {}).get('model')
    return None


def _engine(graph):
    for node in (graph or {}).get('nodes', []):
        if str(node.get('type', '')).startswith('backtest.'):
            return node['type']
    return None


def record(path, *, run_id, status, message, context, values, states, selection_n, new_trial):
    """Persist one execution. Never raises into the caller's job (best effort)."""
    import pandas as pd
    graph = (context.snapshot or {}).get('graph') or {}
    report = _find(values, 'Report') or {}
    returns = _find(values, 'Returns')
    key = context.backtest_key or None
    row = {
        'run_id': run_id, 'recorded_at': datetime.now(timezone.utc).isoformat(),
        'strategy': graph.get('strategy'), 'status': status, 'message': message,
        'graph_hash': context.graph_hash or None,
        'document_graph_hash': context.services.get('document_graph_hash'), 'backtest_key': key,
        'new_trial': int(bool(new_trial)), 'selection_n': selection_n,
        'engine': _engine(graph), 'model': _model(graph),
        'sharpe': _num(report.get('annualized_sharpe')), 'psr': _num(report.get('psr')),
        'dsr': _num(report.get('dsr')), 'max_drawdown': _num(report.get('max_drawdown')),
        'cagr': _num(report.get('cagr')), 'n_days': report.get('n_days'),
        'actor': context.services.get('actor'),
        'states': json.dumps({k: v.get('status') for k, v in states.items()}, ensure_ascii=False),
        'params': json.dumps({n['id']: n.get('params', {}) for n in graph.get('nodes', [])},
                             ensure_ascii=False, default=str),
    }
    with closing(connect(path)) as conn, conn:
        conn.execute(f'INSERT OR REPLACE INTO runs ({",".join(row)}) VALUES ({",".join("?" * len(row))})',
                     list(row.values()))
        if key and isinstance(returns, (pd.Series, pd.DataFrame)):
            series = returns if isinstance(returns, pd.Series) else returns.iloc[:, 0]
            conn.executemany('INSERT OR IGNORE INTO returns VALUES (?,?,?)',
                             [(key, pd.Timestamp(d).date().isoformat(), _num(v)) for d, v in series.items()])
        monte = _find(values, 'MonteCarlo')
        if key and isinstance(monte, dict) and status == 'complete':
            _store_monte_carlo(conn, key, graph.get('strategy'), monte)
        if key:
            for kind, port in (('walk_forward', 'WalkForward'), ('run_info', 'RunInfo'), ('report', 'Report')):
                body = _find(values, port)
                if body is not None:
                    if kind == 'walk_forward':
                        body = {k: v for k, v in body.items() if k != 'benchmark'}
                    conn.execute('INSERT OR REPLACE INTO details VALUES (?,?,?)',
                                 (key, kind, json.dumps(body, ensure_ascii=False, default=str)))


def runs(path, strategy=None, limit=50):
    if not Path(path).is_file():
        return []
    with closing(connect(path)) as conn:
        sql = ('SELECT run_id, recorded_at, strategy, status, message, backtest_key, new_trial, selection_n, '
               'engine, model, sharpe, psr, dsr, max_drawdown, cagr, n_days, actor FROM runs')
        args = []
        if strategy:
            sql += ' WHERE strategy = ?'
            args.append(strategy)
        sql += ' ORDER BY rowid DESC LIMIT ?'
        args.append(int(limit))
        return [dict(r) for r in conn.execute(sql, args)]


def run_detail(path, run_id):
    with closing(connect(path)) as conn:
        row = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out['states'] = json.loads(out['states'] or '{}')
        out['params'] = json.loads(out['params'] or '{}')
        key = out['backtest_key']
        rets = conn.execute('SELECT date, ret FROM returns WHERE backtest_key = ? ORDER BY date', (key,)).fetchall()
        equity, level = [], 1.0
        for r in rets:
            level *= 1 + (r['ret'] or 0.0)
            equity.append([r['date'], level])
        out['equity'] = equity
        out['details'] = {r['kind']: json.loads(r['body']) for r in
                          conn.execute('SELECT kind, body FROM details WHERE backtest_key = ?', (key,))}
        return out


def _store_monte_carlo(conn, key, strategy, monte):
    experiment_id = f"mc:{key[:16]}:{monte['n_paths']}:{monte['mean_block']:g}:{monte['seed']}"
    summary = {k: v for k, v in monte.items() if not str(k).startswith('_')}
    trials = [{'trial_id': f'path_{i:05d}', 'stage': 'bootstrap', 'score': row[0],
               'metrics': {'sharpe': row[0], 'max_drawdown': row[1], 'cagr': row[2]}}
              for i, row in enumerate(monte.get('_paths') or [])]
    _write_experiment(conn, experiment_id=experiment_id, kind='monte_carlo', strategy=strategy,
                      source='validation.monte_carlo', config={k: monte[k] for k in ('method', 'n_paths', 'mean_block', 'seed')},
                      summary=summary, trials=trials, backtest_key=key)


def _write_experiment(conn, *, experiment_id, kind, strategy, source, config, summary, trials, backtest_key):
    conn.execute('INSERT OR REPLACE INTO experiments VALUES (?,?,?,?,?,?,?,?,?)', (
        experiment_id, kind, strategy, source, backtest_key, datetime.now(timezone.utc).isoformat(), len(trials),
        json.dumps(config, ensure_ascii=False, default=str), json.dumps(summary, ensure_ascii=False, default=str)))
    conn.execute('DELETE FROM trials WHERE experiment_id = ?', (experiment_id,))
    conn.executemany('INSERT INTO trials VALUES (?,?,?,?,?)', [
        (experiment_id, str(t['trial_id']), t.get('stage'), _num(t.get('score')),
         json.dumps(t.get('metrics') or {}, ensure_ascii=False, default=str)) for t in trials])


def record_experiment(path, **kwargs):
    with closing(connect(path)) as conn, conn:
        _write_experiment(conn, **kwargs)


def experiments(path, strategy=None, limit=50):
    if not Path(path).is_file():
        return []
    with closing(connect(path)) as conn:
        sql = 'SELECT experiment_id, kind, strategy, source, backtest_key, created_at, n_trials, summary FROM experiments'
        args = []
        if strategy:
            sql += ' WHERE strategy = ? OR strategy IS NULL'
            args.append(strategy)
        sql += ' ORDER BY rowid DESC LIMIT ?'
        args.append(int(limit))
        out = []
        for r in conn.execute(sql, args):
            row = dict(r)
            summary = json.loads(row.pop('summary') or '{}')
            row['headline'] = _headline(row['kind'], summary)
            out.append(row)
        return out


def _headline(kind, s):
    if kind == 'monte_carlo':
        return {'sharpe_p50': s.get('sharpe', {}).get('p50'), 'sharpe_p05': s.get('sharpe', {}).get('p05'),
                'sharpe_p95': s.get('sharpe', {}).get('p95'), 'prob_sharpe_le_0': s.get('prob_sharpe_le_0')}
    if kind == 'codegen':
        return {'papers': s.get('papers'), 'admitted': len(s.get('admitted') or [])}
    if kind == 'mint_tournament':
        return {'n_for_dsr': s.get('n_for_dsr'), 'best_score': (s.get('best_trial') or {}).get('score_oos_net_t'),
                'positive_share': (s.get('score_oos_net_t') or {}).get('positive_share'), 'pbo': s.get('pbo')}
    return {}


def experiment_detail(path, experiment_id, top=50):
    with closing(connect(path)) as conn:
        row = conn.execute('SELECT * FROM experiments WHERE experiment_id = ?', (experiment_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out['config'] = json.loads(out['config'] or '{}')
        out['summary'] = json.loads(out['summary'] or '{}')
        out['top_trials'] = [{**dict(t), 'metrics': json.loads(t['metrics'] or '{}')} for t in conn.execute(
            'SELECT trial_id, stage, score, metrics FROM trials WHERE experiment_id = ? '
            'ORDER BY score DESC LIMIT ?', (experiment_id, int(top)))]
        return out
