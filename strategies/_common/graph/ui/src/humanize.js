// 把節點產出的機器 metadata（StrategyBundle / StrategySpec / 其他未知 JSON）
// 轉成人看得懂的「標題＋一句話＋欄位表＋標籤」。純函式，不碰 DOM，方便測試。

const ASSET = { equity: "股票", futures: "期貨", option: "選擇權", etf: "ETF", fx: "外匯", crypto: "加密貨幣" };
const CALENDAR = { TEJ_XTAI: "台灣證交所（XTAI）交易日曆", XTAI: "台灣證交所（XTAI）交易日曆", TEJ_XTAF: "台灣期交所（XTAF）交易日曆" };
const ORIGIN = { external: "外部匯入", internal: "內部開發", manual: "人工撰寫", generated: "自動產生", llm: "LLM 產生" };
const BUNDLE = { tquant: "TEJ 日線資料包（tquant）", tquant_future: "TEJ 期貨資料包（tquant_future）" };
const PARAM = {
  fast_window: "快線週期",
  slow_window: "慢線週期",
  position_shares: "持有股數",
  symbol: "標的代號",
  lookback: "回看期數",
  threshold: "門檻",
  stop_loss: "停損",
  take_profit: "停利",
};
const PARAM_UNIT = { fast_window: "根 K 棒", slow_window: "根 K 棒", lookback: "根 K 棒", position_shares: "股" };
const GENERIC = {
  id: "代號",
  name: "名稱",
  description: "說明",
  version: "版本",
  start: "起日",
  end: "迄日",
  capital_base: "起始資金",
  strategy_id: "策略代號",
  bundle_version: "資料版本",
};

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const short = (s, n = 8) => (typeof s === "string" && s.length > n ? `${s.slice(0, n)}…` : s);
const lookup = (map, k) => (k == null ? "—" : map[k] ? `${map[k]}（${k}）` : String(k));

function oneLine(text) {
  return String(text || "").replace(/\s*\n\s*/g, " ").trim();
}

export function valueText(v, key) {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "是" : "否";
  if (typeof v === "number") {
    const n = Number.isInteger(v) ? v.toLocaleString("en-US") : String(Number(v.toPrecision(6)));
    return PARAM_UNIT[key] ? `${n} ${PARAM_UNIT[key]}` : n;
  }
  if (Array.isArray(v)) return v.length ? v.map((x) => valueText(x)).join("、") : "—";
  if (isObj(v)) return `${Object.keys(v).length} 項設定`;
  return String(v);
}

function paramRows(params) {
  return Object.entries(isObj(params) ? params : {}).map(([k, v]) => [PARAM[k] || k, valueText(v, k)]);
}

function provenance(p) {
  if (!isObj(p)) return null;
  const who = p.generator ? `${p.generator}${p.generator_version ? ` v${p.generator_version}` : ""}` : null;
  const when = p.generated_at ? String(p.generated_at).slice(0, 10) : null;
  return `${lookup(ORIGIN, p.kind)}${who ? `・${who}` : ""}${when ? `・${when}` : ""}`;
}

function bundleCard(b) {
  const m = isObj(b.meta) ? b.meta : {};
  const w = isObj(b.window) ? b.window : {};
  const rows = [
    ["資產類別", lookup(ASSET, m.asset_class)],
    ["交易日曆", lookup(CALENDAR, m.calendar)],
    ["標的", valueText(m.symbols)],
    ...paramRows(m.params),
    ["回測區間", w.start || w.end ? `${w.start || "?"} ～ ${w.end || "?"}` : "—"],
    ["起始資金", w.capital_base != null ? `${valueText(w.capital_base)} 元` : "—"],
    ["需要 TEJ 金鑰", valueText(m.requires_tej_key)],
    ["來源", lookup(ORIGIN, m.origin)],
  ];
  const prov = provenance(m.provenance);
  if (prov) rows.push(["產生方式", prov]);
  const ver = b.version || w.bundle_version;
  if (ver) rows.push(["資料版本", short(ver)]);
  return {
    kind: "bundle",
    title: m.name || m.id || "策略資料包",
    summary: oneLine(m.description),
    rows: rows.filter((r) => r[1] !== "—"),
    tags: Array.isArray(m.tags) ? m.tags : [],
    reference: m.reference || null,
  };
}

function specCard(s) {
  const p = isObj(s.params) ? s.params : {};
  // params 可能與頂層欄位重複（symbol / fast_window …），以 params 優先、其餘補齊
  const merged = { ...p };
  for (const k of ["symbol", "fast_window", "slow_window", "position_shares"]) if (merged[k] === undefined && s[k] !== undefined) merged[k] = s[k];
  const rows = [
    ["資產類別", lookup(ASSET, s.asset_class)],
    ["資料包", lookup(BUNDLE, s.bundle)],
    ["交易日曆", lookup(CALENDAR, s.calendar)],
    ["投資範圍", valueText(s.universe)],
    ...paramRows(merged),
    ["來源", lookup(ORIGIN, s.origin)],
  ];
  const prov = provenance(s.source);
  if (prov) rows.push(["產生方式", prov]);
  const desc = isObj(s.source) && isObj(s.source.inputs) ? oneLine(s.source.inputs.description) : "";
  const hasValidation = isObj(s.validation) && Object.keys(s.validation).length > 0;
  rows.push(["驗證設定", hasValidation ? "已設定" : "尚未設定"]);
  return {
    kind: "spec",
    title: "策略宣告",
    summary: desc,
    rows: rows.filter((r) => r[1] !== "—"),
    tags: Array.isArray(s.tags) ? s.tags : [],
    reference: s.reference || null,
  };
}

function genericCard(name, o) {
  const rows = [];
  for (const [k, v] of Object.entries(o)) {
    if (rows.length >= 8) break;
    rows.push([GENERIC[k] || PARAM[k] || k, valueText(v, k)]);
  }
  return { kind: "generic", title: name, summary: "", rows, tags: [], reference: null };
}

// outputs: 節點 state.outputs，形如 { StrategyBundle: {...} }；回傳 null 表示沒有可說明的內容
export function humanizeOutputs(outputs) {
  if (!isObj(outputs)) return null;
  const cards = [];
  for (const [port, val] of Object.entries(outputs)) {
    if (port === "StrategyBundle" && isObj(val)) cards.push(bundleCard(val));
    else if (port === "StrategySpec" && isObj(val)) cards.push(specCard(val));
    else if (isObj(val)) cards.push(genericCard(port, val));
    else if (val !== undefined) cards.push({ kind: "generic", title: port, summary: valueText(val), rows: [], tags: [], reference: null });
  }
  return cards.length ? cards : null;
}
