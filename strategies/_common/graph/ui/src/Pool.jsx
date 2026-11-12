import React, { useEffect, useState } from "react";
import { api, fmt } from "./model";

const ORIGIN = {
  "llm-generated": "論文自動生成（未審）",
  "gs-strategy": "gs-strategy",
  "gs-strategy-graph": "gs-strategy（組合圖）",
  external: "外部匯入",
  "manual-md": "手動匯入（MD）",
  "manual-py": "手動匯入（PY）",
  builtin: "gs-zipline-tej 範例",
};

export function StrategyMenu({ current, disabled, onSelect }) {
  const [pool, setPool] = useState(null), [error, setError] = useState("");
  const load = (refresh = false) =>
    api("/strategies" + (refresh ? "?refresh=1" : ""))
      .then((p) => { setPool(p); setError(p.pool_error || ""); })
      .catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  const groups = {};
  for (const s of pool?.strategies || []) {
    const key = (s.tags || []).includes("llm-generated") ? "llm-generated" : s.origin;
    (groups[key] ||= []).push(s);
  }
  const fixture = pool?.fixture;
  return (
    <span className="strategy-menu">
      <select data-testid="strategy-menu" value={current || ""} disabled={disabled || !pool || fixture}
              title={fixture ? "測試資料模式只能檢視目前的圖" : "切換策略池中的策略"}
              onChange={(e) => onSelect(e.target.value)}>
        {!current && <option value="">選擇策略…</option>}
        {current && !(pool?.strategies || []).some((s) => s.id === current) && <option value={current}>{current}</option>}
        {Object.entries(groups).map(([origin, list]) => (
          <optgroup key={origin} label={ORIGIN[origin] || origin}>
            {list.map((s) => (
              <option key={s.id} value={s.id} disabled={!s.selectable}>
                {s.name && s.name !== s.id ? `${s.id} — ${s.name}` : s.id}
                {s.graph === "composed" ? "（完整圖）" : "（粗圖）"}
              </option>
            ))}
          </optgroup>
        ))}
        {(pool?.factors || []).length > 0 && (
          <optgroup label={`因子池（當選股濾網回測，${pool.factors.length}）`}>
            {pool.factors.map((f) => (
              <option key={f.id} value={f.id} disabled={!f.selectable}>
                {f.name && f.name !== f.id ? `${f.id} — ${f.name}` : f.id}（粗圖）
              </option>
            ))}
          </optgroup>
        )}
      </select>
      <button className="icon" disabled={disabled} onClick={() => load(true)} title="重新掃描策略池" aria-label="重新掃描策略池">⟳</button>
      {error && <span className="pool-error" title={error}>策略池：{error}</span>}
    </span>
  );
}

function Equity({ points }) {
  if (!points?.length) return <p className="muted">沒有報酬序列（執行未產生績效）</p>;
  const W = 640, H = 120;
  const ys = points.map((p) => p[1]);
  const lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const d = points.map((p, i) => `${i ? "L" : "M"}${(i / Math.max(1, points.length - 1)) * W},${H - ((p[1] - lo) / span) * (H - 8) - 4}`).join("");
  return (
    <svg className="results-equity" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="權益曲線">
      <path d={d} />
      <text x="4" y="12">{fmt(hi)}</text>
      <text x="4" y={H - 4}>{fmt(lo)}</text>
      <text x={W - 4} y={H - 4} textAnchor="end">{points[0][0]} → {points[points.length - 1][0]}</text>
    </svg>
  );
}


function Fan({ fan, observed }) {
  if (!fan?.p50?.length) return null;
  const W = 640, H = 160, n = fan.p50.length;
  const all = [...fan.p05, ...fan.p95, ...(observed || [])];
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const x = (i) => (i / Math.max(1, n - 1)) * W;
  const y = (v) => H - 6 - ((v - lo) / span) * (H - 12);
  const band = (a, b) => fan[a].map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("") +
    fan[b].map((v, i) => `L${x(n - 1 - i)},${y(fan[b][n - 1 - i])}`).join("") + "Z";
  const line = (arr) => arr.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("");
  return (
    <svg className="results-fan" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="蒙地卡羅權益扇形圖">
      <path d={band("p05", "p95")} className="band outer" />
      <path d={band("p25", "p75")} className="band inner" />
      <path d={line(fan.p50)} className="median" />
      <text x="4" y="12">{fmt(hi)}</text>
      <text x="4" y={H - 4}>{fmt(lo)}</text>
      <text x={W - 4} y={H - 4} textAnchor="end">{fan.dates[0]} → {fan.dates[n - 1]}</text>
    </svg>
  );
}

function Dist({ label, d, pct: asPct }) {
  if (!d) return null;
  const f = (v) => (v == null ? "—" : asPct ? `${(v * 100).toFixed(1)}%` : fmt(v));
  return (
    <tr><th>{label}</th>{["p05", "p25", "p50", "p75", "p95"].map((k) => <td key={k} className="mono">{f(d[k])}</td>)}</tr>
  );
}

function ExperimentDetail({ e }) {
  const s = e.summary || {};
  if (e.kind === "codegen") {
    return (
      <section className="results-detail">
        <h3>論文自動生成策略・{s.papers} 篇・入池 {(s.admitted || []).length}・模型 {s.model}</h3>
        <p className="muted">{s.gates}；入池的策略標為「未審」，回測時才計入 N。</p>
        <table className="replay-table">
          <thead><tr><th>論文</th><th>到達關卡</th><th>策略 id</th><th>交易／減碼</th><th>原因</th></tr></thead>
          <tbody>
            {e.top_trials.map((t) => (
              <tr key={t.trial_id}>
                <td title={t.trial_id}>{t.metrics?.title}</td>
                <td className={t.stage === "admitted" ? "ok" : "bad"}>{t.stage === "admitted" ? "入池" : t.stage}</td>
                <td className="mono">{t.metrics?.strategy_id || "—"}</td>
                <td>{t.metrics?.trades ?? "—"}／{t.metrics?.reductions ?? "—"}</td>
                <td>{t.metrics?.reason || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    );
  }
  if (e.kind === "monte_carlo") {
    return (
      <section className="results-detail">
        <h3>蒙地卡羅重抽・{e.n_trials} 條路徑・平均區塊 {s.mean_block}・seed {s.seed}</h3>
        <p className="muted">{s.method}；只重抽同一條報酬序列，不產生新的績效資訊，因此不增加 N。</p>
        <table className="replay-table">
          <thead><tr><th></th><th>5%</th><th>25%</th><th>中位</th><th>75%</th><th>95%</th></tr></thead>
          <tbody>
            <Dist label="年化 Sharpe" d={s.sharpe} />
            <Dist label="最大回撤" d={s.max_drawdown} pct />
            <Dist label="CAGR" d={s.cagr} pct />
          </tbody>
        </table>
        <p>實際觀測：Sharpe <b>{fmt(s.observed?.sharpe)}</b>・MDD <b>{fmt(s.observed?.max_drawdown)}</b>・
          P(Sharpe ≤ 0) <b>{fmt(s.prob_sharpe_le_0)}</b>・P(期末虧損) <b>{fmt(s.prob_loss)}</b></p>
        <h3>權益扇形圖（5–95%、25–75%、中位）</h3>
        <Fan fan={s.fan} />
      </section>
    );
  }
  return (
    <section className="results-detail">
      <h3>MINT 錦標賽 {s.run_id}・{e.n_trials} 個 trial（DSR 試驗數 {s.n_for_dsr}，含準則變更 {s.criteria_changes}）</h3>
      <p className="muted">來源 {e.source}；依 gs-MINT trial-ledger 契約讀取，不改 gs-MINT。</p>
      <p>各階段：{Object.entries(s.stages || {}).map(([k, v]) => `${k} ${v}`).join("・")}・
        正分比例 <b>{fmt(s.score_oos_net_t?.positive_share)}</b>・PBO <b>{fmt(s.pbo)}</b>（{s.pbo_note}）</p>
      <table className="replay-table">
        <thead><tr><th></th><th>5%</th><th>25%</th><th>中位</th><th>75%</th><th>95%</th></tr></thead>
        <tbody><Dist label="OOS 淨報酬 NW-t" d={s.score_oos_net_t} /></tbody>
      </table>
      {s.champion && <p>冠軍：<span className="mono">{s.champion.champion_variant_id || s.champion.champion_trial_id || s.champion.trial_id}（NW-t {fmt(s.champion.champion_score_oos_net_t)}）</span></p>}
      <h3>分數前 {e.top_trials.length} 名 trial</h3>
      <table className="replay-table">
        <thead><tr><th>trial</th><th>階段</th><th>NW-t</th><th>候選池</th><th>方案</th></tr></thead>
        <tbody>
          {e.top_trials.map((t) => (
            <tr key={t.trial_id}><td className="mono">{t.trial_id}</td><td>{t.stage}</td><td>{fmt(t.score)}</td>
              <td>{t.metrics?.pool_id}</td><td>{[t.metrics?.scheme, t.metrics?.leg, t.metrics?.weighting].filter(Boolean).join(" ")}</td></tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Experiments({ strategy, all, revision }) {
  const [rows, setRows] = useState(null), [detail, setDetail] = useState(null), [error, setError] = useState("");
  useEffect(() => {
    api("/experiments?limit=200" + (all || !strategy ? "" : "&strategy=" + encodeURIComponent(strategy)))
      .then((r) => setRows(r.experiments)).catch((e) => setError(e.message));
  }, [strategy, all, revision]);
  const open = (id) => api("/experiments/" + encodeURIComponent(id)).then(setDetail).catch((e) => setError(e.message));
  return (
    <>
      {error && <p className="error-banner">{error}</p>}
      {rows && !rows.length && (
        <p className="muted">尚無實驗。含「蒙地卡羅」節點的圖每次執行都會自動寫入；MINT 錦標賽可用
          <span className="mono"> python -m strategies._common.mint_ledger import &lt;trial_ledger.jsonl&gt;</span> 匯入。</p>
      )}
      {rows && rows.length > 0 && (
        <table className="replay-table results-table">
          <thead><tr><th>時間（UTC）</th><th>類型</th><th>策略／來源</th><th>試驗數</th><th>重點</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.experiment_id} className={detail?.experiment_id === r.experiment_id ? "active" : ""} onClick={() => open(r.experiment_id)}>
                <td className="mono">{r.created_at.slice(0, 19).replace("T", " ")}</td>
                <td>{{ monte_carlo: "蒙地卡羅", mint_tournament: "MINT 錦標賽", codegen: "論文生成" }[r.kind] || r.kind}</td>
                <td>{r.strategy || r.source?.split("/").slice(-2).join("/")}</td>
                <td>{r.n_trials}</td>
                <td className="mono">{r.kind === "monte_carlo"
                  ? `Sharpe 中位 ${fmt(r.headline.sharpe_p50)}（${fmt(r.headline.sharpe_p05)}～${fmt(r.headline.sharpe_p95)}），P(SR≤0)=${fmt(r.headline.prob_sharpe_le_0)}`
                  : r.kind === "codegen"
                    ? `論文 ${r.headline.papers}，入池 ${r.headline.admitted}`
                    : `N(DSR)=${r.headline.n_for_dsr}，最佳 t=${fmt(r.headline.best_score)}，PBO=${fmt(r.headline.pbo)}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {detail && <ExperimentDetail e={detail} />}
    </>
  );
}

export function Results({ strategy, close, revision }) {
  const [rows, setRows] = useState(null), [all, setAll] = useState(false), [tab, setTab] = useState("runs");
  const [detail, setDetail] = useState(null), [error, setError] = useState("");
  useEffect(() => {
    api("/results?limit=200" + (all || !strategy ? "" : "&strategy=" + encodeURIComponent(strategy)))
      .then((r) => setRows(r.runs)).catch((e) => setError(e.message));
  }, [strategy, all, revision]);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);
  const open = (id) => api("/results/" + id).then(setDetail).catch((e) => setError(e.message));
  return (
    <div className="replay-overlay results-overlay" role="dialog" aria-label="回測結果紀錄">
      <header>
        <button className="replay-back" onClick={close}>← 返回策略圖</button>
        <div className="replay-title">
          <small>BACKTEST RESULTS</small>
          <h2>回測結果紀錄（data/backtests.sqlite）</h2>
        </div>
        <label className="results-all">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> 所有策略
        </label>
      </header>
      <div className="results-tabs" role="tablist">
        <button role="tab" aria-selected={tab === "runs"} className={tab === "runs" ? "on" : ""} onClick={() => setTab("runs")}>執行紀錄</button>
        <button role="tab" aria-selected={tab === "experiments"} className={tab === "experiments" ? "on" : ""} onClick={() => setTab("experiments")}>實驗（蒙地卡羅／MINT）</button>
      </div>
      {error && <p className="error-banner">{error}</p>}
      <div className="replay-bottom">
        {tab === "experiments" && <Experiments strategy={strategy} all={all} revision={revision} />}
        {tab === "runs" && rows && !rows.length && <p className="muted">尚無紀錄。每次按「執行」都會自動寫入一筆，包含被拒絕的執行。</p>}
        {tab === "runs" && rows && rows.length > 0 && (
          <table className="replay-table results-table">
            <thead>
              <tr><th>時間（UTC）</th><th>策略</th><th>執行者</th><th>狀態</th><th>試驗</th><th>N</th><th>模型／引擎</th>
                <th>Sharpe</th><th>DSR</th><th>MDD</th><th>CAGR</th><th>天數</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.run_id} className={detail?.run_id === r.run_id ? "active" : ""} onClick={() => open(r.run_id)}>
                  <td className="mono">{r.recorded_at.slice(0, 19).replace("T", " ")}</td>
                  <td>{r.strategy}</td>
                  <td title={r.actor || "本機"}>{r.actor ? r.actor.split("@")[0] : "本機"}</td>
                  <td className={r.status === "complete" ? "ok" : "bad"} title={r.message || ""}>{r.status === "complete" ? "完成" : "失敗／拒絕"}</td>
                  <td>{r.new_trial ? "新試驗 +1" : r.backtest_key ? "快取重播" : "—"}</td>
                  <td>{r.selection_n ?? "—"}</td>
                  <td>{r.model || (r.engine || "").replace("backtest.", "")}</td>
                  <td>{fmt(r.sharpe)}</td><td>{fmt(r.dsr)}</td><td>{fmt(r.max_drawdown)}</td><td>{fmt(r.cagr)}</td>
                  <td>{r.n_days ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "runs" && detail && (
          <section className="results-detail">
            <h3>{detail.strategy}・{detail.recorded_at.slice(0, 19).replace("T", " ")}</h3>
            {detail.message && <p className="bad">{detail.message}</p>}
            <Equity points={detail.equity} />
            <p className="mono muted">backtest_key {detail.backtest_key || "—"}・graph {String(detail.graph_hash || "—").slice(0, 12)}</p>
            {detail.details?.walk_forward && (
              <p>walk-forward：OOS {detail.details.walk_forward.oos_start} → {detail.details.walk_forward.oos_end}，
                {detail.details.walk_forward.folds?.length} 個 fold，買進持有 Sharpe {fmt(detail.details.walk_forward.benchmark_sharpe)}</p>
            )}
            {detail.details?.report?.warnings?.map((w, i) => <p key={i} className="warning-text">⚠ {w}</p>)}
            <details>
              <summary>參數快照</summary>
              <pre className="cot">{JSON.stringify(detail.params, null, 2)}</pre>
            </details>
          </section>
        )}
      </div>
    </div>
  );
}
