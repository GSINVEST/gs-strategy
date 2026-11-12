import React, { useState, useEffect, useRef, useMemo } from "react";
import { createRoot } from "react-dom/client";
import {
  ReactFlow,
  Background,
  BaseEdge,
  Controls,
  Handle,
  Position,
  ViewportPortal,
  getSmoothStepPath,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/noto-sans-tc/400.css";
import "./style.css";
import { Replay } from "./Replay";
import { Fsm, FsmBody } from "./FsmPanel";
import { StrategyMenu, Results } from "./Pool";
import { humanizeOutputs } from "./humanize";
import {
  api,
  compatible,
  unavailable,
  estimateText,
  portColor,
  portShape,
  names,
  own,
  stages,
  column,
  positions,
  latestPreview,
  frame,
  fmt,
  edgeId,
  costText,
  edgeLanes,
  STAGE_WIDTH,
  initialFit,
  pollJob,
  resumeOrStart,
} from "./model";

function Plot({ value, large = false, title = "最近區間" }) {
  const f = frame(value),
    [hover, setHover] = useState(null);
  if (!f?.data?.length) return <div className="empty">等待資料</div>;
  const numeric = f.columns
    .map((name, j) => ({ name, j }))
    .filter(({ j }) => f.data.some((r) => typeof r[j] === "number"));
  if (!numeric.length)
    return <div className="empty">{f.data.length} 筆資料</div>;
  const cols = numeric.slice(0, large ? 8 : 1),
    all = f.data
      .flatMap((r) => cols.map((c) => r[c.j]))
      .filter(Number.isFinite),
    min = Math.min(...all),
    max = Math.max(...all),
    w = large ? 370 : 210,
    h = large ? 200 : 42,
    p = large ? 22 : 3;
  const x = (i) => p + (i / Math.max(1, f.data.length - 1)) * (w - 2 * p),
    y = (v) => h - p - ((v - min) / (max - min || 1)) * (h - 2 * p);
  return (
    <div className="plot">
      <div className="plot-title">
        {title === "最近區間" || title === "輸出序列"
          ? cols.map((c) => c.name).join(" · ")
          : title}
        <span className="mono">{fmt(f.data.at(-1)[cols[0].j])}</span>
      </div>
      <svg
        viewBox={`0 0 ${w} ${h}`}
        role="img"
        aria-label={title}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setHover(
            Math.max(
              0,
              Math.min(
                f.data.length - 1,
                Math.round(
                  ((((e.clientX - r.left) / r.width) * w - p) / (w - 2 * p)) *
                    (f.data.length - 1),
                ),
              ),
            ),
          );
        }}
      >
        {large &&
          [0, 0.5, 1].map((n) => (
            <g key={n}>
              <line
                className="gridline"
                x1={p}
                x2={w - p}
                y1={p + n * (h - 2 * p)}
                y2={p + n * (h - 2 * p)}
              />
              <text className="axis" x={p} y={p + n * (h - 2 * p) - 3}>
                {fmt(max - n * (max - min))}
              </text>
            </g>
          ))}
        {cols.map((c, k) => (
          <polyline
            key={c.name}
            fill="none"
            stroke={`var(--series-${k % 4})`}
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
            points={f.data
              .map((r, i) =>
                Number.isFinite(r[c.j]) ? `${x(i)},${y(r[c.j])}` : "",
              )
              .join(" ")}
          />
        ))}
        {large &&
          cols.length > 1 &&
          cols.length <= 4 &&
          cols.map((c) => (
            <text
              className="axis"
              key={c.name}
              x={w - p}
              y={y(f.data.at(-1)[c.j])}
              textAnchor="end"
            >
              {c.name}
            </text>
          ))}
        {large && hover !== null && (
          <>
            <line
              className="crosshair"
              x1={x(hover)}
              x2={x(hover)}
              y1="0"
              y2={h}
            />
            <line
              className="crosshair"
              x1="0"
              x2={w}
              y1={y(f.data[hover][cols[0].j])}
              y2={y(f.data[hover][cols[0].j])}
            />
          </>
        )}
      </svg>
      {large && cols.length > 1 && (
        <div className="legend">
          {cols.map((c, k) => (
            <span key={c.name}>
              <i style={{ background: `var(--series-${k % 4})` }} />
              {c.name}
            </span>
          ))}
        </div>
      )}
      {large && hover !== null && (
        <div className="tooltip mono">
          {f.index[hover]}
          <br />
          {cols.map((c) => `${c.name}: ${fmt(f.data[hover][c.j])}`).join(" · ")}
        </div>
      )}
    </div>
  );
}
function Result({ state = {} }) {
  const o = state.outputs || {};
  if (state.status === "error" && o.Prescription) {
    return <div className="empty">目前事實無法產生處方</div>;
  }
  if (o.Report) {
    const r = o.Report.metrics || o.Report;
    return (
      <>
        <div className="metrics">
          {[
            ["Sharpe", "annualized_sharpe"],
            ["PSR", "psr"],
            ["DSR", "dsr"],
            ["MDD", "max_drawdown"],
            ["CAGR", "cagr"],
          ].map(([label, key]) => (
            <div key={key}>
              {label}
              <strong>{fmt(r[key])}</strong>
            </div>
          ))}
        </div>
        <div className="warnings-scroll nodrag nowheel">
          {(o.Report.warnings || []).map((w, i) => (
            <p className="warning-text" key={i}>
              ⚠ {w}
            </p>
          ))}
        </div>
      </>
    );
  }
  if (o.Facts)
    return (
      <div className="facts">
        {["normal", "autocorr", "n_eff"].map((key) => {
          const p = o.Facts.provenance?.[key] || {};
          return (
            <div key={key}>
              <b>
                {key} → {fmt(o.Facts.facts?.[key])}
              </b>
              <small title={p.test || p.method}>
                {p.test ||
                  (key === "n_eff" ? "有效樣本數 / ACF" : p.method) ||
                  "—"}{" "}
                · p=
                {typeof p.p_value === "number" &&
                p.p_value !== 0 &&
                p.p_value < 0.0001
                  ? p.p_value.toExponential(2)
                  : fmt(p.p_value)}
              </small>
            </div>
          );
        })}
      </div>
    );
  if (o.Prescription) {
    const r = o.Prescription;
    return (
      <div className="prescription">
        {["base", "se", "primary", "threshold", "gates"].map((key) => {
          const s = r.slots?.[key] || {};
          return (
            <div key={key}>
              <div className="slot-line nodrag nowheel">
                <b>
                  {key} → {fmt(s.value)}
                </b>
                <span>
                  {" "}
                  ←{" "}
                  {Object.entries(s.facts || {})
                    .map(([k, v]) => `${k}=${fmt(v)}`)
                    .join(", ")}
                </span>
              </div>
              <small className="rule-line nodrag nowheel">
                rule: {fmt(s.rule_id)}
              </small>
            </div>
          );
        })}
        <div className="forbids nodrag nowheel">
          {(r.forbids || []).map((x, i) => (
            <del key={i}>{fmt(x)}</del>
          ))}
        </div>
      </div>
    );
  }
  if (o.Docs) {
    const days = Object.entries(o.Docs.values || {});
    const latest = days[days.length - 1];
    return (
      <div className="scalar summary-card">
        {days.length} 個決策日・排除無可信日期 {fmt(o.Docs.excluded_unknown_time)} 篇
        <small>
          最新（{latest?.[0] || "—"}）：
          {(latest?.[1] || []).map((d) => d.title).join("；") || "無"}
        </small>
      </div>
    );
  }
  if (o.ProbeReport) {
    const p = o.ProbeReport;
    return (
      <div className={`scalar summary-card ${p.leak_suspected ? "bad" : "ok"}`}>
        {p.leak_suspected ? "疑似記住歷史價格" : "未見記憶跡象"}
        <small>
          事前誤差中位數 {fmt(p.pre_cutoff?.median_abs_pct_error)}%（{p.pre_cutoff?.answered}/{p.pre_cutoff?.n}）・
          事後 {fmt(p.post_cutoff?.median_abs_pct_error)}%（{p.post_cutoff?.answered}/{p.post_cutoff?.n}）
        </small>
      </div>
    );
  }
  if (o.MonteCarlo) {
    const m = o.MonteCarlo;
    return (
      <div className="scalar summary-card">
        Sharpe 中位 {fmt(m.sharpe?.p50)}（5–95%：{fmt(m.sharpe?.p05)}～{fmt(m.sharpe?.p95)}）
        <small>
          {m.n_paths} 條路徑・P(Sharpe≤0) {fmt(m.prob_sharpe_le_0)}・MDD 中位 {fmt(m.max_drawdown?.p50)}・不增加 N
        </small>
      </div>
    );
  }
  if (o.LedgerN)
    return (
      <div className="ledger-result mono">
        N {o.LedgerN.selection_n} · +{o.LedgerN.session_n}
      </div>
    );
  if (o.CostModel)
    return <div className="scalar">{costText(o.CostModel)}</div>;
  if (frame(state.equity || o))
    return (
      <Plot
        value={state.equity || o}
        title={state.equity ? "權益曲線" : "最近區間"}
      />
    );
  const cards = humanizeOutputs(o);
  if (cards) return <MetaCards cards={cards} raw={o} />;
  return <div className="empty">— 尚無產出</div>;
}
// 以人看得懂的方式呈現節點 metadata；原始 JSON 收在「原始資料」摺疊裡，需要時才展開
function MetaCards({ cards, raw }) {
  return (
    <div className="meta-cards nodrag nowheel" data-testid="meta-cards">
      {cards.map((c, i) => (
        <div className="meta-card" key={i}>
          <b className="meta-title">{c.title}</b>
          {c.summary && <p className="meta-summary">{c.summary}</p>}
          {c.rows.length > 0 && (
            <dl className="meta-rows">
              {c.rows.map(([k, v]) => (
                <React.Fragment key={k}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </React.Fragment>
              ))}
            </dl>
          )}
          {(c.tags.length > 0 || c.reference) && (
            <div className="meta-tags">
              {c.reference && <span className="meta-tag ref">參考：{c.reference}</span>}
              {c.tags.map((t) => (
                <span className="meta-tag" key={t}>
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
      <details className="meta-raw">
        <summary>原始資料</summary>
        <pre className="mono">{JSON.stringify(raw, null, 2)}</pre>
      </details>
    </div>
  );
}
function Param({ id, name, schema, value, change, disabled }) {
  const props = {
    "data-testid": `param-${id}-${name}`,
    className: "nodrag",
    disabled,
    "aria-label": name,
  };
  if (schema.enum)
    return (
      <label className="param">
        <span>{name}</span>
        <select
          {...props}
          value={value ?? schema.default}
          onChange={(e) => change(id, name, e.target.value)}
        >
          {schema.enum.map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
      </label>
    );
  if (schema.type === "boolean")
    return (
      <label className="param">
        <span>{name}</span>
        <input
          {...props}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => change(id, name, e.target.checked)}
        />
      </label>
    );
  if (["number", "integer"].includes(schema.type)) {
    const step = schema.type === "integer" ? 1 : "any";
    const sliderMax =
      schema.maximum ??
      Math.max(Number(schema.default || 1) * 3, Number(value || 1) * 1.2, 10);
    return (
      <label className="param numeric">
        <span>{name}</span>
        <div>
          {schema.minimum !== undefined && (
            <input
              {...props}
              data-testid={`slider-${id}-${name}`}
              title="滑桿視窗由參數 schema 的 minimum/default 推導；數字欄可輸入更大值"
              type="range"
              min={schema.minimum}
              max={sliderMax}
              step={
                schema.type === "integer"
                  ? 1
                  : (sliderMax - schema.minimum) / 200
              }
              value={value ?? schema.default}
              onChange={(e) => change(id, name, Number(e.target.value))}
            />
          )}
          <input
            {...props}
            type="number"
            min={schema.minimum}
            max={schema.maximum}
            step={step}
            value={value ?? schema.default ?? ""}
            onChange={(e) => {
              if (e.target.value !== "" && e.target.validity.valid)
                change(id, name, Number(e.target.value));
            }}
          />
        </div>
      </label>
    );
  }
  if (["array", "object"].includes(schema.type))
    return (
      <label className="param">
        <span>{name}</span>
        <input
          {...props}
          key={JSON.stringify(value)}
          defaultValue={JSON.stringify(value)}
          onBlur={(e) => {
            try {
              change(id, name, JSON.parse(e.target.value));
              e.target.setCustomValidity("");
            } catch {
              e.target.setCustomValidity("請輸入有效 JSON");
              e.target.reportValidity();
            }
          }}
        />
      </label>
    );
  return (
    <label className="param">
      <span>{name}</span>
      <input
        {...props}
        value={value ?? ""}
        onChange={(e) => change(id, name, e.target.value)}
      />
    </label>
  );
}
const statusLabels = {
  cached: "○ 快取",
  recomputed: "● 已重算",
  running: "◉ 執行中",
  stale: "◷ 過期",
  not_ready: "┄ 未就緒",
  error: "× 錯誤",
};
export function GraphNode({ id, data }) {
  const {
    node,
    type,
    state = {},
    change,
    locked,
    dragType,
    inspect,
    progress,
  } = data;
  const status = state.status || "stale";
  const color = portColor(Object.values(type.outputs)[0]);
  return (
    <article
      data-testid={`node-${id}`}
      data-status={status}
      className={`node-card ${status} ${column(node) >= 3 ? "trial" : ""}`}
      style={{ "--category": color, "--stage": column(node) }}
    >
      <header className="node-title" onClick={() => inspect(id)}>
        <strong>{own(names, id) || id}</strong>
        <small>{node.type}</small>
      </header>
      <div className={`status ${status}`}>
        {own(statusLabels, status)}
        {status === "not_ready" &&
          `：${state.message || "缺必要輸入，請完成接線"}`}
      </div>
      {status === "error" && <p className="error-message">{state.message}</p>}
      <div className="ports">
        {["inputs", "outputs"].map((side) => (
          <div className={side} key={side}>
            {Object.entries(type[side]).map(([port, t]) => (
              <div
                className={`port ${dragType && side === "inputs" ? (compatible(dragType, t) ? "compatible" : "incompatible") : ""}`}
                key={port}
                title={
                  dragType && !compatible(dragType, t)
                    ? `型別不符：${dragType} → ${t}`
                    : t
                }
              >
                <Handle
                  data-testid={`handle-${id}-${side === "inputs" ? "input" : "output"}-${port}`}
                  type={side === "inputs" ? "target" : "source"}
                  position={side === "inputs" ? Position.Left : Position.Right}
                  id={port}
                  className={portShape(t)}
                  style={{ background: portColor(t) }}
                />
                <span>{t}</span>
                {dragType && side === "inputs" && !compatible(dragType, t) && (
                  <em>
                    型別不符：{dragType} → {t}
                  </em>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="params">
        {Object.entries(type.params).map(([name, schema]) => (
          <Param
            key={name}
            id={id}
            name={name}
            schema={schema}
            value={own(node.params, name) ?? schema.default}
            change={change}
            disabled={locked}
          />
        ))}
      </div>
      {status === "running" && node.type.startsWith("backtest.") && (
        <div className="progress">
          <progress
            max="1"
            value={
              progress?.total ? progress.completed / progress.total : undefined
            }
          />
          <span>
            {progress?.total
              ? `${progress.completed} / ${progress.total}`
              : "回測計算中"}
          </span>
        </div>
      )}
      <section className="preview">
        <Result state={state} />
      </section>
      {status === "stale" && column(node) >= 3 && (
        <p className="stale-note">過期：顯示的是舊參數的結果</p>
      )}
    </article>
  );
}
const nodeTypes = { instrument: GraphNode };
function LaneEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
                    style, data, selected }) {
  const [path] = getSmoothStepPath({
    sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
    borderRadius: 8, offset: 12,
    ...(data?.laneX != null ? { centerX: data.laneX } : {}),
  });
  return (
    <BaseEdge id={id} path={path} interactionWidth={14}
      className={`lane-edge ${selected ? "selected" : ""}`} style={style} />
  );
}
const edgeTypes = { lane: LaneEdge };
export function Drawer({ id, close, revision }) {
  const [data, setData] = useState(null),
    [full, setFull] = useState(null),
    [offset, setOffset] = useState(0),
    [error, setError] = useState("");
  useEffect(() => {
    setOffset(0);
  }, [id]);
  useEffect(() => {
    let alive = true;
    api(`/nodes/${id}?limit=60&offset=${offset}`)
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((e) => setError(e.message));
    return () => {
      alive = false;
    };
  }, [id, offset, revision]);
  useEffect(() => {
    let alive = true;
    (async () => {
      const first = await api(`/nodes/${id}?limit=1000&offset=0`);
      let all = frame(first.equity || first.outputs);
      if (all) {
        all = { ...all, index: [...all.index], data: [...all.data] };
        for (let off = 1000; off < all.total; off += 1000) {
          const chunk = await api(`/nodes/${id}?limit=1000&offset=${off}`);
          const part = frame(chunk.equity || chunk.outputs);
          if (part) {
            all.index.unshift(...part.index);
            all.data.unshift(...part.data);
          }
        }
      }
      if (alive) setFull(all);
    })().catch((e) => {
      if (alive) setError(e.message);
    });
    return () => {
      alive = false;
    };
  }, [id, revision]);
  const f = frame(data?.equity || data?.outputs);
  return (
    <aside className="drawer">
      <header>
        <div>
          <small>NODE INSPECTOR</small>
          <h2>{own(names, id) || id}</h2>
        </div>
        <button onClick={close} aria-label="關閉檢視">
          ×
        </button>
      </header>
      {error && <p>{error}</p>}
      {data && (
        <>
          <Plot
            value={full}
            large
            title={data.equity ? "權益曲線" : "輸出序列"}
          />
          <h3>原始數值</h3>
          <div className="table-wrap">
            {f ? (
              <table>
                <thead>
                  <tr>
                    <th>時間</th>
                    {f.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {f.data.map((row, i) => (
                    <tr key={i}>
                      <td>{f.index[i]}</td>
                      {row.map((v, j) => (
                        <td key={j}>{fmt(v)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <pre>{JSON.stringify(data.outputs, null, 2)}</pre>
            )}
          </div>
          {f && (
            <nav>
              <button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 60))}
              >
                較新 60 筆
              </button>
              <span className="mono">
                offset {offset} / {f.total}
              </span>
              <button
                disabled={offset + 60 >= f.total}
                onClick={() => setOffset(offset + 60)}
              >
                較舊 60 筆
              </button>
            </nav>
          )}
          <h3>Provenance</h3>
          <pre>
            {JSON.stringify(
              {
                node_hash: data.hash,
                graph_hash: data.graph_hash,
                status: data.status,
                ...(data.outputs?.Facts?.provenance || {}),
                ...(data.outputs?.Prescription
                  ? { slots: data.outputs.Prescription.slots }
                  : {}),
                source: Object.values(data.outputs || {}).find((v) => v?.source)
                  ?.source,
              },
              null,
              2,
            )}
          </pre>
        </>
      )}
    </aside>
  );
}

function App() {
  const [doc, setDoc] = useState(null),
    [types, setTypes] = useState({}),
    [states, setStates] = useState({}),
    [layout, setLayout] = useState({}),
    [measured, setMeasured] = useState({}),
    [ledger, setLedger] = useState({}),
    [estimate, setEstimate] = useState(null),
    [session, setSession] = useState({}),
    [error, setError] = useState(""),
    [warnings, setWarnings] = useState([]),
    [job, setJob] = useState(null),
    [submitting, setSubmitting] = useState(false),
    [previewState, setPreviewState] = useState("idle"),
    [selected, setSelected] = useState(null),
    [selectedEdges, setSelectedEdges] = useState(new Set()),
    [dragType, setDragType] = useState(null),
    [connectionError, setConnectionError] = useState(""),
    [sidecar, setSidecar] = useState(""),
    [showLoad, setShowLoad] = useState(false),
    [showReplay, setShowReplay] = useState(false),
    [showFsm, setShowFsm] = useState(false),
    [showResults, setShowResults] = useState(false),
    [revision, setRevision] = useState(0),
    [flash, setFlash] = useState(false);
  const flow = useRef(null);
  const fitInitial = useRef(initialFit());
  const following = useRef(new Map());
  const [recovering, setRecovering] = useState(true);
  const submittingRef = useRef(false);
  const previewStart = useRef(null);
  const pendingUpdates = useRef(Object.create(null));
  const pendingRevision = useRef(null);
  const active = useRef(null),
    docRef = useRef(null),
    scheduler = useRef(null),
    ledgerRef = useRef(0),
    mounted = useRef(true);
  const documentUpdate = (d) => {
    if (d.graph && Object.keys(pendingUpdates.current).length)
      d = {
        ...d,
        dirty: true,
        graph: {
          ...d.graph,
          nodes: d.graph.nodes.map((n) => ({
            ...n,
            params: { ...n.params, ...pendingUpdates.current[n.id] },
          })),
        },
      };
    docRef.current = d;
    setDoc(d);
  };
  async function refresh() {
    const [ns, l, e, d] = await Promise.all([
      api("/nodes"),
      api("/ledger"),
      api("/run-estimate").catch(() => null),
      api("/graph"),
    ]);
    if (!mounted.current) return;
    setStates(Object.fromEntries(ns.nodes.map((n) => [n.id, n])));
    setLedger(l);
    if (l.selection_n > ledgerRef.current) {
      setFlash(true);
      setTimeout(() => setFlash(false), 600);
    }
    ledgerRef.current = l.selection_n;
    setEstimate(e);
    documentUpdate(d);
    setRevision((v) => v + 1);
  }
  async function handleError(e) {
    if (e.status === 409 && ["graph_revision_conflict", "run_estimate_conflict"].includes(e.code)) {
      scheduler.current?.reset();
      pendingUpdates.current = Object.create(null);
      pendingRevision.current = null;
      setEstimate(null);
      setConnectionError("");
      setSelectedEdges(new Set());
      await refresh();
      setError(e.code === "run_estimate_conflict"
        ? "執行預判已變更，已重新預判，請確認後再次執行"
        : "圖已被其他分頁修改，已重新載入");
      return;
    }
    setError(e.message);
  }
  async function waitJob(id) {
    if (following.current.has(id)) return following.current.get(id);
    const waiting = pollJob(id, {
      update: (j) => {
        if (mounted.current && active.current?.id === id) setJob(j);
      },
      nodes: async () => {
        if (!mounted.current || active.current?.id !== id) return;
        const ns = await api("/nodes");
        if (mounted.current)
          setStates(Object.fromEntries(ns.nodes.map((n) => [n.id, n])));
      },
    }).finally(() => following.current.delete(id));
    following.current.set(id, waiting);
    return waiting;
  }
  async function cancelPreview() {
    // A newer edit can arrive before POST /preview has returned its job id.
    // Wait for that id so the superseded worker is cancelled as soon as possible.
    if (previewStart.current) await previewStart.current.catch(() => null);
    if (active.current?.preview) {
      const id = active.current.id;
      await api("/jobs/" + id + "/cancel", {}).catch(() => {});
      await waitJob(id);
      if (active.current?.id === id) {
        active.current = null;
        setJob(null);
      }
    }
  }
  async function followJob(j) {
    active.current = j;
    setJob(j);
    const result = await waitJob(j.id);
    if (active.current?.id === j.id) {
      active.current = null;
      setJob(null);
      if (result.sidecar) setSidecar(result.sidecar);
      if (result.status === "error") setError(result.message || "執行失敗");
      await refresh();
    }
    return result;
  }
  async function launchPreview() {
    return resumeOrStart({
      session: () => api("/session"),
      follow: followJob,
      start: async () => {
        const starting = api("/preview", {});
        previewStart.current = starting;
        let j;
        try {
          j = await starting;
        } finally {
          if (previewStart.current === starting) previewStart.current = null;
        }
        return followJob(j);
      },
    });
  }
  useEffect(() => {
    mounted.current = true;
    scheduler.current = latestPreview({
      cancel: cancelPreview,
      apply: async (edits) => {
        for (const [id, params] of Object.entries(edits)) {
          const d = await api("/nodes/" + id + "/params", {
            params, expected_revision: pendingRevision.current ?? docRef.current.revision,
          });
          pendingRevision.current = d.revision;
          for (const [k, v] of Object.entries(params)) {
            if (own(pendingUpdates.current[id], k) === v)
              delete pendingUpdates.current[id][k];
          }
          if (!Object.keys(pendingUpdates.current[id] || {}).length)
            delete pendingUpdates.current[id];
          documentUpdate(d);
        }
        if (!Object.keys(pendingUpdates.current).length) pendingRevision.current = null;
        await refresh();
      },
      preview: launchPreview,
      onState: setPreviewState,
      onError: handleError,
    });
    (async () => {
      try {
        const [t, s, d] = await Promise.all([
          api("/node-types"),
          api("/session"),
          api("/graph"),
        ]);
        setTypes(Object.fromEntries(t.node_types.map((x) => [x.id, x])));
        setSession(s);
        documentUpdate(
          d.graph
            ? d
            : await resumeOrStart({
                session: () => api("/session"),
                follow: followJob,
                start: async () => {
                  const current = await api("/graph");
                  return current.graph ? current : api("/graph/load", {
                    path: s.graph_path || "strategies/tsmom_tx_mtx/graph.json",
                    expected_revision: current.revision,
                  });
                },
              }),
        );
        const l = await api("/layout");
        setLayout(l.positions);
        if (s.active_job) await followJob(s.active_job);
        await refresh();
        scheduler.current.start();
      } catch (e) {
        await handleError(e);
      } finally {
        setRecovering(false);
      }
    })();
    return () => {
      mounted.current = false;
      scheduler.current?.dispose();
    };
  }, []);
  function change(id, key, value) {
    if (submittingRef.current || (active.current && !active.current.preview))
      return;
    if (pendingRevision.current === null) pendingRevision.current = docRef.current.revision;
    pendingUpdates.current[id] = {
      ...pendingUpdates.current[id],
      [key]: value,
    };
    const d = docRef.current;
    const graph = {
      ...d.graph,
      nodes: d.graph.nodes.map((n) =>
        n.id === id ? { ...n, params: { ...n.params, [key]: value } } : n,
      ),
    };
    documentUpdate({ ...d, graph, dirty: true });
    setStates((s) =>
      Object.fromEntries(
        Object.entries(s).map(([k, v]) => [
          k,
          column(graph.nodes.find((n) => n.id === k)) >= 3
            ? { ...v, status: "stale" }
            : v,
        ]),
      ),
    );
    setEstimate(null);
    scheduler.current.schedule(id, key, value);
  }
  async function run() {
    if (submittingRef.current || scheduler.current?.pending || active.current)
      return;
    submittingRef.current = true;
    setSubmitting(true);
    try {
      setError("");
      if (!estimate) return;
      const j = await api("/run", {
        expected_backtest_key: estimate.backtest_key,
        expected_revision: estimate.revision,
      });
      await followJob(j);
    } catch (e) {
      await handleError(e);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }
  async function cancel() {
    if (!active.current) return;
    try {
      await api("/jobs/" + active.current.id + "/cancel", {});
    } catch (e) {
      setError(e.message);
    }
  }
  const locked = recovering || submitting || Boolean(job && !job.preview),
    busy = locked || Boolean(job) || previewState !== "idle";
  const autoPositions = useMemo(() => {
    const result = Object.create(null);
    for (let c = 0; c < 5; c++) {
      let y = 70;
      for (const n of (doc?.graph?.nodes || [])
        .filter((n) => column(n) === c)
        .sort(
          (a, b) => (own(positions, a.id)?.y || 0) - (own(positions, b.id)?.y || 0),
        )) {
        result[n.id] = { x: 24 + c * STAGE_WIDTH, y };
        y += (own(measured, n.id)?.height || 200) + 16;
      }
    }
    return result;
  }, [doc?.graph?.nodes, measured]);
  const missing = unavailable(doc?.graph, types);
  const nodes = useMemo(
    () =>
      doc?.graph?.nodes.map((n) => ({
        id: n.id,
        type: "instrument",
        measured: own(measured, n.id),
        position: own(layout, n.id) ||
          own(autoPositions, n.id) || { x: 24 + column(n) * STAGE_WIDTH, y: 75 },
        dragHandle: ".node-title",
        data: {
          node: n,
          type: own(types, n.type) || { inputs: {}, outputs: {}, params: {} },
          state: own(missing, n.id)
            ? { ...own(states, n.id), status: "not_ready", message: own(missing, n.id) }
            : own(states, n.id),
          change,
          locked,
          dragType,
          inspect: setSelected,
          progress: job?.progress,
        },
      })) || [],
    [
      doc,
      layout,
      autoPositions,
      measured,
      types,
      states,
      locked,
      dragType,
      job,
    ],
  );
  useEffect(() => {
    if (!nodes.length || nodes.some((n) => !own(measured, n.id))) return;
    const timer = setTimeout(() => {
      fitInitial.current(Boolean(flow.current), fitAll);
    }, 100);
    return () => clearTimeout(timer);
  }, [measured, nodes]);
  function fitAll() {
      if (!nodes.length) return;
      const right = Math.max(5 * STAGE_WIDTH + 20, ...nodes.map((n) => n.position.x + 240));
      const bottom =
        Math.max(
          ...nodes.map((n) => n.position.y + (own(measured, n.id)?.height || 200)),
        ) + 20;
      flow.current?.fitBounds(
        { x: 0, y: 0, width: right, height: bottom },
        { padding: 0.025, duration: 0 },
      );
  }
  const lane = useMemo(() => {
    const boxes = Object.create(null);
    for (const n of nodes) {
      const ports = Object.keys(own(types, n.data.node.type)?.outputs || {});
      boxes[n.id] = {
        x: n.position.x, y: n.position.y,
        width: own(measured, n.id)?.width || 240,
        // Output handles sit below the title in declaration order.
        portY: (port) => 48 + Math.max(0, ports.indexOf(port)) * 20,
      };
    }
    return edgeLanes(doc?.graph?.edges || [], boxes);
  }, [nodes, measured, types, doc?.graph?.edges]);
  const edges =
    doc?.graph?.edges.map((e) => ({
      id: edgeId(e),
      source: e.from[0],
      sourceHandle: e.from[1],
      target: e.to[0],
      targetHandle: e.to[1],
      selected: selectedEdges.has(edgeId(e)),
      type: "lane",
      data: { laneX: lane(e) },
      animated: own(states, e.to[0])?.status === "running",
      style: {
        stroke: portColor(
          own(own(types, doc.graph.nodes.find((n) => n.id === e.from[0])?.type)?.outputs, e.from[1]),
        ),
      },
    })) || [];
  function valid(c) {
    const s = doc.graph.nodes.find((n) => n.id === c.source),
      t = doc.graph.nodes.find((n) => n.id === c.target),
      a = own(own(types, s?.type)?.outputs, c.sourceHandle),
      b = own(own(types, t?.type)?.inputs, c.targetHandle);
    if (a && b && !compatible(a, b))
      queueMicrotask(() => setConnectionError(`型別不符：${a} → ${b}`));
    return compatible(a, b);
  }
  async function editEdges(next) {
    if (submittingRef.current || scheduler.current?.pending || active.current) return;
    const current = docRef.current;
    try {
      await cancelPreview();
      documentUpdate(
        await api("/graph", {
          graph: { ...current.graph, edges: next },
          expected_revision: current.revision,
        }),
      );
      await refresh();
      scheduler.current.start();
    } catch (e) {
      await handleError(e);
    }
  }
  function connect(c) {
    if (!valid(c)) return;
    const current = docRef.current.graph.edges.filter(
      (e) => !(e.to[0] === c.target && e.to[1] === c.targetHandle),
    );
    editEdges([
      ...current,
      { from: [c.source, c.sourceHandle], to: [c.target, c.targetHandle] },
    ]);
  }
  async function save() {
    if (submittingRef.current || scheduler.current?.pending || active.current) return;
    try {
      documentUpdate(await api("/graph/save", { expected_revision: docRef.current.revision }));
      await refresh();
    } catch (e) {
      await handleError(e);
    }
  }
  async function selectStrategy(id) {
    if (!id || id === docRef.current?.graph?.strategy) return;
    if (submittingRef.current || scheduler.current?.pending || active.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    try {
      await cancelPreview();
      const d = await api("/strategies/select", { id, expected_revision: docRef.current.revision });
      documentUpdate(d);
      setSelected(null);
      setShowReplay(false);
      setWarnings([]);
      const l = await api("/layout");
      setLayout(l.positions);
      setMeasured({});
      fitInitial.current = initialFit();
      await refresh();
      scheduler.current.start();
    } catch (e) {
      await handleError(e);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }
  async function load() {
    if (submittingRef.current || scheduler.current?.pending || active.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    const expectedRevision = docRef.current.revision;
    try {
      await cancelPreview();
      const d = await api("/graph/from-sidecar", { path: sidecar, expected_revision: expectedRevision });
      documentUpdate(d);
      setWarnings(d.warnings || []);
      setShowLoad(false);
      await refresh();
      scheduler.current.start();
    } catch (e) {
      await handleError(e);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }
  return (
    <main>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">▥</span>
          <b>{doc?.graph?.strategy || "Live Strategy Graph"}</b>
        </div>
        <StrategyMenu current={doc?.graph?.strategy} disabled={busy} onSelect={selectStrategy} />
        <span
          className="hash mono"
          data-testid="graph-hash"
          title={doc?.graph_hash}
        >
          {doc?.graph_hash?.slice(0, 9) || "—"}
        </span>
        {doc?.dirty && (
          <span className="dirty" data-testid="dirty">
            ● 未存檔
          </span>
        )}
        <button disabled={busy} onClick={save}>
          存檔
        </button>
        <button disabled={busy} onClick={() => setShowLoad((v) => !v)}>
          從產出載入
        </button>
        <button data-testid="results" onClick={() => setShowResults(true)}>
          結果
        </button>
        {doc?.graph?.nodes?.some((n) => n.type === "agent.llm_view") && (
          <button data-testid="replay" onClick={() => setShowReplay(true)}>
            回放
          </button>
        )}
        <button data-testid="fsm" onClick={() => setShowFsm(true)}>
          形式化驗證
        </button>
        <a className="nav-link" data-testid="agent-trader-link" href="/agent-trader">
          Agent 交易員
        </a>
        {session.fixture && (
          <span className="fixture" data-testid="fixture-banner">
            FIXTURE 資料・獨立 ledger
          </span>
        )}
        <div className={`n-meter ${flash ? "flash" : ""}`}>
          <div>
            selection N
            <strong data-testid="selection-n">
              {ledger.selection_n ?? "—"}
            </strong>
          </div>
          <div>
            E[max SR]<strong>{fmt(ledger.expected_max_sharpe)}</strong>
          </div>
          <div>
            本次 +N<strong>{ledger.session_n ?? "—"}</strong>
          </div>
        </div>
        <button
          className="run"
          data-testid="run"
          disabled={busy || !estimate}
          onClick={run}
        >
          <span data-testid="estimate">
            {Object.keys(missing).length
              ? "未就緒：" + Object.values(missing)[0]
              : estimateText(estimate)}
          </span>
        </button>
        {job && (
          <button className="cancel" data-testid="cancel" onClick={cancel}>
            取消
          </button>
        )}
      </header>
      {showLoad && (
        <div className="loadbar">
          <label>
            Sidecar 相對路徑{" "}
            <input
              value={sidecar}
              onChange={(e) => setSidecar(e.target.value)}
              placeholder=".graph-runs/….validation.json"
            />
          </label>
          <button disabled={busy} onClick={load}>
            載入圖快照
          </button>
        </div>
      )}
      {warnings.map((w, i) => (
        <div className="warning-banner" key={i}>
          ⚠ {w}
        </div>
      ))}
      {error && (
        <div className="error-banner" role="alert">
          × {error}
          <button onClick={() => setError("")}>關閉</button>
        </div>
      )}
      {connectionError && (
        <div
          className="connection-error"
          role="alert"
          data-testid="connection-error"
        >
          {connectionError}
          <button onClick={() => setConnectionError("")}>×</button>
        </div>
      )}
      <div className={`workspace ${selected ? "with-drawer" : ""}`}>
        <div className="canvas">
          <ReactFlow
            onInit={(instance) => {
              flow.current = instance;
            }}
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            minZoom={0.35}
            maxZoom={1.6}
            nodesConnectable={!busy}
            nodesDraggable={!locked}
            nodesDeletable={false}
            deleteKeyCode={["Backspace", "Delete"]}
            onEdgesChange={(changes) => {
              const selection = changes.filter((c) => c.type === "select");
              if (selection.length) setSelectedEdges((previous) => {
                const next = new Set(previous);
                for (const change of selection) {
                  if (change.selected) next.add(change.id);
                  else next.delete(change.id);
                }
                return next;
              });
            }}
            onNodesChange={(changes) => {
              const dims = changes.filter((c) => c.type === "dimensions");
              if (dims.length)
                setMeasured((prev) => ({
                  ...prev,
                  ...Object.fromEntries(dims.map((c) => [c.id, c.dimensions])),
                }));
              const moved = changes.filter(
                (c) => c.type === "position" && c.position,
              );
              if (moved.length)
                setLayout((prev) => ({
                  ...prev,
                  ...Object.fromEntries(moved.map((c) => [c.id, c.position])),
                }));
            }}
            onNodeDragStop={async (_, n) => {
              const next = {
                ...Object.fromEntries(nodes.map((x) => [x.id, x.position])),
                [n.id]: n.position,
              };
              setLayout(next);
              try {
                await api("/layout", { positions: next });
              } catch (e) {
                setError(e.message);
              }
            }}
            onConnect={connect}
            isValidConnection={valid}
            onConnectStart={(_, p) => {
              setConnectionError("");
              const n = doc.graph.nodes.find((x) => x.id === p.nodeId);
              setDragType(
                own(own(types, n.type)?.[p.handleType === "source" ? "outputs" : "inputs"], p.handleId),
              );
            }}
            onConnectEnd={(event) => {
              const el = event.target?.closest?.('[data-testid^="handle-"]');
              if (el && dragType) {
                const t = el.parentElement?.querySelector("span")?.textContent;
                if (t && !compatible(dragType, t))
                  setConnectionError(`型別不符：${dragType} → ${t}`);
              }
              setDragType(null);
            }}
            onEdgesDelete={(deleted) =>
              editEdges(
                doc.graph.edges.filter(
                  (edge) => !deleted.some((e) => e.id === edgeId(edge)),
                ),
              )
            }
          >
            <Background color="var(--border)" gap={18} size={1} />
            <ViewportPortal>
              <div className="stage-bands">
                {stages.map((s, i) => (
                  <div
                    className="stage-band"
                    style={{ left: i * STAGE_WIDTH, width: STAGE_WIDTH }}
                    key={s}
                  >
                    <span className="stage-label">
                      <small>0{i + 1}</small>
                      {s}
                    </span>
                  </div>
                ))}
                <div className="n-boundary" style={{ left: 3 * STAGE_WIDTH + 2 }}>
                  <span>上游預覽區・不計 N</span>
                  <span>試驗區・每個新組態 N+1</span>
                </div>
              </div>
            </ViewportPortal>
            <Controls showInteractive={false} showFitView={false}>
              <button className="react-flow__controls-button" aria-label="全圖" title="全圖" onClick={fitAll}>⊡</button>
            </Controls>
          </ReactFlow>
        </div>
        {selected && (
          <Drawer
            id={selected}
            close={() => setSelected(null)}
            revision={revision}
          />
        )}
      </div>
      {showReplay && <Replay close={() => setShowReplay(false)} revision={revision} />}
      {showFsm && <Fsm close={() => setShowFsm(false)} revision={revision} />}
      {showResults && (
        <Results strategy={doc?.graph?.strategy} close={() => setShowResults(false)} revision={revision} />
      )}
      <footer>
        <span>
          LIVE STRATEGY GRAPH <b>研究量測台</b>
        </span>
        <span data-testid="preview-state">{previewState}</span>
        <span>
          {job
            ? job.preview ? "◉ 上游預覽中・不計 N" : "◉ 回測執行中"
            : recovering ? "◉ 接手工作狀態中"
            : previewState === "idle"
              ? "○ 就緒・僅明確執行會產生試驗"
              : "◉ 上游預覽中・不計 N"}
        </span>
        <span>拖曳接線 · 點選節點檢視 · Delete 刪除選取接線</span>
      </footer>
    </main>
  );
}
// ?view=fsm：桌面版內嵌用的唯讀頁。只載入形式化驗證檢視，只發 GET /api/fsm，不載入畫布、不輪詢、不帶任何寫入功能。
function FsmEmbed() {
  return (
    <div className="fsm-embed" data-testid="fsm-embed">
      <FsmBody revision={0} />
    </div>
  );
}
if (typeof document !== "undefined" && document.getElementById("root")) {
  const embed = new URLSearchParams(window.location.search).get("view") === "fsm";
  createRoot(document.getElementById("root")).render(embed ? <FsmEmbed /> : <App />);
}
