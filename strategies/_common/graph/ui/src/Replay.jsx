import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, fmt } from "./model";

const PAD = { left: 64, right: 16, top: 10, bottom: 22 };
const GAP = 14;
const HEIGHT_KEY = "replay-chart-height";
const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(2)}%`);
const dirText = { 1: "做多", "-1": "做空", 0: "空手" };
const num = (v, d = 0) => (v == null ? "—" : Number(v).toLocaleString("zh-TW", { maximumFractionDigits: d }));

function extent(values, pad = 0.04) {
  const finite = values.filter((v) => v != null && Number.isFinite(v));
  if (!finite.length) return [0, 1];
  let lo = Math.min(...finite), hi = Math.max(...finite);
  if (lo === hi) { lo -= 1; hi += 1; }
  const m = (hi - lo) * pad;
  return [lo - m, hi + m];
}

function ticks([lo, hi], count = 3) {
  const step = (hi - lo) / count;
  return Array.from({ length: count + 1 }, (_, i) => lo + step * i);
}

function linePath(days, key, x, y) {
  let d = "", open = false;
  days.forEach((day, i) => {
    const v = day[key];
    if (v == null) { open = false; return; }
    d += `${open ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    open = true;
  });
  return d;
}

function useWidth(ref) {
  const [width, setWidth] = useState(900);
  useEffect(() => {
    if (!ref.current || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(320, entry.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

export function ReplayChart({ data, cursor, onPick, height }) {
  const box = useRef(null);
  const W = useWidth(box);
  const [hover, setHover] = useState(null);
  const days = data.days;
  const n = Math.max(1, days.length - 1);
  const plotW = W - PAD.left - PAD.right;
  const x = (i) => PAD.left + (i / n) * plotW;
  // Panel heights scale with the user-chosen chart height.
  const inner = height - PAD.top - PAD.bottom - 2 * GAP;
  const hPrice = inner * 0.45, hEq = inner * 0.38, hPos = inner * 0.17;
  const top = PAD.top, eqTop = top + hPrice + GAP, posTop = eqTop + hEq + GAP;
  const priceRange = extent(days.map((d) => d.settle));
  const eqRange = extent(days.flatMap((d) => [d.equity, d.benchmark]));
  const yPrice = (v) => top + hPrice - ((v - priceRange[0]) / (priceRange[1] - priceRange[0])) * hPrice;
  const yEq = (v) => eqTop + hEq - ((v - eqRange[0]) / (eqRange[1] - eqRange[0])) * hEq;
  const posMid = posTop + hPos / 2;
  const firstClean = days.findIndex((d) => !d.contaminated);
  const dirtyEnd = firstClean < 0 ? days.length - 1 : firstClean;
  const index = new Map(days.map((d, i) => [d.date, i]));
  const cursorIndex = index.get(cursor);
  const folds = data.folds.map((f) => index.get(f.test_start)).filter((i) => i != null);
  const dateTicks = Array.from({ length: 6 }, (_, k) => Math.round((k / 5) * (days.length - 1)));
  const at = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const i = Math.round(((event.clientX - rect.left - PAD.left) / plotW) * n);
    return i >= 0 && i < days.length ? i : null;
  };
  const h = hover != null ? days[hover] : null;
  return (
    <div className="replay-chart-box" ref={box} style={{ height }}>
      <svg className="replay-chart" width={W} height={height} role="img" aria-label="價格、權益與部位回放圖"
           onMouseMove={(e) => setHover(at(e))} onMouseLeave={() => setHover(null)}
           onClick={(e) => { const i = at(e); if (i != null) onPick(days[i].date); }}>
        <defs>
          <pattern id="dirty" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" className="dirty-hatch" />
          </pattern>
        </defs>
        {dirtyEnd > 0 && (
          <g>
            <rect x={x(0)} y={top} width={x(dirtyEnd) - x(0)} height={posTop + hPos - top}
                  fill="url(#dirty)" className="dirty-zone" />
            <text x={x(0) + 6} y={top + 12} className="zone-label">知識截止前・已污染・不計績效</text>
          </g>
        )}
        {ticks(priceRange).map((v, k) => (
          <g key={`p${k}`}>
            <line x1={PAD.left} x2={W - PAD.right} y1={yPrice(v)} y2={yPrice(v)} className="grid" />
            <text x={PAD.left - 6} y={yPrice(v) + 3} textAnchor="end" className="tick">{num(v)}</text>
          </g>
        ))}
        {ticks(eqRange).map((v, k) => (
          <g key={`e${k}`}>
            <line x1={PAD.left} x2={W - PAD.right} y1={yEq(v)} y2={yEq(v)} className="grid" />
            <text x={PAD.left - 6} y={yEq(v) + 3} textAnchor="end" className="tick">{v.toFixed(2)}</text>
          </g>
        ))}
        {[1, 0, -1].map((v) => (
          <text key={`q${v}`} x={PAD.left - 6} y={posMid - (v * hPos) / 2 + 3} textAnchor="end" className="tick">
            {v > 0 ? "+1" : v}
          </text>
        ))}
        <text className="axis-title" transform={`translate(12 ${top + hPrice / 2}) rotate(-90)`} textAnchor="middle">價格（點）</text>
        <text className="axis-title" transform={`translate(12 ${eqTop + hEq / 2}) rotate(-90)`} textAnchor="middle">權益（起始=1）</text>
        <text className="axis-title" transform={`translate(12 ${posTop + hPos / 2}) rotate(-90)`} textAnchor="middle">部位</text>
        {folds.map((i, k) => (
          <line key={k} x1={x(i)} x2={x(i)} y1={eqTop} y2={posTop + hPos} className="fold-line" />
        ))}
        <path d={linePath(days, "settle", x, yPrice)} className="line price" />
        <path d={linePath(days, "benchmark", x, yEq)} className="line benchmark" />
        <path d={linePath(days, "equity", x, yEq)} className="line equity" />
        {days.map((d, i) => d.position ? (
          <rect key={d.date} x={x(i)} width={Math.max(1, plotW / n)}
                y={d.position > 0 ? posMid - (Math.min(1, d.position) * hPos) / 2 : posMid}
                height={(hPos / 2) * Math.min(1, Math.abs(d.position))}
                className={d.position > 0 ? "pos long" : "pos short"} />
        ) : null)}
        <line x1={PAD.left} x2={W - PAD.right} y1={posMid} y2={posMid} className="zero" />
        {dateTicks.map((i) => (
          <text key={`d${i}`} x={x(i)} y={height - 6} textAnchor={i === 0 ? "start" : i === days.length - 1 ? "end" : "middle"}
                className="tick">{days[i]?.date}</text>
        ))}
        {cursorIndex != null && (
          <line x1={x(cursorIndex)} x2={x(cursorIndex)} y1={top} y2={posTop + hPos} className="cursor" />
        )}
        {hover != null && (
          <g className="hover">
            <line x1={x(hover)} x2={x(hover)} y1={top} y2={posTop + hPos} className="hover-line" />
            {h.settle != null && <circle cx={x(hover)} cy={yPrice(h.settle)} r={3} className="dot price" />}
            {h.equity != null && <circle cx={x(hover)} cy={yEq(h.equity)} r={3} className="dot equity" />}
            {h.benchmark != null && <circle cx={x(hover)} cy={yEq(h.benchmark)} r={3} className="dot benchmark" />}
          </g>
        )}
      </svg>
      {h && (
        <div className="replay-tooltip" style={{ left: Math.min(x(hover) + 12, W - 210), top: 8 }}>
          <b className="mono">{h.date}</b>
          <span>{h.contaminated ? "污染區間・不計績效" : h.oos ? "樣本外（OOS）" : "樣本內"}</span>
          <span>價格 <b>{num(h.settle)}</b> 點</span>
          <span>策略權益 <b>{h.equity == null ? "—" : `${h.equity.toFixed(4)}（${pct(h.equity - 1)}）`}</b></span>
          <span>買進持有 <b>{h.benchmark == null ? "—" : `${h.benchmark.toFixed(4)}（${pct(h.benchmark - 1)}）`}</b></span>
          <span>部位 <b>{h.position == null ? "—" : `${h.position > 0 ? "多" : h.position < 0 ? "空" : "空手"} ${Math.abs(h.position).toFixed(2)} 倍`}</b></span>
        </div>
      )}
    </div>
  );
}

function Splitter({ height, setHeight }) {
  const start = (event) => {
    event.preventDefault();
    const y0 = event.clientY, h0 = height;
    const move = (e) => setHeight(Math.min(Math.max(160, h0 + e.clientY - y0), Math.round(window.innerHeight * 0.75)));
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  return (
    <div className="replay-splitter" role="separator" aria-orientation="horizontal" aria-label="拖曳調整圖表高度"
         onPointerDown={start} title="拖曳調整圖表高度">
      <span />
    </div>
  );
}

function storedHeight() {
  try {
    const v = Number(window.localStorage.getItem(HEIGHT_KEY));
    return Number.isFinite(v) && v >= 160 ? v : 240;
  } catch {
    return 240;
  }
}

function Features({ features }) {
  const rows = Object.entries(features || {});
  if (!rows.length) return null;
  return (
    <table className="replay-table">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}><th>{k}</th><td className="mono">{v == null ? "—" : fmt(v)}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export function Replay({ close, revision }) {
  const [data, setData] = useState(null), [error, setError] = useState("");
  const [at, setAt] = useState(0), [playing, setPlaying] = useState(false);
  const [height, setHeight] = useState(storedHeight);
  useEffect(() => {
    try { window.localStorage.setItem(HEIGHT_KEY, String(Math.round(height))); } catch { /* per-viewer convenience only */ }
  }, [height]);
  useEffect(() => {
    let alive = true;
    api("/replay").then((d) => { if (alive) setData(d); }).catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [revision]);
  const decisions = data?.decisions || [];
  useEffect(() => {
    const first = decisions.findIndex((d) => d.called && !d.contaminated);
    setAt(first < 0 ? 0 : first);
  }, [data]);
  useEffect(() => {
    if (!playing) return undefined;
    const t = setInterval(() => setAt((i) => {
      if (i + 1 >= decisions.length) { setPlaying(false); return i; }
      return i + 1;
    }), 900);
    return () => clearInterval(t);
  }, [playing, decisions.length]);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);
  const card = decisions[at];
  const byDate = useMemo(() => new Map(decisions.map((d, i) => [d.date, i])), [decisions]);
  const pickDay = (date) => {
    let best = 0;
    decisions.forEach((d, i) => { if (d.date <= date) best = i; });
    setAt(byDate.get(date) ?? best);
  };
  return (
    <div className="replay-overlay" role="dialog" aria-label="walk-forward 回放">
      <header>
        <button className="replay-back" onClick={close} data-testid="replay-back">← 返回策略圖</button>
        <div className="replay-title">
          <small>WALK-FORWARD REPLAY</small>
          <h2>回放：資料 → 推論 → 決策 → 損益</h2>
        </div>
        <button onClick={close} aria-label="關閉回放">×</button>
      </header>
      {error && <p className="error-banner">{error}</p>}
      {data && !data.available && <p className="replay-empty">{data.reason}</p>}
      {data?.available && (
        <>
          <div className="replay-meta">
            <span>模型 <b>{data.model}</b></span>
            <span title={data.cutoff_basis}>知識截止上界 <b>{data.cutoff}</b></span>
            <span>乾淨樣本起點 <b>{data.clean_start}</b></span>
            <span>{data.anonymize ? "提示已匿名（無日期與標的名稱）" : "提示含日期與標的"}</span>
            {data.summary && (
              <>
                <span>OOS <b>{data.summary.oos_start} → {data.summary.oos_end}</b>（{data.summary.oos_days} 日）</span>
                <span>OOS Sharpe <b>{fmt(data.summary.oos_sharpe)}</b> vs 買進持有 <b>{fmt(data.summary.benchmark_sharpe)}</b></span>
                <span>{data.folds.length} 個 fold</span>
              </>
            )}
            {data.probe && (
              <span className={data.probe.leak_suspected ? "probe bad" : "probe ok"}
                    title={data.probe.rule}>
                記憶探測：{data.probe.leak_suspected ? "疑似記住歷史價格" : "未見記憶跡象"}
                （事前誤差 {fmt(data.probe.pre_cutoff?.median_abs_pct_error)}%／事後 {fmt(data.probe.post_cutoff?.median_abs_pct_error)}%）
              </span>
            )}
          </div>
          <div className="replay-legend">
            <span className="lg equity">策略權益（OOS）</span>
            <span className="lg benchmark">買進持有</span>
            <span className="lg dirty">污染區間</span>
            <span className="lg fold">fold 起點</span>
          </div>
          <ReplayChart data={data} cursor={card?.date} onPick={pickDay} height={height} />
          <Splitter height={height} setHeight={setHeight} />
          <div className="replay-controls">
            <button onClick={() => setAt((i) => Math.max(0, i - 1))} aria-label="上一個決策">◀</button>
            <button onClick={() => setPlaying((p) => !p)}>{playing ? "暫停" : "播放"}</button>
            <button onClick={() => setAt((i) => Math.min(decisions.length - 1, i + 1))} aria-label="下一個決策">▶</button>
            <input type="range" min={0} max={Math.max(0, decisions.length - 1)} value={at}
                   onChange={(e) => setAt(Number(e.target.value))} aria-label="決策日" />
            <span className="mono">{card?.date}</span>
          </div>
          <div className="replay-bottom">
          {card && (
            <div className="replay-card">
              <section>
                <h3>決策 {card.date}{card.contaminated && <em className="badge dirty">污染・不計分</em>}</h3>
                {card.called ? (
                  <>
                    <p className="decision">
                      <b>{dirText[String(card.decision?.direction)] ?? "—"}</b>
                      ・信心 {fmt(card.decision?.confidence)}
                      ・{card.period_return == null ? "樣本內（未進入 OOS，不計績效）" : `持有期間 OOS 報酬 ${pct(card.period_return)}`}
                      {card.cached && <em className="badge">快取重播</em>}
                    </p>
                    <p className="rationale">{card.decision?.rationale}</p>
                  </>
                ) : (
                  <p className="muted">此決策日在模型知識截止前，未呼叫模型。</p>
                )}
                <h3>模型看到的特徵</h3>
                <Features features={card.features} />
                <h3>檢索到的研究（決策日前已公開）</h3>
                {card.docs.length ? (
                  <ul className="docs">
                    {card.docs.map((d) => <li key={d.id}><span className="mono">{d.knowledge_time}</span> {d.title}</li>)}
                  </ul>
                ) : <p className="muted">無</p>}
              </section>
              <section>
                <h3>推論過程（CoT）</h3>
                <pre className="cot">{card.reasoning || (card.called ? "（模型未回傳推論內容）" : "—")}</pre>
                {card.prompt && (
                  <details>
                    <summary>完整提示</summary>
                    <pre className="cot">{card.prompt}</pre>
                  </details>
                )}
                {card.content && (
                  <details>
                    <summary>模型原始回答</summary>
                    <pre className="cot">{card.content}</pre>
                  </details>
                )}
              </section>
            </div>
          )}
          {data.folds.length > 0 && (
            <details className="folds">
              <summary>各 fold 的樣本內選擇（只看樣本內，不看樣本外）</summary>
              <table className="replay-table">
                <thead><tr><th>fold</th><th>樣本內</th><th>樣本外</th><th>IS Sharpe（各門檻）</th><th>選中門檻</th><th>OOS Sharpe</th><th>OOS 報酬</th></tr></thead>
                <tbody>
                  {data.folds.map((f) => (
                    <tr key={f.fold}>
                      <td>{f.fold}</td><td className="mono">{f.train_start}→{f.train_end}</td>
                      <td className="mono">{f.test_start}→{f.test_end}</td>
                      <td className="mono">{Object.entries(f.is_sharpe).map(([k, v]) => `${k}:${fmt(v)}`).join("  ")}</td>
                      <td>{f.chosen_threshold}</td><td>{fmt(f.oos_sharpe)}</td><td>{pct(f.oos_return)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
          </div>
        </>
      )}
    </div>
  );
}
