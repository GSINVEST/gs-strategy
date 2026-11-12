"""Paper -> LLM-written strategy bundle -> gates -> strategy pool.

The template generator (``generate.py``) emits skeletons that mostly fall back
to buy-and-hold (see docs/spec/auto-research-funnel.md).  This module asks a
model from the registry (``strategies/_common/compose/models.yaml``) to write a
real ``strategy.py`` from the paper's own text, then admits it to the pool only
after three gates:

G0  static     gs-zipline-tej's own ``validate_strategy_py`` (entry points) plus an
               import/name allow-list: zipline.api, numpy, pandas, math only; no
               file, process, network or dynamic-code access.
G1  smoke      a short real Zipline run through gs-zipline-tej's runner must finish.
G2  behaviour  the run must trade more than once and must reduce or close a
               position at least once (not a buy-and-hold clone).  Only order
               activity is inspected, never returns, so screening adds no N.

Passing bundles land in ``strategies/_llm_generated/<id>/`` (gitignored, like
``_generated``), tagged ``llm-generated`` / ``unreviewed``; they appear in the
dashboard pool on the next refresh.  Every attempt, pass or fail, is recorded as
a ``codegen`` experiment in ``data/backtests.sqlite`` and is never retried.

    python -m quant_crawler.strategy_gen.llm_codegen --limit 3 [--model qwen3-235b-2507] [--dry-run]
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

import yaml

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / 'strategies' / '_llm_generated'
SMOKE_WINDOW = ('2023-01-03', '2023-06-30')
_SLUG = re.compile(r'^[a-z][a-z0-9_]{2,48}$')
ALLOWED_IMPORTS = {'zipline', 'zipline.api', 'numpy', 'pandas', 'math', '__future__'}
BANNED_NAMES = {'open', 'exec', 'eval', 'compile', '__import__', 'globals', 'locals', 'vars', 'getattr',
                'setattr', 'delattr', 'input', 'breakpoint', 'memoryview'}

SYSTEM = ('你是量化工程師，負責把學術論文的交易規則忠實轉成可在 gs-zipline-tej 執行的 Zipline 策略。'
          '只能使用論文中寫明的規則；論文沒寫清楚的參數要用保守預設值並在 rationale 說明。輸出必須是單一 JSON 物件。')

REFERENCE = '''from zipline.api import order_target_percent, record, symbol
import numpy as np

def initialize(context):
    p = context.params
    context.assets = [symbol(s) for s in p.get("symbols", ["2330"])]
    context.lookback = int(p.get("lookback", 60))
    context.i = 0

def handle_data(context, data):
    context.i += 1
    if context.i < context.lookback or context.i % 5:
        return
    for asset in context.assets:
        closes = data.history(asset, "price", context.lookback, "1d").to_numpy(dtype=float)
        if not np.all(np.isfinite(closes)):
            continue
        weight = 1.0 / len(context.assets) if closes[-1] > closes.mean() else 0.0
        order_target_percent(asset, weight)
    record(i=context.i)
'''


def _papers_db():
    return REPO / 'data' / 'papers.db'


def candidate_papers(db_path, attempted, limit):
    from quant_crawler.paper_class import classify_kind
    with sqlite3.connect(f'file:{Path(db_path).as_posix()}?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            'SELECT source, source_id, title, abstract, published, url, categories, keywords_hit FROM papers '
            'ORDER BY fetched_at DESC')]
    out = []
    for row in rows:
        key = f"{row['source']}:{row['source_id']}"
        if key in attempted or not (row.get('abstract') or '').strip():
            continue
        # classify_kind defaults to "strategy" with no evidence; require an explicit cue.
        kind = classify_kind(row)
        if kind.kind != 'strategy' or kind.strategy_score == 0:
            continue
        out.append({**row, 'key': key})
        if len(out) >= limit:
            break
    return out


def paper_text(paper, max_chars=6000):
    try:
        from quant_crawler.rag.retrieve import paper_context
        ctx = paper_context(paper['source'], paper['source_id'],
                            query='trading rule signal entry exit portfolio rebalance holding period', max_chunks=6)
        chunks = '\n\n'.join(c['text'] for c in ctx.get('chunks', []))
    except Exception:
        chunks = ''
    body = f"# {paper['title']}\n\n{paper['abstract']}\n\n{chunks}".strip()
    return body[:max_chars], bool(chunks)


_SYMBOLS = r'''
import json, sys
from dashboard.runner import list_bundle_symbols
print("\n__Y__" + json.dumps(list_bundle_symbols(sys.argv[1])))
'''


def bundle_symbols(bundle='tquant'):
    """Symbols actually ingested locally (gs-zipline-tej public helper); [] = unknown."""
    staging = Path(tempfile.mkdtemp(prefix='llm-codegen-syms-'))
    try:
        return sorted(_run_zipline(_SYMBOLS, '__Y__', [bundle], staging) or [])
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def symbol_gate(source, symbols, universe):
    """G0 part 2: every symbol the strategy names must exist in the local bundle."""
    if not universe:
        return []
    named = set(map(str, symbols or []))
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'symbol'
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            named.add(node.args[0].value)
    missing = sorted(named - set(universe))
    return [f'標的不在本機 bundle：{", ".join(missing)}'] if missing else []


def build_messages(paper, text, universe=()):
    pick = (f'- 標的只能從本機 bundle 實際有的這些代號挑選：{", ".join(universe)}。\n' if universe else '')
    user = f"""論文全文節錄（標題、摘要、與交易規則最相關的段落）：
<<<
{text}
>>>

請把論文的交易規則轉成台股（tquant bundle，日資料，標的以台股代號字串表示，例如 "2330"、"0050"）上可執行的 Zipline 策略。
限制：
- 只能 import：zipline.api、numpy、pandas、math；不可讀寫檔案、不可連網、不可用 eval/exec/open。
- 必須定義頂層 initialize(context) 與 handle_data(context, data)；參數一律從 context.params 讀（附預設值）。
- 用 order_target_percent 下單、record() 記錄關鍵訊號；要有出場或減碼邏輯，不能只是買進持有。
- 若論文是期貨或美股規則，請映射到最接近的台股標的並在 mapping 說明。
{pick}參考骨架（只示範介面，不要照抄邏輯）：
```python
{REFERENCE}```
只輸出 JSON：{{"id": "小寫英數底線、3–48 字", "name": "...", "description": "一段說明",
"symbols": ["..."], "params": {{...}}, "mapping": "論文規則 → 台股的映射說明", "rationale": "參數與簡化的理由",
"strategy_py": "完整的 strategy.py 原始碼"}}"""
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}]


def static_gate(source):
    """G0 part 1: our allow-list (the entry-point check is gs-zipline-tej's own validator)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f'語法錯誤：第 {exc.lineno} 行']
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            problems += [f'不允許 import {a.name}' for a in node.names if a.name.split('.')[0] not in
                         {m.split('.')[0] for m in ALLOWED_IMPORTS}]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or '') not in ALLOWED_IMPORTS:
                problems.append(f'不允許 from {node.module} import')
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            problems.append(f'不允許使用 {node.id}')
        elif isinstance(node, ast.Attribute) and node.attr.startswith('__'):
            problems.append(f'不允許存取 {node.attr}')
    return sorted(set(problems))


_VALIDATE = r'''
import json, sys
from dashboard.strategies.manual_import import validate_strategy_py, UploadError
try:
    validate_strategy_py(open(sys.argv[1], encoding="utf-8").read(), bundle_id=sys.argv[2])
    print("\n__V__" + json.dumps({"ok": True}))
except UploadError as exc:
    print("\n__V__" + json.dumps({"ok": False, "error": str(getattr(exc, "message", exc))[:300]}))
'''

_SMOKE = r'''
import json, sys
import pandas as pd
from dashboard.strategies import registry
from dashboard.runner import run_backtest
registry.refresh()
sid, start, end = sys.argv[1:4]
r = run_backtest(sid, start=start, end=end, timeout=600)
out = {"status": r.status, "stderr": (r.stderr or "")[-4000:], "trades": None, "reductions": None}
if r.status == "ok" and r.transactions_path:
    from pathlib import Path
    p = Path(r.transactions_path)
    t = pd.read_parquet(p if p.is_absolute() else Path.cwd() / p)
    out["trades"] = int(len(t))
    amount = next((c for c in ("amount", "qty", "quantity") if c in t.columns), None)
    out["reductions"] = int((t[amount] < 0).sum()) if amount else None
print("\n__S__" + json.dumps(out))
'''


def _run_zipline(script, marker, args, staging):
    import subprocess
    from strategies._common import pool
    env = pool._env()
    env['DASHBOARD_STRATEGY_DIRS'] = str(staging)
    proc = subprocess.run([str(pool.bt_python()), '-c', script, *args], cwd=pool.zipline_root(), env=env,
                          capture_output=True, text=True, timeout=900)
    _, sep, payload = proc.stdout.rpartition(marker)
    return json.loads(payload) if sep else None


def manifest_for(spec, paper, model):
    return {
        'id': spec['id'], 'name': str(spec.get('name') or spec['id'])[:80],
        'description': str(spec.get('description') or '')[:2000],
        'asset_class': 'equity', 'bundle': 'tquant', 'start': SMOKE_WINDOW[0], 'end': '2024-12-31',
        'capital_base': 1_000_000, 'params': {**(spec.get('params') or {}), 'symbols': list(spec.get('symbols') or [])},
        'symbols': list(spec.get('symbols') or []),
        'tags': ['llm-generated', 'unreviewed', 'paper'], 'requires_tej_key': True, 'extra_deps': [],
        'source': {'kind': 'generated', 'generator': 'gs-strategy/quant_crawler.strategy_gen.llm_codegen',
                   'generated_at': datetime.now(timezone.utc).isoformat(), 'model': model,
                   'inputs': {'paper': {'key': paper['key'], 'title': paper['title'], 'url': paper.get('url'),
                                        'published': paper.get('published')},
                              'mapping': str(spec.get('mapping') or '')[:2000],
                              'rationale': str(spec.get('rationale') or '')[:2000]}},
        'spec_version': 1,
    }


def attempt(paper, *, model, cache, dry_run=False, universe=()):
    """One paper through generation and the gates. Returns a trial record."""
    from strategies._common.compose import llm
    text, full = paper_text(paper)
    record = {'trial_id': paper['key'], 'stage': 'generate', 'score': 0.0,
              'metrics': {'title': paper['title'], 'fulltext_chunks': full, 'model': model}}
    reply = llm.chat(model, build_messages(paper, text, universe), cache=cache, temperature=0.0, max_tokens=4096)
    spec = llm.parse_json_object(reply['content']) or {}
    source = spec.get('strategy_py')
    sid = str(spec.get('id') or '').lower()
    if not isinstance(source, str) or not source.strip() or not _SLUG.match(sid):
        record['metrics']['reason'] = '模型輸出不是可用的 JSON（缺 id 或 strategy_py）'
        return record, None
    sid = 'llm_' + sid if not sid.startswith('llm_') else sid
    spec['id'] = sid[:56]
    record['metrics']['strategy_id'] = spec['id']
    record['stage'] = 'G0'
    problems = static_gate(source) or symbol_gate(source, spec.get('symbols'), universe)
    if problems:
        record['metrics']['reason'] = '；'.join(problems)[:500]
        return record, None
    staging = Path(tempfile.mkdtemp(prefix='llm-codegen-'))
    bundle = staging / spec['id']
    bundle.mkdir()
    (bundle / 'strategy.py').write_text(source, encoding='utf-8')
    manifest = manifest_for(spec, paper, model)
    (bundle / 'manifest.yaml').write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding='utf-8')
    (bundle / 'README.md').write_text(f"# {manifest['name']}\n\n論文：{paper['title']}\n\n{manifest['description']}\n\n"
                                      f"## 映射\n\n{manifest['source']['inputs']['mapping']}\n\n"
                                      f"## 參數理由\n\n{manifest['source']['inputs']['rationale']}\n", encoding='utf-8')
    try:
        verdict = _run_zipline(_VALIDATE, '__V__', [str(bundle / 'strategy.py'), spec['id']], staging)
        if not verdict or not verdict.get('ok'):
            record['metrics']['reason'] = 'gs-zipline-tej 驗證失敗：' + str((verdict or {}).get('error', '無回應'))
            return record, None
        if dry_run:
            record['stage'], record['metrics']['reason'] = 'G0', '乾跑：未執行 G1／G2'
            return record, None
        record['stage'] = 'G1'
        smoke = _run_zipline(_SMOKE, '__S__', [spec['id'], *SMOKE_WINDOW], staging)
        if not smoke or smoke['status'] != 'ok':
            # Only the exception class is kept: runner stderr may contain secrets.
            kind = re.findall(r'([A-Za-z_]+(?:Error|Exception|NotFound))\b', (smoke or {}).get('stderr', ''))
            record['metrics']['reason'] = '煙霧測試失敗' + (f'（{kind[-1]}）' if kind else '')
            return record, None
        record['stage'] = 'G2'
        record['metrics'].update(trades=smoke['trades'], reductions=smoke['reductions'])
        if not smoke['trades'] or smoke['trades'] < 2 or smoke['reductions'] == 0:
            record['metrics']['reason'] = '交易行為不足（少於 2 筆或從未減碼，疑似買進持有）'
            return record, None
        manifest['source']['gates'] = {'G0': 'pass', 'G1': f'pass {SMOKE_WINDOW[0]}~{SMOKE_WINDOW[1]}',
                                       'G2': f"trades={smoke['trades']} reductions={smoke['reductions']}"}
        (bundle / 'manifest.yaml').write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding='utf-8')
        target = OUT_DIR / spec['id']
        if target.exists():
            record['metrics']['reason'] = '策略池已有同名策略，未覆寫'
            return record, None
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundle, target)
        record['stage'], record['score'] = 'admitted', 1.0
        return record, target
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def attempted_keys(db_path):
    from strategies._common import results
    if not Path(db_path).is_file():
        return set()
    with results.connect(db_path) as conn:
        return {r[0] for r in conn.execute(
            "SELECT t.trial_id FROM trials t JOIN experiments e ON e.experiment_id = t.experiment_id "
            "WHERE e.kind = 'codegen'")}


def run(limit=3, model='qwen3-235b-2507', dry_run=False, papers_db=None, results_db=None):
    from strategies._common import results
    from strategies._common.compose import llm
    results_db = Path(results_db or results.default_path(REPO))
    papers = candidate_papers(papers_db or _papers_db(), attempted_keys(results_db), limit)
    cache = llm.CallCache(REPO / '.cache' / 'llm-calls')
    universe = bundle_symbols('tquant') if papers else []
    trials, admitted = [], []
    for paper in papers:
        try:
            record, target = attempt(paper, model=model, cache=cache, dry_run=dry_run, universe=universe)
        except Exception as exc:          # one bad paper never stops the batch
            record, target = {'trial_id': paper['key'], 'stage': 'error', 'score': 0.0,
                              'metrics': {'title': paper['title'], 'reason': type(exc).__name__}}, None
        trials.append(record)
        if target:
            admitted.append(target.name)
    batch = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    summary = {'model': model, 'papers': len(papers), 'admitted': admitted, 'dry_run': dry_run,
               'universe': len(universe),
               'by_stage': {s: sum(t['stage'] == s for t in trials) for s in {t['stage'] for t in trials}},
               'gates': 'G0 靜態＋gs-zipline-tej 驗證 / G1 煙霧回測 / G2 交易行為（不看報酬，ΔN=0）'}
    if trials and not dry_run:
        results.record_experiment(results_db, experiment_id=f'codegen:{batch}', kind='codegen', strategy=None,
                                  source='quant_crawler.strategy_gen.llm_codegen', config={'model': model, 'limit': limit},
                                  summary=summary, trials=trials, backtest_key=None)
    return summary, trials


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--model', default='qwen3-235b-2507')
    parser.add_argument('--dry-run', action='store_true', help='只跑到 G0，不寫入策略池與結果庫')
    args = parser.parse_args(argv)
    summary, trials = run(args.limit, args.model, args.dry_run)
    print(json.dumps({'summary': summary, 'trials': trials}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
