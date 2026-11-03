"""形式化驗證（FSM）唯讀檢視資料：atlas（strategy-fsm-atlas/1）＋策略自己的 fsm.ir.json。

只讀檔、不執行 TLC、不呼叫外部程式。atlas 由 genesis-deck 的主機端排程產生（gs-strategy 不依賴該 repo 的程式碼，
只依賴這份 JSON 的形狀）；沒有 atlas 時，退回只用 IR 畫出狀態圖與規則清單，所有規則標「未檢查」。
輸出一律經過欄位白名單：不帶主機路徑、提示詞、金鑰或 ledger。
"""
import json
import os
import re
from pathlib import Path

SCHEMA = 'strategy-fsm-atlas/1'
VIEW_SCHEMA = 'strategy-fsm-view/1'
MAX_BYTES = 8 * 1024 * 1024
DEFAULT_ATLAS = Path('data') / 'strategy-fsm' / 'strategy-fsm-atlas.json'
FIXTURE_ATLAS = Path(__file__).parent / 'fixtures' / 'fsm-atlas.fixture.json'
ID = re.compile(r'^[A-Za-z0-9_]{1,64}$')
DISCLAIMER = ('這裡檢查的是策略「規則的邏輯」，不是策略賺不賺錢。標示「有限模型下不變量未被違反」只代表：在下列步數與價格變動範圍內，'
              '被檢查的規則沒有被打破；不代表策略有效、程式沒有錯誤，也不代表超出這個範圍後仍然成立。')


def _s(v, n=4000):
    return v[:n] if isinstance(v, str) else None


def _b(v):
    return v if isinstance(v, bool) or v == 'unknown' else None


def _i(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _list(v, limit=500):
    return v[:limit] if isinstance(v, list) else []


def _dict(v):
    return v if isinstance(v, dict) else {}


def _cfg_name(v):
    # 只留檔名，避免主機路徑外洩。
    return os.path.basename(v.replace('\\', '/')) if isinstance(v, str) else None


def clean_target(t):
    g = _dict(t.get('graph'))
    invs = []
    for i in map(_dict, _list(t.get('invariants'))):
        d = _dict(i.get('detail'))
        cex = _dict(i.get('counterexample'))
        invs.append({
            'id': _s(i.get('id'), 128), 'label_zh': _s(i.get('label_zh')), 'origin': _s(i.get('origin'), 64),
            'confirm_status': _s(i.get('confirm_status'), 64), 'profile': _s(i.get('profile'), 64),
            'enforced_in_code': _b(i.get('enforced_in_code')), 'verdict': _s(i.get('verdict'), 64),
            'verdict_reason': _s(i.get('verdict_reason')), 'vacuity_note': _s(i.get('vacuity_note')),
            'cfg': _cfg_name(d.get('cfg')), 'distinct_states': _i(d.get('distinct_states')),
            'counterexample': {'cfg': _cfg_name(cex.get('cfg')), 'text_zh': _s(cex.get('text_zh'), 20000), 'trace_len': _i(cex.get('trace_len'))}
            if cex.get('text_zh') else None,
        })
    muts = [{'name': _s(m.get('name'), 128), 'cfg': _cfg_name(m.get('cfg')), 'description_zh': _s(m.get('description_zh')),
             'caught': m.get('caught') if isinstance(m.get('caught'), bool) else None, 'distinct_states': _i(m.get('distinct_states'))}
            for m in map(_dict, _list(t.get('mutations')))]
    check = _dict(t.get('check'))
    return {
        'id': _s(t.get('id'), 64), 'title_zh': _s(t.get('title_zh')), 'status': _s(t.get('status'), 64), 'status_reason': _s(t.get('status_reason')),
        'drift_status': _s(_dict(t.get('drift')).get('status'), 32),
        'graph': {
            'nodes': [{'id': _s(n.get('id'), 128), 'label_zh': _s(n.get('label_zh')), 'description_zh': _s(n.get('description_zh')),
                       'initial': bool(n.get('initial')), 'unreachable_by_config': bool(n.get('unreachable_by_config'))}
                      for n in map(_dict, _list(g.get('nodes')))],
            'edges': [{'id': _s(e.get('id'), 128), 'from': _s(e.get('from'), 128), 'to': _s(e.get('to'), 128), 'label_zh': _s(e.get('label_zh')),
                       'event_label_zh': _s(e.get('event_label_zh')), 'guard_zh': _s(e.get('guard_zh')),
                       'orders_zh': [x for x in map(_s, _list(e.get('orders_zh'), 20)) if x]}
                      for e in map(_dict, _list(g.get('edges'), 2000))],
        },
        'invariants': invs, 'mutations': muts,
        'model_numbers': [{'name': _s(m.get('name'), 128), 'value': m.get('value') if isinstance(m.get('value'), (int, float, str)) else None,
                           'unit': _s(m.get('unit'), 64), 'source': _s(m.get('source'))}
                          for m in map(_dict, _list(_dict(t.get('model')).get('numbers'), 100))],
        'refused': [{'code': _s(r.get('code'), 128), 'where': _s(r.get('where'), 256), 'message': _s(r.get('message'))}
                    for r in map(_dict, _list(check.get('refused'), 100))],
        'out_of_scope': [x for x in map(_s, _list(t.get('out_of_scope'), 100)) if x],
    }


def read_atlas(path):
    """回 (狀態, atlas)：ok／missing／bad。"""
    try:
        p = Path(path)
        if not p.exists():
            return 'missing', None
        if not p.is_file() or p.stat().st_size > MAX_BYTES:
            return 'bad', None
        data = json.loads(p.read_bytes().decode('utf-8'))
    except (OSError, ValueError, RecursionError):
        return 'bad', None
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or not isinstance(data.get('targets'), list):
        return 'bad', None
    return 'ok', data


def target_from_ir(ir):
    """沒有 atlas 時的退路：只畫狀態圖與規則清單，所有規則標未檢查。不做守衛白話化（不亂編）。"""
    events = {e.get('id'): e.get('label_zh') for e in map(_dict, _list(ir.get('events')))}
    edges = []
    for t in map(_dict, _list(ir.get('transitions'), 2000)):
        orders = []
        for o in map(_dict, _list(t.get('orders'), 20)):
            orders.append(('目標部位 ' + str(o.get('target')) if o.get('kind') == 'target_position' else str(o.get('kind')))
                          + (f"（{o['fills']} 筆成交）" if o.get('fills') else ''))
        for src in _list(t.get('from'), 50):
            edges.append({'id': t.get('id'), 'from': src, 'to': t.get('to'), 'label_zh': t.get('label_zh'),
                          'event_label_zh': events.get(t.get('on'), t.get('on')), 'guard_zh': None, 'orders_zh': orders})
    reason = '沒有取得模型檢查結果檔，這條規則沒有被檢查'
    t = {
        'id': ir.get('id'), 'title_zh': ir.get('title_zh'), 'status': 'unchecked',
        'status_reason': '只有狀態機描述（IR），沒有模型檢查結果；沒有任何規則被檢查',
        'graph': {'nodes': [{'id': s.get('id'), 'label_zh': s.get('label_zh'), 'description_zh': s.get('description_zh'),
                             'initial': s.get('id') == ir.get('initial_state'), 'unreachable_by_config': bool(s.get('unreachable_by_config'))}
                            for s in map(_dict, _list(ir.get('states')))], 'edges': edges},
        'invariants': [{'id': i.get('id'), 'label_zh': i.get('label_zh'), 'origin': i.get('origin'), 'confirm_status': i.get('status'),
                        'profile': i.get('tlc_profile'), 'enforced_in_code': i.get('enforced_in_code'), 'verdict': 'unchecked',
                        'verdict_reason': reason, 'vacuity_note': i.get('vacuity_note')} for i in map(_dict, _list(ir.get('invariants')))],
        'out_of_scope': _list(ir.get('out_of_scope'), 100),
    }
    return clean_target(t)


class FsmView:
    def __init__(self, source_root, atlas_path=None, fixture=False):
        self.source_root = Path(source_root).resolve()
        self.fixture = fixture
        if fixture:
            self.atlas_path = FIXTURE_ATLAS  # 測試資料模式不看操作者設定的路徑
        else:
            configured = atlas_path or os.environ.get('GS_FSM_ATLAS')
            self.atlas_path = Path(configured) if configured else self.source_root / DEFAULT_ATLAS

    def ir_target(self, strategy_id):
        if not strategy_id or not ID.match(strategy_id):
            return None
        strategies = self.source_root / 'strategies'
        p = (strategies / strategy_id / 'fsm.ir.json').resolve()
        if not p.is_relative_to(strategies) or not p.is_file() or p.stat().st_size > MAX_BYTES:
            return None
        try:
            return target_from_ir(json.loads(p.read_text(encoding='utf-8')))
        except (OSError, ValueError, RecursionError):
            return None

    def payload(self, strategy_id=None):
        status, atlas = read_atlas(self.atlas_path)
        targets = ([clean_target(t) for t in atlas['targets'] if isinstance(t, dict) and isinstance(t.get('id'), str)]
                   if status == 'ok' else [])
        if strategy_id and not any(t['id'] == strategy_id for t in targets):
            fallback = self.ir_target(strategy_id)
            if fallback:
                targets.append(fallback)
        return {
            'schema': VIEW_SCHEMA, 'source': 'fixture' if self.fixture else ('atlas' if status == 'ok' else 'ir'),
            'atlas_state': status, 'current': strategy_id,
            'generated_at': _s(atlas.get('generated_at'), 64) if atlas else None,
            'stale_days': atlas.get('stale_days') if atlas and isinstance(atlas.get('stale_days'), int) else None,
            'tlc_ran': bool(atlas and atlas.get('tlc_ran') is True),
            'tlc_note': (_s(atlas.get('tlc_note')) if atlas else None) or '',
            'disclaimer': DISCLAIMER, 'targets': targets,
        }
