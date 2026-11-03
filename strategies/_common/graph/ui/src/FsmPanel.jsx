import React, { useEffect, useMemo, useState } from "react";
import { api } from "./model";
import {
  cexParts, cfgRows, edgePath, enforcedText, layoutGraph, pickTarget, resultInfo, staleInfo, verdictInfo,
  ORIGIN, CONFIRM, PASS_TEXT,
} from "./fsm";

export function StateGraph({ graph, onPick, active }) {
  const layout = useMemo(() => layoutGraph(graph), [graph]);
  const groups = useMemo(() => {
    const g = new Map();
    (graph.edges || []).forEach((e, i) => {
      const k = [e.from, e.to].sort().join("|");
      g.set(k, [...(g.get(k) || []), i]);
    });
    return g;
  }, [graph]);
  if (!graph.nodes?.length) return <p className="fsm-empty">沒有狀態圖資料。</p>;
  return (
    <svg className="fsm-graph" style={{ width: Math.max(360, layout.width * 1.25) }} viewBox={`0 0 ${layout.width} ${Math.max(layout.height, 80)}`} role="img" aria-label="倉位狀態圖" data-testid="fsm-graph">
      <defs>
        <marker id="fsm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" className="fsm-arrow" />
        </marker>
      </defs>
      {(graph.edges || []).map((e, i) => {
        const peers = groups.get([e.from, e.to].sort().join("|")) || [i];
        const p = edgePath(e, layout.nodes, peers.indexOf(i), peers.length);
        if (!p) return null;
        return (
          <g key={`${e.id}-${e.from}-${i}`} className={`fsm-edge ${active === e.id ? "on" : ""}`} onClick={() => onPick?.(e)} data-testid="fsm-edge">
            <title>{[e.label_zh, e.event_label_zh, e.guard_zh && `條件：${e.guard_zh}`, ...(e.orders_zh || [])].filter(Boolean).join("\n")}</title>
            <path d={p.d} markerEnd="url(#fsm-arrow)" />
            <text x={p.lx} y={p.ly} textAnchor={p.anchor || "middle"}>{e.label_zh}</text>
          </g>
        );
      })}
      {graph.nodes.map((n) => {
        const b = layout.nodes[n.id];
        return (
          <g key={n.id} className={`fsm-node ${n.initial ? "initial" : ""} ${n.unreachable_by_config ? "unreachable" : ""}`} data-testid="fsm-node">
            <title>{n.description_zh || n.label_zh}</title>
            <rect x={b.x} y={b.y} width={b.w} height={b.h} rx="8" />
            <text x={b.x + b.w / 2} y={b.y + b.h / 2 + 4} textAnchor="middle">{n.label_zh || n.id}</text>
            {n.initial && <text x={b.x + 8} y={b.y - 4} className="fsm-tag">起點</text>}
            {n.unreachable_by_config && <text x={b.x + b.w - 8} y={b.y - 4} textAnchor="end" className="fsm-tag">此設定下不可達</text>}
          </g>
        );
      })}
    </svg>
  );
}

function Cex({ cex }) {
  const parts = useMemo(() => cexParts(cex.text_zh), [cex.text_zh]);
  return (
    <div className="fsm-cex" data-testid="fsm-cex">
      {!parts.complete && (
        <p className="fsm-note warn" data-testid="fsm-cex-incomplete">
          這段反例缺少固定的三段結尾（規則來源、模型數字與來源、代表什麼），不能當成完整說明，請以原始結果為準。
        </p>
      )}
      <pre className="fsm-cex-text">{cex.text_zh}</pre>
    </div>
  );
}

export function FsmView({ view, initialId }) {
  const [id, setId] = useState(initialId || null);
  const [sel, setSel] = useState(null);
  const target = pickTarget(view, id);
  if (!target) {
    return <p className="fsm-empty" data-testid="fsm-empty">還沒有資料：這個策略沒有狀態機描述，也沒有模型檢查結果。</p>;
  }
  const stale = staleInfo(view);
  const rows = cfgRows(target);
  const invs = target.invariants || [];
  const chosen = invs.find((i) => i.id === sel && i.counterexample) || invs.find((i) => i.counterexample) || null;
  const refused = target.refused || [];
  return (
    <div className="fsm-body">
      <p className="fsm-disclaimer" data-testid="fsm-disclaimer">{view.disclaimer}</p>
      <div className="fsm-meta">
        <span data-testid="fsm-source">
          {view.source === "fixture" ? "測試資料（示範，不是真實結果）" : view.source === "atlas" ? "模型檢查結果檔" : "只有狀態機描述（尚無模型檢查結果）"}
        </span>
        {view.generated_at && <span className="mono">{view.generated_at}</span>}
        {!stale.known && <span className="fsm-badge warn">無法判斷資料新舊</span>}
        {stale.stale && <span className="fsm-badge warn" data-testid="fsm-stale">結果已過舊</span>}
        {view.tlc_note && <span className="muted">{view.tlc_note}</span>}
      </div>
      {view.targets.length > 1 && (
        <div className="fsm-tabs" role="tablist">
          {view.targets.map((t) => (
            <button key={t.id} role="tab" aria-selected={t.id === target.id} className={t.id === target.id ? "on" : ""} onClick={() => { setId(t.id); setSel(null); }}>
              {t.title_zh || t.id}
            </button>
          ))}
        </div>
      )}
      <h3 data-testid="fsm-title">{target.title_zh || target.id}</h3>
      {target.status_reason && <p className="fsm-note">{target.status_reason}</p>}
      {refused.length > 0 && (
        <div className="fsm-note warn" data-testid="fsm-refused">
          <b>沒有任何規則被檢查：</b>模型還缺下列資料，產生器不會替人補預設值。
          <ul>{refused.map((r, i) => <li key={i}><span className="mono">{r.code}</span> {r.message}</li>)}</ul>
        </div>
      )}
      <h4>狀態圖</h4>
      <StateGraph graph={target.graph || {}} />
      <h4>規則清單</h4>
      <table className="fsm-table" data-testid="fsm-invariants">
        <thead><tr><th>規則</th><th>結果</th><th>來源</th><th>確認狀態</th><th>程式碼是否實作</th></tr></thead>
        <tbody>
          {invs.map((i) => {
            const v = verdictInfo(i.verdict);
            return (
              <tr key={i.id} className={chosen?.id === i.id ? "on" : ""} onClick={() => i.counterexample && setSel(i.id)}>
                <td>{i.label_zh || i.id}<div className="mono muted">{i.id}</div></td>
                <td><span className={`fsm-badge ${v.cls}`} title={i.verdict_reason || ""}>{v.icon} {v.label}</span></td>
                <td>{ORIGIN[i.origin] || i.origin || "—"}</td>
                <td>{CONFIRM[i.confirm_status] || i.confirm_status || "—"}</td>
                <td>{enforcedText(i.enforced_in_code)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <h4>各設定的結果</h4>
      <table className="fsm-table" data-testid="fsm-cfgs">
        <thead><tr><th>設定</th><th>結果</th><th>探索的狀態數</th></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan="3" className="muted">沒有任何設定被執行。</td></tr>}
          {rows.map((r) => {
            const x = resultInfo(r.result);
            return (
              <tr key={r.key}>
                <td className="mono">{r.key}{r.note ? <div className="muted">{r.note}</div> : null}</td>
                <td><span className={`fsm-badge ${x.cls}`}>{x.icon} {x.label}</span></td>
                <td className="mono">{r.distinct_states ?? "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {chosen && (
        <>
          <h4>白話反例：{chosen.label_zh || chosen.id}</h4>
          <Cex cex={chosen.counterexample} />
        </>
      )}
      {(target.model_numbers || []).length > 0 && (
        <details>
          <summary>模型數字與出處</summary>
          <ul>{target.model_numbers.map((m, i) => <li key={i}>{m.name}：{String(m.value)} {m.unit}　<span className="muted">{m.source}</span></li>)}</ul>
        </details>
      )}
      {(target.out_of_scope || []).length > 0 && (
        <details>
          <summary>沒有驗到的</summary>
          <ul>{target.out_of_scope.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

/** 載入資料；失敗時只顯示控制過的訊息。 */
export function useFsm(revision) {
  const [state, setState] = useState({ phase: "loading" });
  useEffect(() => {
    let live = true;
    api("/fsm")
      .then((view) => live && setState({ phase: "ok", view }))
      .catch(() => live && setState({ phase: "error" }));
    return () => { live = false; };
  }, [revision]);
  return state;
}

export function FsmBody({ revision }) {
  const s = useFsm(revision);
  if (s.phase === "loading") return <p className="fsm-empty">讀取中…</p>;
  if (s.phase === "error") return <p className="fsm-empty" data-testid="fsm-error">形式化驗證資料暫時無法使用。</p>;
  return <FsmView view={s.view} initialId={s.view.current} />;
}

export function Fsm({ close, revision }) {
  return (
    <div className="replay-overlay fsm-overlay" data-testid="fsm-panel" role="dialog" aria-label="形式化驗證（FSM）">
      <header>
        <div>
          <small>FORMAL CHECK</small>
          <h2>形式化驗證（FSM）</h2>
        </div>
        <button onClick={close} data-testid="fsm-close">關閉</button>
      </header>
      <FsmBody revision={revision} />
    </div>
  );
}

export { PASS_TEXT };
