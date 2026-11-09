"""Agent 交易員分頁：唯讀渲染 agent-trader 的 UI 快照，外加唯一的寫入（核准／駁回草稿）。

資料契約：specfeed-test/agent-trader/docs/ui-contract.md（latest.json、days/<date>.json、drafts.json）。
本模組只讀那些 JSON；路徑由環境變數 AGENT_TRADER_UI_DIR 指定，未設或目錄不存在就顯示「尚無資料」。

安全原則
- 快照內容一律不信任：所有進入 HTML 的值都經 html.escape；覆盤 markdown 以純文字（<pre>）呈現，不渲染。
- 頁面腳本是外部檔 /agent-trader/app.js（CSP script-src 'self'），沒有 inline script／事件屬性。
- 核准是唯一寫入，預設關閉；要同時設定 AGENT_TRADER_APPROVE_CMD 與 AGENT_TRADER_IDENTITY_HEADER，
  且請求帶有效身分標頭才放行。--by 只取伺服端從該標頭讀到的身分，絕不採用請求本文的自報值。
  以 subprocess 參數陣列呼叫（無 shell），草稿 id、身分、備註都先驗證。
"""
from __future__ import annotations

import html
import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

ENV_UI_DIR = 'AGENT_TRADER_UI_DIR'
ENV_APPROVE_CMD = 'AGENT_TRADER_APPROVE_CMD'
ENV_APPROVE_ARGS = 'AGENT_TRADER_APPROVE_EXTRA_ARGS'
ENV_IDENTITY_HEADER = 'AGENT_TRADER_IDENTITY_HEADER'

SCREENS = [
    ('overview', '總覽'),
    ('positions', '今日持倉'),
    ('decisions', '決策日誌'),
    ('reasoning', '推論鏈'),
    ('review', '覆盤報告'),
    ('drafts', '待核准草稿'),
]
MAX_JSON_BYTES = 20_000_000
MAX_DAY_FILES = 120
DEFAULT_STALE_HOURS = 36.0
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
DRAFT_ID_RE = re.compile(r'^[a-z][a-z0-9_]{0,31}-[0-9a-f]{6,40}$')
ACTOR_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.@+\-]{0,99}$')
HEADER_NAME_RE = re.compile(r'^[A-Za-z0-9\-]{1,64}$')
ACTIONS = ('approve', 'reject')
NOTE_MAX = 500
DECIDE_TIMEOUT = 30
TAIPEI = timezone(timedelta(hours=8))

e = lambda v: html.escape('' if v is None else str(v), quote=True)  # noqa: E731


# ---------------------------------------------------------------- 讀檔
def ui_dir(env: Mapping[str, str]) -> Path | None:
    v = (env.get(ENV_UI_DIR) or '').strip()
    return Path(v) if v else None


def read_json(path: Path) -> tuple[dict | None, str | None]:
    """回傳 (資料, 錯誤碼)。錯誤碼：missing | too_large | invalid。絕不丟例外。"""
    try:
        if not path.is_file():
            return None, 'missing'
        if path.stat().st_size > MAX_JSON_BYTES:
            return None, 'too_large'
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, 'invalid'
    return (data, None) if isinstance(data, dict) else (None, 'invalid')


def list_days(root: Path) -> list[str]:
    """days/ 下合法日期檔名，新到舊。"""
    try:
        names = [p.stem for p in (root / 'days').iterdir()
                 if p.suffix == '.json' and DATE_RE.fullmatch(p.stem)]
    except OSError:
        return []
    return sorted(names, reverse=True)[:MAX_DAY_FILES]


def _parse_ts(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=TAIPEI)


def stale_reasons(doc: Mapping, now: datetime) -> list[str]:
    """產生端原因 + 消費端 AGE_EXCEEDED（契約 §1.1）。"""
    reasons = [str(r) for r in (doc.get('stale_reasons') or []) if isinstance(r, str)]
    if doc.get('stale') is True and not reasons:
        reasons.append('STALE')
    hours = doc.get('stale_after_hours')
    hours = float(hours) if isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0 \
        else DEFAULT_STALE_HOURS
    gen = _parse_ts(doc.get('generated_at'))
    if gen is None:
        reasons.append('GENERATED_AT_UNREADABLE')
    elif now - gen > timedelta(hours=hours):
        reasons.append('AGE_EXCEEDED')
    return list(dict.fromkeys(reasons))


# ---------------------------------------------------------------- 小工具
def num(v: Any, nd: int = 4) -> str:
    if v is None:
        return '—'
    if isinstance(v, bool):
        return e(v)
    if isinstance(v, float):
        return e(f'{v:.{nd}f}')
    return e(v)


def pct(v: Any) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return e(f'{v * 100:.2f}%')
    return '—'


def txt(v: Any) -> str:
    return '—' if v is None else e(v)


def table(headers: list[str], rows: list[list[str]], cls: str = '') -> str:
    """rows 的儲存格必須是已跳脫的 HTML。"""
    if not rows:
        return '<p class="muted">（無資料）</p>'
    head = ''.join(f'<th>{e(h)}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>' for r in rows)
    return f'<div class="tw"><table class="{e(cls)}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def generic(v: Any, depth: int = 0) -> str:
    """把任意 JSON 值畫成安全的巢狀 HTML（計分卡等欄位用）。"""
    if depth > 5:
        return '<span class="muted">…</span>'
    if isinstance(v, dict):
        if not v:
            return '<span class="muted">—</span>'
        return '<dl>' + ''.join(f'<dt>{e(k)}</dt><dd>{generic(x, depth + 1)}</dd>' for k, x in list(v.items())[:200]) + '</dl>'
    if isinstance(v, list):
        if not v:
            return '<span class="muted">—</span>'
        if all(isinstance(x, dict) for x in v):
            keys = list(dict.fromkeys(k for x in v[:200] for k in x))[:20]
            return table(keys, [[generic(x.get(k), depth + 1) for k in keys] for x in v[:200]])
        return '<ul>' + ''.join(f'<li>{generic(x, depth + 1)}</li>' for x in v[:200]) + '</ul>'
    if isinstance(v, float):
        return num(v)
    return txt(v)


def badge(text: Any, kind: str = '') -> str:
    return f'<span class="badge {e(kind)}">{e(text)}</span>'


STATUS_KIND = {'valid': 'ok', 'void_format': 'bad', 'void_limit': 'bad', 'no_decision': 'warn'}


def warnings_html(doc: Mapping) -> str:
    items = [w for w in (doc.get('warnings') or []) if isinstance(w, dict)]
    if not items:
        return ''
    return '<ul class="warn-list">' + ''.join(
        f'<li>{badge(w.get("code"), "warn")} {e(w.get("section"))}：{txt(w.get("message"))}</li>' for w in items[:100]) + '</ul>'


def date_nav(screen: str, days: list[str], current: str | None) -> str:
    if not days:
        return ''
    opts = ''.join(
        f'<a class="{"on" if d == current else ""}" href="/agent-trader?screen={e(screen)}&amp;date={e(d)}">{e(d)}</a>'
        for d in days[:30])
    return f'<nav class="dates">日期：{opts}</nav>'


# ---------------------------------------------------------------- 畫面
SEG_COLORS = 8


def chart(overview: Mapping) -> str:
    series = [p for p in (overview.get('series') or []) if isinstance(p, dict)]
    keys = [('nav_level', 'l-nav', 'NAV'), ('index_level', 'l-idx', '指數'), ('counterfactual_level', 'l-cf', '不動作反事實')]
    vals = [p.get(k) for p in series for k, _, _ in keys
            if isinstance(p.get(k), (int, float)) and not isinstance(p.get(k), bool)]
    if len(series) < 2 or not vals:
        return '<p class="muted">序列資料不足，無法畫圖。</p>'
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi, lo = hi + 0.01, lo - 0.01
    w, h, pad, band = 760, 240, 34, 14
    n = len(series)
    x = lambda i: pad + (w - 2 * pad) * i / (n - 1)  # noqa: E731
    y = lambda v: pad / 2 + (h - band - pad) * (1 - (v - lo) / (hi - lo))  # noqa: E731
    versions: list[str] = []
    for p in series:
        v = str(p.get('agent_version') if p.get('agent_version') is not None else '(none)')
        if v not in versions:
            versions.append(v)
    parts = []
    # 版本分段色帶
    for i, p in enumerate(series):
        v = str(p.get('agent_version') if p.get('agent_version') is not None else '(none)')
        x0 = x(i) - (x(1) - x(0)) / 2
        wd = x(1) - x(0)
        parts.append(f'<rect class="seg seg{versions.index(v) % SEG_COLORS}" x="{max(x0, pad):.1f}" y="{h - band}" '
                     f'width="{wd:.1f}" height="{band}"><title>{e(p.get("date"))} {e(v)}</title></rect>')
    parts.append(f'<text class="ax" x="2" y="{y(hi) + 4:.1f}">{e(f"{hi:.3f}")}</text>')
    parts.append(f'<text class="ax" x="2" y="{y(lo) + 4:.1f}">{e(f"{lo:.3f}")}</text>')
    for k, cls, _ in keys:
        d, pen = [], False
        for i, p in enumerate(series):
            v = p.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                d.append(f'{"L" if pen else "M"}{x(i):.1f},{y(v):.1f}')
                pen = True
            else:
                pen = False
        if d:
            parts.append(f'<path class="line {cls}" d="{" ".join(d)}"/>')
    legend = ''.join(f'<span class="lg {cls}">{e(label)}</span>' for _, cls, label in keys)
    vleg = ''.join(f'<span class="lgv seg{i % SEG_COLORS}">{e(v)}</span>' for i, v in enumerate(versions))
    first, last = series[0].get('date'), series[-1].get('date')
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="NAV 與指數與反事實">{"".join(parts)}</svg>'
            f'<div class="legend">{legend}</div><div class="legend">版本分段：{vleg}</div>'
            f'<p class="muted">{e(first)} → {e(last)}（起點 = 1.0）</p>')


def segments_table(segs: Any) -> str:
    rows = []
    for s in segs or []:
        if not isinstance(s, dict):
            continue
        rows.append([txt(s.get('agent_version')), txt(s.get('first_date')), txt(s.get('last_date')), txt(s.get('days')),
                     pct(s.get('cumulative_return')), pct(s.get('vs_index')), pct(s.get('vs_counterfactual')),
                     badge('樣本不足', 'warn') if s.get('insufficient') else '', txt(s.get('caveat'))])
    return table(['版本', '起', '迄', '天數', '累計報酬', '相對指數', '相對反事實', '', '備註'], rows)


def sample_notice(ov: Mapping) -> str:
    days = ov.get('days')
    parts = [f'樣本天數：<b>{num(days)}</b>']
    if ov.get('insufficient'):
        parts.append(badge('統計上尚不足', 'warn'))
    out = f'<div class="callout warn">{" ".join(parts)}'
    for k in ('caveat', 'stats_note'):
        if ov.get(k):
            out += f'<div>{e(ov[k])}</div>'
    if not ov.get('insufficient') and days is not None:
        out += '<div>仍須注意：樣本天數少時，任何績效差異都不構成統計證據。</div>'
    return out + '</div>'


def screen_overview(latest: Mapping | None, **_: Any) -> str:
    ov = (latest or {}).get('overview')
    if not isinstance(ov, dict):
        return '<p class="muted">latest.json 沒有 overview 區塊（尚無計分卡與估值）。</p>'
    ln = ov.get('latest_nav') if isinstance(ov.get('latest_nav'), dict) else {}
    biased = badge('NAV 有缺價，僅供參考', 'warn') if ln.get('nav_biased') else ''
    head = (f'<div class="cards"><div class="card"><span>最新 NAV</span><b>{num(ln.get("nav"), 2)}</b>'
            f'<small>{txt(ln.get("date"))} {biased}</small></div>'
            f'<div class="card"><span>累計報酬</span><b>{pct(ov.get("total_return"))}</b></div>'
            f'<div class="card"><span>樣本天數</span><b>{num(ov.get("days"))}</b></div></div>')
    alerts = ((latest or {}).get('alerts') or {}).get('items') if isinstance((latest or {}).get('alerts'), dict) else None
    alert_html = ''
    if alerts:
        alert_html = '<h3>近期告警</h3>' + table(['日期', '代碼', '等級', '訊息'], [
            [txt(a.get('date')), txt(a.get('code')), badge(a.get('severity'), 'bad' if a.get('severity') == 'critical' else 'warn'),
             txt(a.get('message'))] for a in alerts[:20] if isinstance(a, dict)])
    return (head + sample_notice(ov) + '<h3>淨值 vs 指數 vs 不動作反事實</h3>' + chart(ov)
            + '<h3>按版本分段</h3>' + segments_table(ov.get('segments')) + alert_html + warnings_html(latest or {}))


def screen_positions(latest: Mapping | None, **_: Any) -> str:
    po = (latest or {}).get('positions')
    if not isinstance(po, dict):
        return '<p class="muted">latest.json 沒有 positions 區塊（沒有帳本或重播失敗）。</p>'
    val = po.get('valuation') if isinstance(po.get('valuation'), dict) else None
    cards = (f'<div class="cards"><div class="card"><span>持倉檔數</span><b>{num(po.get("count"))} / {num(po.get("max_positions"))}</b></div>'
             f'<div class="card"><span>空位</span><b>{num(po.get("open_slots"))}</b></div>'
             f'<div class="card"><span>現金</span><b>{num(po.get("cash"), 0)}</b></div></div>')
    note = ''
    if val:
        note = f'<p class="muted">估值日 {txt(val.get("date"))}・NAV {num(val.get("nav"), 2)}'
        if val.get('nav_biased'):
            note += ' ' + badge('NAV 有缺價，僅供參考', 'warn')
        if val.get('stale_symbols'):
            note += f'・缺價代號：{e(", ".join(map(str, val["stale_symbols"])))}'
        note += '</p>'
    rows = []
    for p in po.get('positions') or []:
        if not isinstance(p, dict):
            continue
        pnl = p.get('unrealized_pnl')
        cls = 'pos' if isinstance(pnl, (int, float)) and pnl > 0 else 'neg' if isinstance(pnl, (int, float)) and pnl < 0 else ''
        rows.append([txt(p.get('symbol')), num(p.get('shares'), 0), num(p.get('avg_cost'), 2), num(p.get('last_price'), 2),
                     num(p.get('market_value'), 0), f'<span class="{cls}">{num(pnl, 0)} ({pct(p.get("unrealized_pnl_pct"))})</span>',
                     num(p.get('stop_price'), 2), num(p.get('target_price'), 2), txt(p.get('planned_hold_days')),
                     txt(p.get('opened_date')), txt(p.get('held_calendar_days'))])
    price_gap = ''
    if any(isinstance(p, dict) and p.get('last_price') is None for p in po.get('positions') or []):
        price_gap = '<p class="muted">部分持倉缺價（帳本不存逐檔價格；沒被計分卡追蹤者為「—」，不是 0）。</p>'
    return cards + note + table(['代號', '股數', '均價', '最新價', '市值', '未實現損益', '停損', '目標', '計畫天數', '建倉日', '持有日'], rows) \
        + price_gap + warnings_html(latest or {})


def screen_decisions(days: list[str], root: Path | None, **_: Any) -> str:
    if root is None or not days:
        return '<p class="muted">尚無資料：days/ 下沒有逐日檔。</p>'
    rows = []
    for d in days[:60]:
        doc, _err = read_json(root / 'days' / f'{d}.json')
        dec = (doc or {}).get('decision')
        if not isinstance(dec, dict):
            rows.append([txt(d), badge('無決策資料', 'warn'), '', '', '', ''])
            continue
        c = dec.get('action_counts') if isinstance(dec.get('action_counts'), dict) else {}
        flag = badge('引用有問題', 'bad') if dec.get('has_citation_issues') else ''
        rows.append([f'<a href="/agent-trader?screen=reasoning&amp;date={e(d)}">{e(d)}</a>',
                     badge(dec.get('status'), STATUS_KIND.get(dec.get('status'), '')),
                     txt(dec.get('agent_version')),
                     f'買 {num(c.get("buy"), 0)}・賣 {num(c.get("sell"), 0)}・持 {num(c.get("hold"), 0)}',
                     flag, txt(dec.get('detail'))])
    return table(['日期', '狀態', '版本', '動作', '', '說明'], rows) \
        + '<p class="muted">狀態：valid 有效／void_format 格式違規作廢／void_limit 超限作廢／no_decision 無決策單。</p>'


CITE = {True: ('ok', '有效'), False: ('bad', '失效'), None: ('na', '無法驗證')}


def screen_reasoning(day_doc: Mapping | None, **_: Any) -> str:
    rs = (day_doc or {}).get('reasoning')
    if not isinstance(rs, dict):
        return '<p class="muted">該日沒有推論鏈資料（沒有決策鎖）。</p>'
    out = [f'<p>{txt(rs.get("date"))}・{badge(rs.get("status"), STATUS_KIND.get(rs.get("status"), ""))}・版本 {txt(rs.get("agent_version"))}'
           f'・lock <code>{e(str(rs.get("lock_hash") or "")[:12])}</code></p>']
    theses = rs.get('theses')
    if theses is None:
        out.append('<p class="muted">此日沒有論點（void_format／no_decision）。</p>')
    for t in theses or []:
        if not isinstance(t, dict):
            continue
        valid = t.get('valid')
        cites = []
        for c in t.get('citations') or []:
            if not isinstance(c, dict):
                continue
            ok = c.get('citation_ok')
            kind, label = CITE.get(ok if isinstance(ok, bool) else None)
            reason = f'（{e(c.get("reason"))}）' if c.get('reason') else ''
            cites.append(f'<li class="cite {kind}">{badge(label, kind)} [{txt(c.get("index"))}] {txt(c.get("source_type"))} / '
                         f'{txt(c.get("source_id"))} ・ {txt(c.get("published_at"))}{reason}</li>')
        used = '、'.join(f'#{e(u.get("idx"))} {e(u.get("symbol"))} {e(u.get("kind"))}' for u in (t.get('used_by') or []) if isinstance(u, dict))
        issues = f'<div class="neg">鎖定時記錄的問題：{e(", ".join(map(str, t["recorded_issues"])))}</div>' if t.get('recorded_issues') else ''
        out.append(f'<div class="thesis {"" if valid is not False else "invalid"}"><h4>論點 {txt(t.get("id"))} '
                   f'{badge("有效" if valid else "無效" if valid is False else "未知", "ok" if valid else "bad" if valid is False else "na")} '
                   f'<small>{txt(t.get("direction"))}・{txt(t.get("theory_type"))}・權重 {num(t.get("weight"))}</small></h4>'
                   f'<p class="body">{txt(t.get("content"))}</p>{issues}'
                   f'<ul class="cites">{"".join(cites) or "<li class=muted>（無引用）</li>"}</ul>'
                   f'<small class="muted">被引用於：{used or "—"}</small></div>')
    q = rs.get('queries')
    if isinstance(q, dict):
        out.append(f'<p class="muted">查詢紀錄：{num(q.get("count"), 0)} 次・chain_ok {txt(q.get("chain_ok"))}・'
                   f'{e(json.dumps(q.get("by_tool"), ensure_ascii=False)) if q.get("by_tool") else ""}</p>')
    else:
        out.append('<p class="muted">當日查詢紀錄不存在（引用無法驗證）。</p>')
    if rs.get('agent_reply_tail'):
        out.append(f'<h3>agent 最後回覆（末段，純文字）</h3><pre>{e(rs["agent_reply_tail"])}</pre>')
    return ''.join(out)


def screen_review(day_doc: Mapping | None, latest: Mapping | None, **_: Any) -> str:
    rv = (day_doc or {}).get('review')
    if not isinstance(rv, dict):
        return '<p class="muted">該日沒有覆盤資料。</p>'
    out = [f'<p>{txt(rv.get("date"))}・覆盤狀態 {badge(rv.get("review_status"), {"done": "ok", "failed": "bad"}.get(rv.get("review_status"), "warn"))}'
           f'・版本 {txt(rv.get("agent_version"))}・決策 {txt(rv.get("lock_status"))}</p>']
    if rv.get('stats_note'):
        out.append(f'<div class="callout warn">{e(rv["stats_note"])}</div>')
    sc = rv.get('scorecard')
    if isinstance(sc, dict):
        for key, title in (('performance', '績效'), ('calibration', '校準桶'), ('attribution', '歸因（v1 簡化版）')):
            out.append(f'<h3>{e(title)}</h3>{generic(sc.get(key))}')
        for key, title in (('actions', '動作計分'), ('tracked_buys', '追蹤中的買進'), ('thesis_issues', '論點問題'), ('warnings', '警告')):
            if sc.get(key):
                out.append(f'<h3>{e(title)}</h3>{generic(sc.get(key))}')
    else:
        out.append('<p class="muted">沒有計分卡。</p>')
    rp = rv.get('report')
    if isinstance(rp, dict):
        if rp.get('report_md'):
            out.append(f'<h3>LLM 覆盤報告（純文字）</h3><pre>{e(rp["report_md"])}</pre>')
        if rp.get('citation_failures'):
            out.append(f'<h3>引用失敗</h3>{generic(rp["citation_failures"])}')
        lessons = [x for x in rp.get('lessons') or [] if isinstance(x, dict)]
        if lessons:
            rows = []
            for ls in lessons:
                tr = ls.get('tracking')
                rows.append([generic({k: v for k, v in ls.items() if k != 'tracking'}),
                             generic(tr) if tr else '<span class="muted">未追蹤</span>'])
            out.append('<h3>教訓清單</h3>' + table(['教訓', '追蹤狀態'], rows))
    ov = (latest or {}).get('overview')
    if isinstance(ov, dict) and ov.get('segments'):
        out.append('<h3>績效（按版本）</h3>' + segments_table(ov['segments']))
    return ''.join(out)


def screen_drafts(drafts_doc: Mapping | None, approval: Mapping, **_: Any) -> str:
    dr = (drafts_doc or {}).get('drafts')
    if not isinstance(dr, dict):
        return '<p class="muted">drafts.json 沒有 drafts 區塊（沒有草稿目錄）。</p>'
    items = [d for d in dr.get('items') or [] if isinstance(d, dict)]
    banner = ''
    if approval.get('enabled'):
        banner = f'<p class="muted">核准身分（伺服端由登入身分決定）：<b>{e(approval.get("actor"))}</b></p>'
    else:
        banner = f'<div class="callout">核准／駁回功能未啟用：{e(approval.get("reason"))}</div>'
    out = [banner, f'<p>待核准：<b>{num(dr.get("pending_count"), 0)}</b> 筆</p>']
    for d in items:
        diff = d.get('diff') if isinstance(d.get('diff'), dict) else {}
        base = diff.get('baseline_text')
        status = d.get('status')
        decision = d.get('decision')
        dec = f'<p class="muted">決定：{generic(decision)}</p>' if isinstance(decision, dict) else ''
        buttons = ''
        did = d.get('id')
        if status == 'pending_approval' and approval.get('enabled') and isinstance(did, str) and DRAFT_ID_RE.fullmatch(did):
            buttons = (f'<div class="actions"><input class="note" maxlength="{NOTE_MAX}" placeholder="備註（選填）">'
                       f'<button class="decide-btn ok" data-id="{e(did)}" data-action="approve">核准</button> '
                       f'<button class="decide-btn bad" data-id="{e(did)}" data-action="reject">駁回</button>'
                       f'<span class="result" role="status"></span></div>')
        hist = generic(d.get('history')) if d.get('history') else ''
        out.append(
            f'<div class="draft"><h4>{txt(d.get("topic"))} <small>{txt(d.get("kind"))}・<code>{txt(did)}</code></small> '
            f'{badge(status, "warn" if status == "pending_approval" else "")}</h4>'
            f'<p class="muted">證據日期（{num(d.get("evidence_days"), 0)} / 至少 {num(d.get("min_days"), 0)} 日，還差 {num(d.get("remaining_days"), 0)} 日）：'
            f'{e(", ".join(map(str, d.get("evidence_dates") or [])) or "—")}</p>'
            f'<div class="callout warn">{e(d.get("stats_note") or "樣本天數少，統計上不足以證明任何事。")}</div>'
            f'<div class="diff"><div><h5>修改前{" —" if not base else ""} <small>{txt(diff.get("baseline_note"))}</small></h5>'
            f'<pre>{e(base) if base else "（無基準文字）"}</pre></div>'
            f'<div><h5>提案</h5><pre>{e(diff.get("proposed_text"))}</pre></div></div>'
            f'{dec}{buttons}{("<details><summary>歷程</summary>" + hist + "</details>") if hist else ""}</div>')
    if not items:
        out.append('<p class="muted">目前沒有草稿。</p>')
    return ''.join(out) + warnings_html(drafts_doc or {})


# ---------------------------------------------------------------- 核准（唯一寫入）
def identity_header_name(env: Mapping[str, str]) -> str | None:
    name = (env.get(ENV_IDENTITY_HEADER) or '').strip()
    return name if HEADER_NAME_RE.fullmatch(name) else None


def resolve_actor(headers: Any, env: Mapping[str, str]) -> str | None:
    """只信任環境變數指名的標頭（應由 OIDC 反向代理注入並剝除客戶端自帶值）；格式不合法就視為沒有身分。"""
    name = identity_header_name(env)
    if not name or headers is None:
        return None
    raw = headers.get(name)
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    return raw if ACTOR_RE.fullmatch(raw) else None


def approve_prefix(env: Mapping[str, str]) -> list[str] | None:
    raw = (env.get(ENV_APPROVE_CMD) or '').strip()
    if not raw:
        return None
    try:
        argv = shlex.split(raw)
    except ValueError:
        return None
    return argv or None


def approval_state(headers: Any, env: Mapping[str, str]) -> dict:
    if approve_prefix(env) is None:
        return {'enabled': False, 'reason': f'未設定 {ENV_APPROVE_CMD}（預設關閉）'}
    if identity_header_name(env) is None:
        return {'enabled': False, 'reason': f'未設定 {ENV_IDENTITY_HEADER}，無身分機制'}
    actor = resolve_actor(headers, env)
    if actor is None:
        return {'enabled': False, 'reason': '此請求沒有可用的已驗證身分'}
    return {'enabled': True, 'actor': actor}


DENIED = {
    'NO_APPROVERS_CONFIGURED': (403, '核准人名單為空，一律拒絕'),
    'NOT_AUTHORIZED': (403, '你不在核准人名單內'),
    'UNKNOWN_DRAFT': (404, '找不到這份草稿'),
    'BAD_STATE': (409, '草稿不是待核准狀態'),
    'BAD_ACTION': (400, '不支援的動作'),
    'BAD_INPUT': (400, '輸入不合法'),
}


def _fail(status: int, code: str, message: str) -> tuple[int, dict]:
    return status, {'ok': False, 'code': code, 'message': message}


def handle_decide(body: Mapping, headers: Any, env: Mapping[str, str] = os.environ,
                  runner: Callable[..., Any] = subprocess.run) -> tuple[int, dict]:
    prefix = approve_prefix(env)
    if prefix is None:
        return _fail(503, 'APPROVAL_DISABLED', f'核准功能未啟用（未設定 {ENV_APPROVE_CMD}）')
    if identity_header_name(env) is None:
        return _fail(503, 'APPROVAL_DISABLED', f'核准功能未啟用（未設定 {ENV_IDENTITY_HEADER}，無身分機制）')
    actor = resolve_actor(headers, env)
    if actor is None:
        return _fail(403, 'NO_IDENTITY', '缺少已驗證的使用者身分，拒絕')
    draft_id, action, note = body.get('draft_id'), body.get('action'), body.get('note', '')
    if action not in ACTIONS:
        return _fail(400, 'BAD_ACTION', '不支援的動作')
    if not isinstance(draft_id, str) or not DRAFT_ID_RE.fullmatch(draft_id):
        return _fail(400, 'BAD_DRAFT_ID', '草稿 id 格式不合法')
    if not isinstance(note, str) or len(note) > NOTE_MAX or any(ord(c) < 32 for c in note):
        return _fail(400, 'BAD_NOTE', f'備註須為不含控制字元的文字，最長 {NOTE_MAX} 字')
    # 先對照目前快照：id 必須存在且為 pending_approval（CLI 仍會再檢查一次，名單檢查只在 CLI）。
    root = ui_dir(env)
    drafts, _err = read_json(root / 'drafts.json') if root else (None, 'missing')
    items = ((drafts or {}).get('drafts') or {}).get('items') if isinstance((drafts or {}).get('drafts'), dict) else None
    match = next((d for d in items or [] if isinstance(d, dict) and d.get('id') == draft_id), None)
    if match is None:
        return _fail(404, 'UNKNOWN_DRAFT', '快照中找不到這份草稿')
    if match.get('status') != 'pending_approval':
        return _fail(409, 'BAD_STATE', '草稿不是待核准狀態')
    extra: list[str] = []
    if (env.get(ENV_APPROVE_ARGS) or '').strip():
        try:
            extra = shlex.split(env[ENV_APPROVE_ARGS])
        except ValueError:
            return _fail(503, 'APPROVAL_DISABLED', f'{ENV_APPROVE_ARGS} 無法解析')
    argv = [*prefix, action, draft_id, f'--by={actor}']
    if note:
        argv.append(f'--note={note}')
    argv += extra
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=DECIDE_TIMEOUT, shell=False,
                      stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return _fail(504, 'DECIDE_TIMEOUT', '核准命令逾時')
    except OSError:
        return _fail(502, 'DECIDE_FAILED', '無法執行核准命令')
    result: dict = {}
    for line in reversed((proc.stdout or '').splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                result = parsed
                break
    if proc.returncode == 0 and result.get('status') == 'ok':
        return 200, {'ok': True, 'code': 'OK', 'message': '已核准' if action == 'approve' else '已駁回', 'by': actor}
    if result.get('status') == 'denied':
        status, msg = DENIED.get(str(result.get('code')), (403, '核准被拒絕'))
        return _fail(status, str(result.get('code')) if result.get('code') in DENIED else 'DENIED', msg)
    return _fail(502, 'DECIDE_FAILED', '核准命令執行失敗')


# ---------------------------------------------------------------- 頁面
CSS = """
:root{--bg:#0f1419;--panel:#1a212b;--b:#2d3744;--t:#e6edf3;--m:#8b98a9;--a:#4ea1ff;--g:#3fb950;--y:#d29922;--r:#f85149}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:13px/1.5 "IBM Plex Sans","Noto Sans TC",sans-serif}
header{display:flex;gap:14px;align-items:center;padding:8px 16px;background:var(--panel);border-bottom:1px solid var(--b)}
header a{color:var(--m);text-decoration:none}header .tabs a{padding:6px 10px;border-radius:3px}
header .tabs a.on{background:var(--b);color:var(--t)}main{padding:14px 16px;max-width:1180px;margin:auto}
.stale{background:#4b1d1d;border:1px solid var(--r);padding:8px 16px;font-weight:600}
.callout{border:1px solid var(--b);background:var(--panel);padding:8px 12px;margin:10px 0;border-radius:3px}.callout.warn{border-color:var(--y)}
.muted{color:var(--m)}.neg{color:var(--r)}.pos{color:var(--g)}code,pre{font-family:"JetBrains Mono",monospace}
pre{white-space:pre-wrap;word-break:break-word;background:#0b0f13;border:1px solid var(--b);padding:8px;max-height:420px;overflow:auto}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid var(--b);padding:4px 8px;text-align:left;vertical-align:top}
th{color:var(--m);font-weight:500}.cards{display:flex;gap:10px;flex-wrap:wrap}.card{background:var(--panel);border:1px solid var(--b);padding:8px 14px;min-width:150px}
.card span{display:block;color:var(--m)}.card b{font-size:20px}
.badge{display:inline-block;padding:0 6px;border-radius:9px;border:1px solid var(--b);font-size:11px}
.badge.ok{color:var(--g);border-color:var(--g)}.badge.bad{color:var(--r);border-color:var(--r)}.badge.warn{color:var(--y);border-color:var(--y)}.badge.na{color:var(--m)}
.cite.bad{color:var(--r)}.cite.na{color:var(--m)}.cites{list-style:none;padding-left:0}.thesis,.draft{border:1px solid var(--b);background:var(--panel);padding:8px 12px;margin:10px 0}
.thesis.invalid{border-color:var(--r)}.diff{display:grid;grid-template-columns:1fr 1fr;gap:10px}.dates{margin:8px 0;display:flex;gap:6px;flex-wrap:wrap}
.dates a{color:var(--m);text-decoration:none;padding:1px 6px;border:1px solid var(--b)}.dates a.on{color:var(--t);background:var(--b)}
.chart{width:100%;max-width:760px;background:var(--panel);border:1px solid var(--b)}.line{fill:none;stroke-width:1.8}.l-nav{stroke:var(--a)}.l-idx{stroke:var(--m)}.l-cf{stroke:var(--y);stroke-dasharray:5 3}
.lg{margin-right:14px}.lg::before{content:"";display:inline-block;width:18px;border-top:2px solid;margin-right:4px;vertical-align:middle}
.lg.l-nav{color:var(--a)}.lg.l-idx{color:var(--m)}.lg.l-cf{color:var(--y)}.ax{fill:var(--m);font-size:10px}.legend{margin:4px 0}
.lgv{margin-right:10px;padding:0 6px;border-left:8px solid}dl{margin:0}dt{color:var(--m);float:left;clear:left;margin-right:8px}dd{margin:0 0 2px 0}
.seg0{fill:#4ea1ff;border-color:#4ea1ff}.seg1{fill:#3fb950;border-color:#3fb950}.seg2{fill:#d29922;border-color:#d29922}.seg3{fill:#bc8cff;border-color:#bc8cff}
.seg4{fill:#f778ba;border-color:#f778ba}.seg5{fill:#39c5cf;border-color:#39c5cf}.seg6{fill:#ff9b52;border-color:#ff9b52}.seg7{fill:#8b98a9;border-color:#8b98a9}
button{font:inherit;color:var(--t);background:var(--b);border:1px solid var(--m);border-radius:3px;padding:4px 12px;cursor:pointer}
button.ok{border-color:var(--g)}button.bad{border-color:var(--r)}.note{font:inherit;width:260px;background:#0b0f13;color:var(--t);border:1px solid var(--b);padding:4px}
.actions{margin-top:8px}.result{margin-left:10px}
"""

APP_JS = r"""
document.addEventListener('click', async (ev) => {
  const btn = ev.target.closest('.decide-btn');
  if (!btn) return;
  const box = btn.closest('.draft');
  const out = box.querySelector('.result');
  const action = btn.dataset.action;
  if (!window.confirm((action === 'approve' ? '確定核准' : '確定駁回') + '草稿 ' + btn.dataset.id + '？')) return;
  box.querySelectorAll('.decide-btn').forEach((b) => { b.disabled = true; });
  try {
    const res = await fetch('/api/agent-trader/decide', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ draft_id: btn.dataset.id, action, note: box.querySelector('.note').value }),
    });
    const data = await res.json();
    out.textContent = (data.ok ? '完成：' : '失敗：') + (data.message || data.code || res.status);
    if (data.ok) setTimeout(() => window.location.reload(), 800);
    else box.querySelectorAll('.decide-btn').forEach((b) => { b.disabled = false; });
  } catch (err) {
    out.textContent = '失敗：網路錯誤';
    box.querySelectorAll('.decide-btn').forEach((b) => { b.disabled = false; });
  }
});
"""

CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
       "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SECURITY_HEADERS = {'Content-Security-Policy': CSP, 'X-Content-Type-Options': 'nosniff',
                    'X-Frame-Options': 'DENY', 'Cache-Control': 'no-store'}


def shell(screen: str, body: str, banners: str, meta: str) -> str:
    tabs = ''.join(f'<a class="{"on" if k == screen else ""}" href="/agent-trader?screen={k}">{e(lbl)}</a>' for k, lbl in SCREENS)
    return ('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent 交易員</title>'
            f'<style>{CSS}</style></head><body><header><a href="/">← 策略圖</a><b>Agent 交易員</b>'
            f'<nav class="tabs">{tabs}</nav></header>{banners}<main>{body}<p class="muted">{meta}</p></main>'
            '<script src="/agent-trader/app.js"></script></body></html>')


def render_page(screen: str, date: str | None, headers: Any, env: Mapping[str, str], now: datetime | None = None) -> str:
    now = now or datetime.now(TAIPEI)
    root = ui_dir(env)
    if screen not in dict(SCREENS):
        screen = 'overview'
    if date is not None and not DATE_RE.fullmatch(date):
        date = None
    if root is None or not root.is_dir():
        reason = f'尚未設定 {ENV_UI_DIR}' if root is None else '資料目錄不存在'
        return shell(screen, f'<div class="callout">尚無資料：{e(reason)}。請先在 agent-trader 執行 <code>ops export-ui</code>。</div>', '', '')
    latest, lerr = read_json(root / 'latest.json')
    drafts, derr = read_json(root / 'drafts.json')
    days = list_days(root)
    current = date if date in days else (days[0] if days else None)
    day_doc = None
    day_err = None
    if current:
        day_doc, day_err = read_json(root / 'days' / f'{current}.json')
    docs = {'overview': latest, 'positions': latest, 'drafts': drafts, 'decisions': None, 'reasoning': day_doc, 'review': day_doc}
    used = docs[screen]
    banners = []
    for label, doc in (('latest.json', latest), ('drafts.json', drafts if screen == 'drafts' else None),
                       (f'days/{current}.json', day_doc if screen in ('reasoning', 'review') else None)):
        if not isinstance(doc, dict):
            continue
        reasons = stale_reasons(doc, now)
        if reasons:
            banners.append(f'<div class="stale" role="alert">資料已過期（{e(label)}）：{e(", ".join(reasons))}。以下不是最新資料。</div>')
        if doc.get('schema_version') != 1:
            banners.append(f'<div class="stale" role="alert">{e(label)} 的 schema_version = {e(doc.get("schema_version"))}，'
                           '本頁只支援 1，內容可能顯示不全。</div>')
    need = {'overview': (lerr, 'latest.json'), 'positions': (lerr, 'latest.json'), 'drafts': (derr, 'drafts.json'),
            'reasoning': (day_err, 'days/<date>.json'), 'review': (day_err, 'days/<date>.json')}
    if screen in need and (need[screen][0] or used is None) and screen != 'decisions':
        err, fname = need[screen]
        msg = {'missing': '檔案不存在', 'invalid': '檔案內容損毀或不是 JSON 物件', 'too_large': '檔案過大'}.get(err or 'missing', '無法讀取')
        if screen in ('reasoning', 'review') and not days:
            msg = 'days/ 下沒有逐日檔'
        body = f'<div class="callout">尚無資料：{e(fname)} {e(msg)}。</div>'
    else:
        approval = approval_state(headers, env)
        fn = {'overview': screen_overview, 'positions': screen_positions, 'decisions': screen_decisions,
              'reasoning': screen_reasoning, 'review': screen_review, 'drafts': screen_drafts}[screen]
        body = fn(latest=latest, drafts_doc=drafts, day_doc=day_doc, days=days, root=root, approval=approval)
        if screen in ('reasoning', 'review'):
            body = date_nav(screen, days, current) + body
    src = used if isinstance(used, dict) else latest
    meta = ''
    if isinstance(src, dict):
        meta = f'產生於 {e(src.get("generated_at"))}・as_of {e(src.get("as_of_date"))}・資料至 {e(src.get("data_as_of"))}'
    return shell(screen, body, ''.join(banners), meta)


def handle_get(parts: list[str], query: Mapping[str, list[str]], headers: Any,
               env: Mapping[str, str] = os.environ, now: datetime | None = None) -> tuple[int, str, bytes]:
    """回傳 (狀態, content-type, body)。parts 為 URL 路徑片段，第一段必為 agent-trader。"""
    if parts == ['agent-trader', 'app.js']:
        return 200, 'application/javascript; charset=utf-8', APP_JS.encode('utf-8')
    if parts == ['agent-trader']:
        page = render_page((query.get('screen') or ['overview'])[0], (query.get('date') or [None])[0], headers, env, now)
        return 200, 'text/html; charset=utf-8', page.encode('utf-8')
    return 404, 'text/plain; charset=utf-8', b'not found'
