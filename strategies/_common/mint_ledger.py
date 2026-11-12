"""Read a gs-MINT tournament ledger (``trial_ledger.jsonl``) by its published contract.

Contract: GSINVEST/gs-MINT ``docs/contracts/trial-ledger-fields.md``.  gs-MINT
is not imported or modified; only the documented fields are read:

* rows are mixed: only ``event_type == "trial"`` rows are trading trials;
  ``criteria_change_trial`` rows have no ``pool_id`` / ``score_oos_net_t``
  but still count toward the trial number used by DSR;
* ``(run_id, trial_id)`` is unique; deep ``trial_id`` values contain colons;
* ``returns_path`` is relative to the ledger's directory (OOS *monthly*
  returns per trial) and lets PBO be rebuilt from the month x trial matrix.

    python -m strategies._common.mint_ledger import path/to/trial_ledger.jsonl [--db data/backtests.sqlite]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)


def read(path):
    rows = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _returns_matrix(base, trials):
    columns = {}
    for t in trials:
        rel = t.get('returns_path')
        if not rel:
            continue
        p = (base / rel).resolve()
        if not p.is_file() or not p.is_relative_to(base.resolve()):
            continue
        frame = pd.read_parquet(p)
        series = frame.select_dtypes('number').iloc[:, -1] if frame.shape[1] > 1 else frame.iloc[:, 0]
        if not isinstance(frame.index, pd.DatetimeIndex):
            date_col = next((c for c in frame.columns if 'date' in str(c).lower() or str(c).lower() in ('month', 'dt')), None)
            if date_col is not None:
                series.index = pd.to_datetime(frame[date_col])
        columns[t['trial_id']] = pd.to_numeric(series, errors='coerce')
    return pd.DataFrame(columns).dropna() if columns else None


def summarize(path):
    """One summary per tournament run: counts, score distribution, champion, PBO when returns exist."""
    path = Path(path)
    rows = read(path)
    runs = {}
    for r in rows:
        run = runs.setdefault(r.get('run_id', '?'), {'trials': [], 'selections': [], 'criteria_changes': 0})
        kind = r.get('event_type')
        if kind == 'trial':
            run['trials'].append(r)
        elif kind == 'selection':
            run['selections'].append(r)
        elif kind == 'criteria_change_trial':
            run['criteria_changes'] += 1
    out = []
    for run_id, run in runs.items():
        trials = run['trials']
        scores = np.array([float(t['score_oos_net_t']) for t in trials if t.get('score_oos_net_t') is not None])
        stages = {}
        for t in trials:
            stages[t.get('stage', '?')] = stages.get(t.get('stage', '?'), 0) + 1
        best = max(trials, key=lambda t: t.get('score_oos_net_t', -np.inf), default=None)
        summary = {
            'source': str(path), 'run_id': run_id, 'n_trials': len(trials),
            # The contract counts governance changes toward the DSR trial number.
            'n_for_dsr': len(trials) + run['criteria_changes'],
            'criteria_changes': run['criteria_changes'], 'stages': stages,
            'score_oos_net_t': ({f'p{int(q * 100):02d}': float(np.quantile(scores, q)) for q in QUANTILES}
                                | {'mean': float(scores.mean()), 'max': float(scores.max()),
                                   'positive_share': float(np.mean(scores > 0))}) if len(scores) else None,
            'best_trial': {k: best.get(k) for k in ('trial_id', 'pool_id', 'stage', 'score_oos_net_t', 'scheme')} if best else None,
            'champion': run['selections'][-1] if run['selections'] else None,
            'pbo': None, 'pbo_note': 'trial_returns 不在本機，無法重建 month × trial 矩陣',
        }
        matrix = _returns_matrix(path.parent, trials)
        if matrix is not None and matrix.shape[1] >= 2 and len(matrix) >= 20:
            from strategies._common.validation.pbo import pbo
            summary['pbo'] = float(pbo(matrix.to_numpy(), n_splits=min(10, len(matrix) // 2 * 2)))
            summary['pbo_note'] = f'由 {matrix.shape[1]} 個 trial × {len(matrix)} 期 OOS 月報酬重建'
        out.append({'run_id': run_id, 'summary': summary,
                    'trials': [{'trial_id': t['trial_id'], 'stage': t.get('stage'), 'score': t.get('score_oos_net_t'),
                                'metrics': {k: t.get(k) for k in ('pool_id', 'scheme', 'leg', 'weighting', 'size_neutral',
                                                                  'frequency', 'min_train', 'is_ensemble')}}
                               for t in trials]})
    return out


def import_ledger(path, db_path):
    from strategies._common import results
    imported = []
    for run in summarize(path):
        experiment_id = f'mint:{run["run_id"]}'
        results.record_experiment(db_path, experiment_id=experiment_id, kind='mint_tournament',
                                  strategy=None, source=str(path), config={'contract': 'gs-MINT trial-ledger-fields'},
                                  summary=run['summary'], trials=run['trials'], backtest_key=None)
        imported.append(experiment_id)
    return imported


def main(argv=None):
    from strategies._common import results
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=['import', 'summary'])
    parser.add_argument('ledger', type=Path)
    parser.add_argument('--db', type=Path, default=results.default_path(Path.cwd()))
    args = parser.parse_args(argv)
    if args.command == 'summary':
        print(json.dumps([r['summary'] for r in summarize(args.ledger)], ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(import_ledger(args.ledger, args.db), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
