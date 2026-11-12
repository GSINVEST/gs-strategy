// Mirrors strategies/_common/compose/types.py (Python stays authoritative):
// "Base[args]" feeds an input "Base", the same "Base[args]", or "Base[*]".
const TYPE = /^([A-Za-z][A-Za-z0-9_]*)(?:\[([A-Za-z0-9_*.,\- ]*)\])?$/;
export const parseType = (t) => {
  const m = TYPE.exec(String(t ?? "").trim());
  if (!m) return null;
  if (m[2] === undefined) return { base: m[1], args: [] };
  const args = m[2].split(",").map((a) => a.trim());
  return args.some((a) => !a) ? null : { base: m[1], args };
};
export const baseType = (t) => parseType(t)?.base ?? t;
export const compatible = (source, target) => {
  const out = parseType(source), inp = parseType(target);
  if (!out || !inp || out.base !== inp.base) return false;
  if (!inp.args.length) return true;
  return inp.args.length === out.args.length &&
    inp.args.every((want, i) => want === "*" || want === out.args[i]);
};
export const own = (object, key) =>
  object != null && Object.hasOwn(object, key) ? object[key] : undefined;
export function unavailable(graph, types) {
  const missing = Object.create(null);
  if (!graph) return missing;
  for (const n of graph.nodes) {
    const required = Object.keys(own(types, n.type)?.inputs || {}).filter(
      (port) => !graph.edges.some((e) => e.to[0] === n.id && e.to[1] === port),
    );
    if (required.length) missing[n.id] = "缺 " + required.join(", ");
  }
  let changed = true;
  while (changed) {
    changed = false;
    for (const e of graph.edges) {
      if (missing[e.from[0]] && !missing[e.to[0]]) {
        missing[e.to[0]] = `上游 ${e.from[0]} 未就緒：${missing[e.from[0]]}`;
        changed = true;
      }
    }
  }
  return missing;
}
export const estimateText = (e) =>
  !e
    ? "執行・等待預判"
    : e.already_recorded
      ? "執行・快取重播，N 不變"
      : `執行・N ${e.selection_n}→${e.next_selection_n}`;
// One hue per port type so every wire can be told apart; port dots use the same map.
export const TYPE_COLORS = {
  Bars: "#3987e5", ContinuousBars: "#5b9cf0", PriceBars: "#3987e5",
  MarketView: "#22b8cf", Docs: "#b084f5",
  Score: "#199e70", Sigma: "#2fb5a0", Direction: "#3fb950",
  RawWeights: "#8bc34a", Weights: "#a5d65a", Signals: "#3fb950",
  CostModel: "#a1887f",
  Returns: "#d95926", Positions: "#e8a33d", WalkForward: "#f06292",
  LedgerN: "#9aa7b8", Report: "#f2cc60", Facts: "#7aa2f7",
  Prescription: "#c792ea", ProbeReport: "#ff6b6b",
  StrategyBundle: "#3987e5", StrategySpec: "#b084f5", RunInfo: "#9aa7b8", MonteCarlo: "#ffb86c",
};
export const portColor = (type) => TYPE_COLORS[baseType(type)] || "var(--muted)";

// Lane routing: every edge turns in the column gap just left of its target, and
// each (gap, source port) gets its own vertical lane, so fan-outs share one trunk
// and different wires never overlap in the gap.
export function edgeLanes(edges, boxes) {
  const lanes = new Map();
  const byGap = new Map();
  for (const e of edges) {
    const s = boxes[e.from[0]], t = boxes[e.to[0]];
    if (!s || !t || t.x <= s.x) continue;
    const gap = Math.round(t.x);
    const key = `${gap}|${e.from[0]}|${e.from[1]}`;
    if (!byGap.has(gap)) byGap.set(gap, new Map());
    const group = byGap.get(gap);
    if (!group.has(key)) group.set(key, { key, y: s.y + s.portY(e.from[1]), left: s.x + s.width });
  }
  for (const [gap, group] of byGap) {
    const list = [...group.values()].sort((a, b) => a.y - b.y);
    const start = Math.max(...list.map((l) => l.left)) + 6, end = gap - 6;
    const width = Math.max(4, end - start);
    list.forEach((l, i) => lanes.set(l.key, start + (width * (i + 1)) / (list.length + 1)));
  }
  return (e) => {
    const t = boxes[e.to[0]];
    return t ? lanes.get(`${Math.round(t.x)}|${e.from[0]}|${e.from[1]}`) : undefined;
  };
}
export const portShape = (type) =>
  ["CostModel"].includes(baseType(type))
    ? "square"
    : ["LedgerN", "Report", "Facts", "Prescription", "ProbeReport", "MonteCarlo"].includes(baseType(type))
      ? "diamond"
      : "circle";
export const names = {
  bars: "期貨資料",
  continuous: "連續價格",
  momentum: "動能特徵",
  volatility: "波動估計",
  direction: "方向訊號",
  sizing: "波動目標",
  cap: "總曝險上限",
  cost: "交易成本",
  backtest: "策略回測",
  ledger: "試驗計數",
  report: "績效檢定",
  facts: "統計事實",
  resolve: "統計處方",
  data: "QUANTDATA 期貨",
  view: "市場特徵快照",
  docs: "研究檢索（RAG）",
  agent: "LLM 判斷",
  probe: "記憶探測",
  bundle: "策略池 bundle",
  spec: "策略宣告",
  montecarlo: "蒙地卡羅重抽",
};
// Column pitch: card width (~240px) plus a gap wide enough for routed wire lanes.
export const STAGE_WIDTH = 320;
export const stages = ["資料", "特徵", "訊號與部位", "回測", "驗證與檢定"];
export const column = (n) =>
  n.type.startsWith("data.")
    ? 0
    : /^(feature|rag)\./.test(n.type)
      ? 1
      : /^(signal|sizing|cost|agent)\./.test(n.type)
        ? 2
        : /^(backtest|ledger)\./.test(n.type)
          ? 3
          : 4;
export const positions = {
  bars: { x: 24, y: 75 },
  continuous: { x: 24, y: 395 },
  momentum: { x: 298, y: 75 },
  volatility: { x: 298, y: 330 },
  direction: { x: 572, y: 75 },
  sizing: { x: 572, y: 265 },
  cap: { x: 572, y: 475 },
  cost: { x: 572, y: 650 },
  backtest: { x: 846, y: 120 },
  ledger: { x: 846, y: 465 },
  report: { x: 1120, y: 75 },
  facts: { x: 1120, y: 295 },
  resolve: { x: 1120, y: 690 },
};
export async function api(path, body) {
  const r = await fetch(
    "/api" + path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data = await r.json();
  if (!r.ok) {
    const message = data.message || data.error;
    throw Object.assign(new Error(
      typeof message === "string" && /[\u3400-\u9fff]/.test(message)
        ? message : "操作失敗，請稍後重試",
    ), { status: r.status, code: data.code, revision: data.revision });
  }
  return data;
}
export const edgeId = (edge) => "edge-" + JSON.stringify([edge.from, edge.to]);
export const costText = (cost) =>
  Object.entries(cost.per_contract_cost || {})
    .sort(([a], [b]) => {
      const rank = (symbol) => symbol === "TX" ? 0 : symbol === "MTX" ? 1 : 2;
      return rank(a) - rank(b) || a.localeCompare(b, "en");
    })
    .map(([symbol, amount]) => `${symbol} ${fmt(amount)}`).join("・") +
  ` 元／口，滑價 ${fmt(cost.spread_points)} 點`;
export function initialFit() {
  let fitted = false;
  return (ready, fit) => {
    if (!ready || fitted) return;
    fitted = true;
    fit();
  };
}
export async function pollJob(id, {
  get = api, nodes, update = () => {},
  pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}) {
  let previous;
  while (true) {
    const job = await get("/jobs/" + id);
    update(job);
    const summary = JSON.stringify(Object.entries(job.node_states || {}).sort());
    if (summary !== previous || job.status !== "running") await nodes();
    previous = summary;
    if (job.status !== "running") return job;
    await pause(300);
  }
}
export async function resumeOrStart({ activeJob, follow, start, session }) {
  if (activeJob) await follow(activeJob);
  let retriedFinishedJob = false;
  while (true) {
    try {
      return await start();
    } catch (error) {
      if (error.status !== 409 || ["graph_revision_conflict", "run_estimate_conflict"].includes(error.code)) throw error;
      const current = await session();
      if (current.active_job) {
        await follow(current.active_job);
        retriedFinishedJob = false;
      } else if (!retriedFinishedJob) retriedFinishedJob = true;
      else throw error;
    }
  }
}
// One replaceable payload and one worker: never queue obsolete previews.
export function latestPreview({
  cancel,
  apply,
  preview,
  onState = () => {},
  onError = () => {},
  delay = 300,
}) {
  let timer,
    pending = Object.create(null),
    busy = false,
    generation = 0,
    disposed = false;
  async function drain() {
    timer = null;
    if (busy || disposed) return;
    busy = true;
    const current = generation;
    const edits = pending;
    pending = Object.create(null);
    onState("running");
    try {
      await cancel();
      if (disposed) return;
      await apply(edits);
      if (current === generation) await preview();
    } catch (e) {
      await onError(e);
    } finally {
      busy = false;
      if (Object.keys(pending).length) {
        clearTimeout(timer);
        timer = setTimeout(drain, delay);
      } else onState("idle");
    }
  }
  return {
    schedule(id, key, value) {
      pending[id] = { ...pending[id], [key]: value };
      generation++;
      if (busy) Promise.resolve(cancel()).catch(onError);
      clearTimeout(timer);
      onState("debouncing");
      timer = setTimeout(drain, delay);
    },
    start() {
      clearTimeout(timer);
      onState("debouncing");
      timer = setTimeout(drain, delay);
    },
    reset() {
      generation++;
      pending = Object.create(null);
      clearTimeout(timer);
      timer = null;
      if (!busy) onState("idle");
    },
    dispose() {
      disposed = true;
      clearTimeout(timer);
    },
    get pending() {
      return Boolean(timer) || busy || Object.keys(pending).length > 0;
    },
  };
}
export function frame(value) {
  if (value?.data && value?.columns) return value;
  if (value?.values) return frame(value.values);
  if (value && typeof value === "object") {
    for (const v of Object.values(value)) {
      const found = frame(v);
      if (found) return found;
    }
  }
  return null;
}
export const fmt = (v) =>
  v === null || v === undefined
    ? "—"
    : typeof v === "number"
      ? Number.isInteger(v)
        ? String(v)
        : v.toFixed(4)
      : typeof v === "object"
        ? JSON.stringify(v)
        : String(v);
