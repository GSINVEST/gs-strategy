"""Shared CLI/HTTP lifecycle and JSON artifact serialization."""
from __future__ import annotations

import copy
import dataclasses
import json
import math
import os
import re
from pathlib import Path
import tempfile
import threading
import uuid

from . import core
from .core import Cancelled, Context, Engine, GraphError, Registry, digest
from .ledger import SelectionLedger
from .output_guard import quiet_worker_output
from strategies._common.validation.decision import UnderdeterminedError


GENERIC_MESSAGE = '操作失敗，請檢查設定後重試'


def user_message(message):
    """Translate controlled categories without echoing input or external text.

    Job messages join several node messages with '; ', and a not-ready node
    appends its upstream cause; each part is translated on its own.
    """
    whole = _missing_message(str(message)) or _user_message_one(message)
    if whole != GENERIC_MESSAGE or '; ' not in str(message):
        return whole
    parts = []
    for part in str(message).split('; '):
        text = _missing_message(part) or _user_message_one(part)
        if text not in parts:
            parts.append(text)
    return '；'.join(parts)


def _missing_message(part):
    missing = re.fullmatch(r'missing inputs: ([A-Za-z][A-Za-z0-9_]*(?:, [A-Za-z][A-Za-z0-9_]*)*)(?: \| upstream: (.*))?',
                           part, re.S)
    if not missing:
        return None
    text = '缺少輸入：' + missing[1]
    if missing[2]:
        causes = [_missing_message(c) or _user_message_one(c) for c in missing[2].split(' / ')]
        text += '（上游：' + '／'.join(dict.fromkeys(causes)) + '）'
    return text


def _user_message_one(message):
    message = str(message)
    edge = re.fullmatch(r'invalid edge at index (\d+)(?:: incompatible or unknown ports)?', message)
    if edge:
        return f'接線索引 {edge[1]} 無效：接點不相容或不存在'
    # Only fixed remedies are emitted; arbitrary exception text is never echoed.
    if 'Ljung-Box' in message:
        return '自相關尚未判定，請先執行 Ljung-Box 檢定並補足觀測資料；無法預設使用獨立樣本標準誤'
    if 'n_eff' in message:
        return '缺少有效獨立觀測數，請先處理重疊或群聚觀測，再計算有效樣本數'
    translations = {
        '圖已被其他分頁修改，請重新載入': '圖已被其他分頁修改，請重新載入',
        '執行預判已變更，請確認最新預判後再次執行': '執行預判已變更，請確認最新預判後再次執行',
        'execution active; cancel or wait before editing': '目前有工作正在執行，請取消或等待完成後再編輯',
        'bundle has no ingestions': '資料集沒有可用的匯入版本',
        'bundle has no ingestions; supply observations': '資料集沒有可用的匯入版本，請補足觀測資料',
        'pinned bundle ingestion is unavailable': '指定的資料匯入版本已無法使用',
        'load a graph first': '請先載入策略圖',
        'job is not running': '此工作目前未在執行',
        'unknown job': '找不到指定的工作',
        'unknown node': '找不到指定的節點',
        'unknown endpoint': '找不到指定的功能',
        'local Host required': '僅允許本機連線',
        'same origin required': '僅允許相同來源的請求',
        'application/json required': '請使用 JSON 格式提交',
        'JSON body required; maximum 2 MB': '請提交 JSON 內容，大小不得超過 2 MB',
        'invalid request or unavailable file': '請求格式無效或檔案無法使用',
        'execution cancelled': '執行已取消',
        'graph contains a cycle': '策略圖含有循環接線',
        'implementation changed during execution; retry': '執行期間程式碼已變更，請重新執行',
    }
    # Controlled point-in-time / data-identity messages are authored in Chinese
    # with only counts interpolated; they never carry external text.
    if re.fullmatch(r'模型知識截止後的乾淨樣本外天數不足（\d+ < \d+）', message):
        return message
    translations.update({
        'unknown model': '模型登錄表中沒有這個模型',
        'LLM API key unavailable; set the configured environment variable or key file': '找不到 LLM API 金鑰，請設定環境變數或金鑰檔',
        'pin data_version with prepare_graph before execution': '資料版本尚未釘選，請重新執行預判',
        'pin model_spec with prepare_graph before execution': '模型設定尚未釘選，請重新執行預判',
        'pin bundle_version with prepare_graph before execution': '策略版本尚未釘選，請重新執行預判',
    })
    llm_failure = re.fullmatch(r'LLM request failed \((HTTP \d{3}|[A-Za-z]+Error|[A-Za-z]+)\)', message)
    if llm_failure:
        return f'LLM 服務呼叫失敗（{llm_failure[1]}），請確認模型服務與網路'
    chinese = {'找不到 gs-zipline-tej 策略池（設定 GS_ZIPLINE_TEJ_ROOT）', '策略池程序逾時', '策略池程序執行失敗',
               '策略池中沒有這個策略', '找不到策略原始碼', '策略原始碼已變更，請重新執行預判',
               'gs-zipline-tej 回測失敗，請在該 repo 的 dashboard 檢視錯誤', '此策略 id 無法開啟',
               '測試資料模式只能檢視目前的圖，切換策略請用真實模式啟動',
               'QUANTDATA 在指定區間沒有資料', 'QUANTDATA 資料內容與釘選的版本不同，請重新執行預判',
               '找不到論文資料庫 papers.db', 'gs-rag 未安裝，無法使用 gs_rag 後端',
               '模型登錄表中此模型的設定已變更，請重新執行預判', 'Signals 與 PriceBars 的資料來源必須相同',
               'thresholds 必須是 0 到 1 之間的數字清單', '乾淨樣本不足以切出任何 walk-forward 區段'}
    if message in chinese:
        return message
    if message in translations.values():
        return message
    return translations.get(message, GENERIC_MESSAGE)


def default_registry():
    from strategies.tsmom_tx_mtx.graph_nodes import register_nodes as strategy_nodes
    from strategies.llm_view_tx.graph_nodes import register_nodes as llm_nodes
    from .stat_nodes import register_nodes as stat_nodes
    from .pool_nodes import register_nodes as pool_nodes
    from .mc_nodes import register_nodes as mc_nodes
    registry = Registry()
    strategy_nodes(registry)
    llm_nodes(registry)
    pool_nodes(registry)
    stat_nodes(registry)
    mc_nodes(registry)
    return registry


def prepare_any(graph):
    """Pin every data/model source the graph uses before identity is computed."""
    types = {n['type'] for n in graph['nodes']}
    if 'data.futures_bars' in types:
        from strategies.tsmom_tx_mtx.graph_nodes import prepare_graph
        graph = prepare_graph(graph)
    if types & {'data.quantdata_futures', 'agent.llm_view', 'pit.memorization_probe'}:
        from strategies.llm_view_tx.graph_nodes import prepare_graph
        graph = prepare_graph(graph)
    if 'data.pool_strategy' in types:
        from .pool_nodes import prepare_graph
        graph = prepare_graph(graph)
    return graph


def json_value(value, limit=60, offset=0):
    """Expose portable data only; omit internal executable/cache representations."""
    import numpy as np
    import pandas as pd
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if isinstance(value, (pd.Series, pd.DataFrame)):
        frame = value.to_frame() if isinstance(value, pd.Series) else value
        end = max(0, len(frame) - offset)
        sample = frame.iloc[max(0, end-limit):end]
        return {'index': [x.isoformat() if hasattr(x, 'isoformat') else str(x) for x in sample.index],
                'columns': list(map(str, sample.columns)),
                'data': json_value(sample.to_numpy().tolist(), limit, offset), 'total': len(frame)}
    if isinstance(value, dict):
        if 'values' in value and isinstance(value['values'], (pd.Series, pd.DataFrame)):
            return {'values': json_value(value['values'], limit, offset),
                    'source': json_value(value.get('source', {}), limit, offset)}
        return {str(k): json_value(v, limit, offset) for k,v in value.items()
                if k != 'histories' and not str(k).startswith('_')}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [json_value(v, limit, offset) for v in value]
    if isinstance(value, np.generic):
        return json_value(value.item(), limit, offset)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if hasattr(value, 'symbol'):
        return str(value.symbol)
    return None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(json_value(value), handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write('\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def restore_sidecar(document, registry):
    snapshot = document.get('graph_snapshot')
    if not isinstance(snapshot, dict) or digest(snapshot) != document.get('graph_hash'):
        raise GraphError('sidecar graph snapshot/hash mismatch')
    warnings = []
    for name, old in snapshot.get('fingerprints', {}).items():
        node = registry.types.get(name)
        if node is None or node.fingerprint != old:
            warnings.append('節點實作已變更，無法保證重現：' + name)
    return {'graph': copy.deepcopy(snapshot['graph']), 'graph_hash': document['graph_hash'], 'warnings': warnings}


def save_artifact(context, values, path):
    from strategies._common.validation import report
    from .stat_nodes import returns_frame
    output = next((v for v in values.values() if 'Returns' in v), None)
    if output is None:
        return None
    validation = next((v['Report'] for v in values.values() if 'Report' in v), None)
    if validation is None:
        try:
            validation = report.build_report(returns_frame(output['Returns']),
                n_trials=context.ledger.summary()['selection_n'], n_trials_source='ledger')
        except (ValueError, ArithmeticError):
            validation = {'schema': report.SCHEMA, 'warnings': ['統計資料不足，請補足觀測後再檢定。']}
    document = {**validation, 'graph_hash': context.graph_hash, 'graph_snapshot': context.snapshot,
                'backtest_key': context.backtest_key,
                'document_graph_hash': context.services.get('document_graph_hash')}
    write_json(path, document)
    return Path(path)


_UNSET = object()


class GraphConflict(GraphError):
    def __init__(self, code, revision):
        self.code, self.revision = code, revision
        super().__init__('圖已被其他分頁修改，請重新載入' if code == 'graph_revision_conflict' else '執行預判已變更，請確認最新預判後再次執行')


class GraphService:
    def __init__(self, root, *, registry=None, cache_dir=None, ledger_path=None):
        self.root = Path(root).resolve()
        self.registry = registry or default_registry()
        self.engine = Engine(self.registry, cache_dir or self.root / '.cache/live-strategy-graph')
        self.ledger = SelectionLedger(ledger_path or self.root / 'log/trials.jsonl')
        self.revision = 0
        self.graph = None
        self.path = None
        self.saved_hash = None
        self.jobs = {}
        self.active = None
        self.lock = threading.RLock()
        self.completed = {}
        self.fixture = False

    def session(self):
        return {**self._session(), 'strategy': (self.graph or {}).get('strategy')}

    def _session(self):
        with self.lock:
            active_job = self.job(self.active) if self.active and self.jobs[self.active]['status'] == 'running' else None
        result = {'fixture': self.fixture, 'active_job': active_job,
                  'label': 'FIXTURE 資料・獨立 ledger' if self.fixture else ''}
        if hasattr(self, 'initial_graph_path'):
            result['graph_path'] = self.initial_graph_path
        return result

    def run_estimate(self):
        with self.lock:
            if self.graph is None:
                raise GraphError('load a graph first')
            graph = copy.deepcopy(self.graph)
            document_hash = self.engine.identity(graph)[0]
            revision = self.revision
        graph = self.prepare(graph)
        return {'graph_hash': document_hash, 'revision': revision, **self.estimate_prepared(graph)}

    def prepare(self, graph):
        with quiet_worker_output():
            return prepare_any(graph)

    def strategies(self, refresh=False):
        from strategies._common import pool
        listing = pool.pool(refresh=refresh)
        current = (self.graph or {}).get('strategy')
        return {**listing, 'current': current, 'fixture': bool(getattr(self, 'fixture', False))}

    def select_strategy(self, strategy_id, *, expected_revision=_UNSET):
        """Open a pool strategy: its own graph when it ships one, else a coarse graph."""
        from strategies._common import pool
        if getattr(self, 'fixture', False):
            raise GraphError('測試資料模式只能檢視目前的圖，切換策略請用真實模式啟動')
        meta = pool.entry(strategy_id)
        if not meta.get('selectable'):
            raise GraphError('此策略 id 無法開啟')
        relative = meta.get('graph_path')
        if not relative:
            relative = f'strategies/_pool/{strategy_id}/graph.json'
            target = self.confined(relative)
            # Regenerate from the current template so new nodes appear, keeping
            # parameters the user saved on nodes that still exist.
            graph = pool.coarse_graph(meta)
            if target.exists():
                try:
                    saved = {(n['id'], n['type']): n.get('params', {})
                             for n in json.loads(target.read_text(encoding='utf-8')).get('nodes', [])}
                except (OSError, ValueError, KeyError, TypeError):
                    saved = {}
                for node in graph['nodes']:
                    kept = {k: v for k, v in saved.get((node['id'], node['type']), {}).items() if k != 'bundle_version'}
                    node['params'] = {**node['params'], **kept}
            write_json(target, graph)
        return self.load(relative, expected_revision=expected_revision)

    def experiments(self, strategy=None, limit=50):
        from strategies._common import results
        return {'experiments': results.experiments(results.default_path(self.root), strategy, limit)}

    def experiment(self, experiment_id):
        from strategies._common import results
        detail = results.experiment_detail(results.default_path(self.root), experiment_id)
        if detail is None:
            raise GraphError('unknown job')
        return json_value(detail, limit=100000)

    def results(self, strategy=None, limit=50):
        from strategies._common import results
        return {'runs': results.runs(results.default_path(self.root), strategy, limit)}

    def result(self, run_id):
        from strategies._common import results
        detail = results.run_detail(results.default_path(self.root), run_id)
        if detail is None:
            raise GraphError('unknown job')
        return json_value(detail, limit=100000)

    def replay(self):
        from strategies._common.compose import replay
        with self.lock:
            values = dict(self.engine.values)
        return json_value(replay.build(values), limit=100000)

    def estimate_prepared(self, graph):
        _, nodes, incoming, order = self.registry.normalize(graph)
        targets = [i for i in order if nodes[i]['type'].startswith('backtest.')]
        if len(targets) != 1:
            raise GraphError('run estimate requires one backtest')
        needed = set(targets)
        for i in reversed(order):
            if i in needed:
                needed.update(s for s, _ in incoming[i].values())
        hashes = {}
        for i in order:
            if i not in needed:
                continue
            node = nodes[i]
            kind = self.registry.types[node['type']]
            missing = set(kind.inputs) - set(incoming[i])
            if missing:
                raise GraphError(f'{i}: missing inputs: ' + ', '.join(sorted(missing)))
            if not kind.cacheable:
                raise GraphError(f'{i}: run estimate requires cacheable upstream nodes')
            hashes[i] = core.node_cache_key(kind, node['params'],
                {p: hashes[s] for p, (s, _) in incoming[i].items()})
        key = hashes[targets[0]]
        return {'backtest_key': key,
                **self.ledger.estimate(key)}

    def layout(self, positions=None):
        with self.lock:
            if self.graph is None or self.path is None:
                raise GraphError('load a graph with a path first')
            path = self.path.with_name('graph.layout.json').resolve()
            if not path.is_relative_to(self.root / 'strategies'):
                raise GraphError('layout path must be inside strategies')
            if positions is None:
                positions = json.loads(path.read_text(encoding='utf-8')).get('positions', {}) if path.exists() else {}
                saving = False
            else:
                saving = True
            ids = {n['id'] for n in self.graph['nodes']}
            if not isinstance(positions, dict) or set(positions) - ids:
                raise GraphError('layout contains unknown node')
            for point in positions.values():
                if not isinstance(point, dict) or set(point) != {'x', 'y'}:
                    raise GraphError('layout requires x and y')
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point.values()):
                    raise GraphError('layout coordinates must be finite numbers')
            result = {'schema': 'live-strategy-layout/1', 'positions': positions}
            if saving:
                write_json(path, result)
            return {**result, 'path': path.relative_to(self.root).as_posix()}

    def confined(self, relative, *, sidecar=False):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise GraphError('path must be inside workspace')
        if sidecar:
            if not path.name.endswith('.validation.json'):
                raise GraphError('expected validation sidecar JSON')
        elif path.name != 'graph.json' or not path.is_relative_to(self.root / 'strategies'):
            raise GraphError('expected strategies/<id>/graph.json')
        return path

    def idle(self):
        if self.active is not None and self.jobs[self.active]['status'] == 'running':
            raise GraphError('execution active; cancel or wait before editing')

    def check_revision(self, expected_revision):
        if expected_revision is not _UNSET and (type(expected_revision) is not int or expected_revision != self.revision):
            raise GraphConflict('graph_revision_conflict', self.revision)

    def set_graph(self, graph, path=None, *, saved=False, expected_revision=_UNSET):
        with self.lock:
            self.check_revision(expected_revision)
            self.idle()
            graph = self.registry.normalize(graph)[0]
            new_path = self.confined(path) if path is not None else self.path
            self.graph = graph
            self.revision += 1
            self.path = new_path
            self.engine.states = {n['id']: {'status': 'stale'} for n in graph['nodes']}
            self.engine.values = {}
            self.engine.hashes = {}
            if saved:
                self.saved_hash = self.engine.identity(graph)[0]
            return self.document()

    def load(self, relative, *, expected_revision=_UNSET):
        with self.lock:
            self.check_revision(expected_revision)
            path = self.confined(relative)
            graph = json.loads(path.read_text(encoding='utf-8-sig'))
            return self.set_graph(graph, relative, saved=True, expected_revision=expected_revision)

    def document(self):
        with self.lock:
            return self._document()

    def _document(self):
        if self.graph is None:
            return {'graph': None, 'dirty': False, 'revision': self.revision}
        key = self.engine.identity(self.graph)[0]
        return {'graph': copy.deepcopy(self.graph), 'graph_hash': key, 'revision': self.revision,
                'path': self.path.relative_to(self.root).as_posix() if self.path else None,
                'dirty': key != self.saved_hash}

    def save(self, relative=None, *, expected_revision=_UNSET):
        with self.lock:
            self.check_revision(expected_revision)
            self.idle()
            if self.graph is None:
                raise GraphError('load a graph first')
            path = self.confined(relative) if relative else self.path
            if path is None:
                raise GraphError('graph save path required')
            write_json(path, self.graph)
            self.path = path
            self.saved_hash = self.engine.identity(self.graph)[0]
            artifact = self.completed.get(self.saved_hash)
            if artifact is None:
                candidates = sorted((self.root / '.graph-runs').glob('*.validation.json'),
                                    key=lambda item: item.stat().st_mtime_ns, reverse=True)
                for candidate in candidates:
                    try:
                        document = json.loads(candidate.read_text(encoding='utf-8'))
                        if self.saved_hash not in (document.get('document_graph_hash'), document.get('graph_hash')):
                            continue
                        restored = restore_sidecar(document, self.registry)
                        if restored['warnings'] or not document.get('backtest_key'):
                            continue
                    except (ValueError, OSError):
                        continue
                    artifact = candidate
                    break
            if artifact is not None:
                from strategies._common.validation import report
                document = json.loads(artifact.read_text(encoding='utf-8'))
                report.apply_to_manifest(path.parent / 'manifest.yaml', document)
            self.revision += 1
            return self.document()

    def parameters(self, node_id, params, *, expected_revision=_UNSET):
        with self.lock:
            self.check_revision(expected_revision)
            self.idle()
            graph = copy.deepcopy(self.graph)
            if graph is None:
                raise GraphError('load a graph first')
            node = next((n for n in graph['nodes'] if n['id'] == node_id), None)
            if node is None:
                raise GraphError('unknown node')
            node['params'].update(params)
            graph = self.registry.normalize(graph)[0]
            self.engine.invalidate(graph, node_id)
            self.graph = graph
            self.revision += 1
            return self.document()

    def start(self, preview=False, *, expected_revision=_UNSET, expected_backtest_key=_UNSET, actor=None):
        with self.lock:
            self.check_revision(expected_revision)
            self.idle()
            if self.graph is None:
                raise GraphError('load a graph first')
            graph = copy.deepcopy(self.graph)
            prepared_run = None
            if not preview and expected_backtest_key is not _UNSET:
                if not isinstance(expected_backtest_key, str) or not expected_backtest_key:
                    raise GraphConflict('run_estimate_conflict', self.revision)
                prepared_run = self.prepare(graph)
                if expected_backtest_key != self.estimate_prepared(prepared_run)['backtest_key']:
                    raise GraphConflict('run_estimate_conflict', self.revision)
            job_id = uuid.uuid4().hex
            job = {'id': job_id, 'status': 'running', 'preview': preview, 'progress': {}}
            document_hash = self.engine.identity(graph)[0]
            def progress(value):
                job.update(progress=value)
                if self.fixture and value.get('phase') == 'backtest':
                    # Demonstration pacing remains cancellable before ledger commit.
                    import time
                    time.sleep(0.01)
                    ctx.check_cancelled()
            ctx = Context(ledger=self.ledger, progress=progress,
                          services={'root': self.root, 'document_graph_hash': document_hash, 'actor': actor})
            self.jobs[job_id] = job
            self.active = job_id
            self._token = ctx.token
            n_before = None if preview else self.ledger.summary()['selection_n']
            def record():
                if preview:
                    return
                try:
                    from strategies._common import results
                    n_after = self.ledger.summary()['selection_n']
                    results.record(results.default_path(self.root), run_id=job_id, status=job['status'],
                                   message=user_message(job['message']) if job.get('message') else None,
                                   context=ctx, values=dict(self.engine.values),
                                   states=dict(self.engine.states), selection_n=n_after,
                                   new_trial=n_after > n_before)
                except Exception:
                    # The store is a convenience; it must never fail or alter the job.
                    job['result_store'] = 'unavailable'
            def work():
                try:
                    if prepared_run is not None:
                        prepared = prepared_run
                    else:
                        prepared = prepare_any(graph)
                    values = self.engine.run(prepared, preview=preview, context=ctx)
                    inspected = {n['id'] for n in prepared['nodes'] if not preview or not n['type'].startswith(('backtest.', 'ledger.', 'validation.', 'stat.'))}
                    errors = [state for node_id, state in self.engine.states.items() if node_id in inspected and state['status'] in ('error','not_ready')]
                    if not preview and ctx.token.committed:
                        artifact = self.root / '.graph-runs' / (ctx.graph_hash + '.validation.json')
                        save_artifact(ctx, values, artifact)
                        self.completed[document_hash] = artifact
                        job['sidecar'] = artifact.relative_to(self.root).as_posix()
                        job['graph_hash'] = ctx.graph_hash
                        job['backtest_key'] = ctx.backtest_key
                    if errors:
                        job['message'] = '; '.join(dict.fromkeys(state['message'] for state in errors))
                    job['status'] = 'error' if errors else 'complete'
                except Cancelled:
                    job['status'] = 'cancelled'
                except (GraphError, UnderdeterminedError) as exc:
                    job['status'] = 'error'
                    job['message'] = str(exc)
                except Exception:
                    job['status'] = 'error'
                    job['message'] = 'execution failed; no external error text exposed'
                record()
            def quiet_work():
                # Third-party console output is confidential and never persisted.
                with quiet_worker_output():
                    work()
            thread = threading.Thread(target=quiet_work, name='live-graph-worker', daemon=True)
            self._thread = thread
            thread.start()
            return self.job(job_id)

    def job(self, job_id):
        with self.lock:
            result = {**copy.deepcopy(self.jobs[job_id]),
                      'node_states': {i: state['status'] for i, state in list(self.engine.states.items())}}
            if 'message' in result:
                result['message'] = user_message(result['message'])
            return result

    def cancel(self, job_id):
        with self.lock:
            if self.active != job_id or self.jobs[job_id]['status'] != 'running':
                raise GraphError('job is not running')
            accepted = self._token.cancel()
            return {'accepted': accepted, 'reason': '已要求取消執行' if accepted else '回測已提交，無法取消'}

    def node(self, node_id, limit=60, offset=0):
        if node_id not in self.engine.states:
            raise GraphError('unknown node')
        outputs = self.engine.values.get(node_id, {})
        result = {'id': node_id, **dict(self.engine.states[node_id]), 'outputs': json_value(outputs,limit,offset)}
        if 'message' in result:
            result['message'] = user_message(result['message'])
        if 'Returns' in outputs:
            from .stat_nodes import returns_frame
            equity = (1 + returns_frame(outputs['Returns'])['returns']).cumprod()
            result['equity'] = json_value(equity,limit,offset)
        return result
