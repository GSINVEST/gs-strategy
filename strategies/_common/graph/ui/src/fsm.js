// 形式化驗證（FSM）面板的純邏輯（無 DOM）。資料契約與 genesis-deck 桌面版一致（strategy-fsm-atlas/1 的白名單版）。
// 用詞硬規則：模型檢查的 PASS 只能說「有限模型下不變量未被違反」，不得寫成已證明。
export const PASS_TEXT = "有限模型下不變量未被違反";
export const FORBIDDEN = /已證明|證明正確|保證安全|已驗證策略/;
export const BACKEND = /vllm|供應商|\bgpu\b|qwen|deepseek|nvidia/i;

export const ORIGIN = { code_derived: "程式碼推得", trader_stated: "交易員提出", template_proposed: "範本建議", model_self: "模型自檢" };
export const CONFIRM = { confirmed: "已確認", proposed_needs_human: "建議規則・待確認" };
export const ENFORCED = {
  true: "程式碼有實作這條規則",
  false: "程式碼沒有實作（規格上的建議）",
  unknown: "未確認程式碼是否實作",
};

const PASSISH = new Set(["pass_with_bounds", "proposed_pass"]);
const WARN = new Set(["needs_review", "inconclusive"]);

export const VERDICT = {
  pass_with_bounds: { icon: "✔", label: PASS_TEXT, cls: "ok" },
  proposed_pass: { icon: "○", label: `${PASS_TEXT}（建議規則・待確認）`, cls: "mute" },
  counterexample: { icon: "✖", label: "發現反例", cls: "bad" },
  mutation_not_caught: { icon: "✖", label: "負控制未被抓到（檢查可能是空洞的）", cls: "bad" },
  needs_review: { icon: "！", label: "需人審（來源可能已變動）", cls: "warn" },
  inconclusive: { icon: "！", label: "無法判定", cls: "warn" },
  vacuous: { icon: "–", label: "由表示法保證，模型沒有檢查", cls: "mute" },
  unchecked: { icon: "–", label: "未檢查", cls: "mute" },
};
export const verdictInfo = (v) => VERDICT[v] || { icon: "！", label: "不認得的狀態，視為未判定", cls: "warn" };

export function enforcedText(v) {
  return ENFORCED[String(v)] || ENFORCED.unknown;
}

/** cfg 檔名 → 約定的 key：core.cfg→core、stress-<INV>.cfg→stress:<INV>、mutation-<name>.cfg→mutation:<name> */
export function cfgKey(file) {
  const m = /^(core|stress|mutation)(?:-(.+))?\.cfg$/.exec(String(file || ""));
  if (!m) return null;
  return m[2] ? `${m[1]}:${m[2]}` : m[1];
}

/** 一條 cfg 的整體結果：任何反例＝cex；需人審／無法判定／不認得＝unknown；全部是通過類才算 pass；其餘＝unchecked */
export function cfgResult(verdicts) {
  if (verdicts.includes("counterexample")) return "cex";
  if (verdicts.some((v) => WARN.has(v) || !(PASSISH.has(v) || v === "vacuous" || v === "unchecked" || v === "mutation_not_caught"))) return "unknown";
  if (verdicts.includes("mutation_not_caught")) return "unknown";
  if (verdicts.length && verdicts.every((v) => PASSISH.has(v))) return "pass";
  return "unchecked";
}

export const RESULT = {
  pass: { icon: "✔", label: PASS_TEXT, cls: "ok" },
  cex: { icon: "✖", label: "發現反例", cls: "bad" },
  caught: { icon: "✔", label: "故意破壞的版本被抓到（檢查有效）", cls: "ok" },
  not_caught: { icon: "✖", label: "負控制未被抓到（檢查可能是空洞的）", cls: "bad" },
  unknown: { icon: "！", label: "無法判定或需人審", cls: "warn" },
  unchecked: { icon: "–", label: "未檢查", cls: "mute" },
};
export const resultInfo = (r) => RESULT[r] || RESULT.unchecked;

/** 每個 cfg 一列（core／stress:<INV>／mutation:<name>） */
export function cfgRows(t) {
  const rows = [];
  const by = new Map();
  for (const inv of t.invariants || []) {
    const key = cfgKey(inv.cfg) || (inv.profile === "stress" ? `stress:${inv.id}` : null);
    if (!key) continue;
    if (!by.has(key)) {
      const r = { key, kind: key.split(":")[0], verdicts: [], distinct_states: null };
      by.set(key, r);
      rows.push(r);
    }
    const r = by.get(key);
    r.verdicts.push(inv.verdict);
    if (Number.isFinite(inv.distinct_states)) r.distinct_states = inv.distinct_states;
  }
  for (const r of rows) r.result = cfgResult(r.verdicts);
  for (const m of t.mutations || []) {
    rows.push({ key: cfgKey(m.cfg) || `mutation:${m.name}`, kind: "mutation", verdicts: [], note: m.description_zh || "",
      distinct_states: Number.isFinite(m.distinct_states) ? m.distinct_states : null,
      result: m.caught === true ? "caught" : m.caught === false ? "not_caught" : "unchecked" });
  }
  return rows;
}

/** 白話反例依【標題】切段；固定三段結尾缺任一段就 complete=false（畫面要警示，不假裝完整）。 */
export function cexParts(text) {
  const s = String(text || "");
  const re = /^【([^】\n]+)】/gm;
  const marks = [];
  let m;
  while ((m = re.exec(s))) marks.push({ title: m[1], at: m.index, end: re.lastIndex });
  const sections = marks.map((k, i) => ({ title: k.title, body: s.slice(k.end, i + 1 < marks.length ? marks[i + 1].at : s.length).trim() }));
  const lead = sections[0] || null;
  const model = sections.find((x) => /模型數字與來源/.test(x.title)) || null;
  const meaning = sections.find((x) => /代表什麼/.test(x.title)) || null;
  const source = lead && /範本建議|交易員提出|程式碼推得|模型自檢|已確認|待確認|尚待/.test(lead.body) ? lead : null;
  return { lead, source, model, meaning, complete: !!(lead && source && model && meaning) };
}

/** 資料新鮮度：沒有 generated_at＝無法判斷（不當作新鮮）；超過 stale_days＝過舊 */
export function staleInfo(view, nowMs = Date.now()) {
  const t = Date.parse(view.generated_at || "");
  if (!Number.isFinite(t)) return { known: false, stale: false };
  return { known: true, stale: nowMs - t > (view.stale_days || 7) * 86400000 };
}

/** 選預設要看的策略：目前載入的圖對應的策略優先，否則第一個。 */
export function pickTarget(view, id) {
  const targets = view?.targets || [];
  return targets.find((t) => t.id === id) || targets.find((t) => t.id === view?.current) || targets[0] || null;
}

/** 狀態圖排版：深度優先排層（從初始狀態），同層橫排；不可達的狀態放最後一排。回傳 {width,height,nodes:{id:{x,y}}}。 */
export function layoutGraph(graph, { w = 160, h = 48, gx = 90, gy = 96 } = {}) {
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  const depth = new Map();
  const queue = nodes.filter((n) => n.initial).map((n) => n.id);
  if (!queue.length && nodes.length) queue.push(nodes[0].id);
  queue.forEach((id) => depth.set(id, 0));
  for (let i = 0; i < queue.length; i += 1) {
    for (const e of edges) {
      if (e.from === queue[i] && !depth.has(e.to)) {
        depth.set(e.to, depth.get(queue[i]) + 1);
        queue.push(e.to);
      }
    }
  }
  const rest = Math.max(-1, ...depth.values()) + 1;
  for (const n of nodes) if (!depth.has(n.id)) depth.set(n.id, rest);
  const layers = [];
  for (const n of nodes) (layers[depth.get(n.id)] ||= []).push(n.id);
  const cols = Math.max(1, ...layers.filter(Boolean).map((l) => l.length));
  const width = cols * w + (cols - 1) * gx + 32;
  const pos = {};
  let row = 0;
  for (const layer of layers) {
    if (!layer) continue;
    const span = layer.length * w + (layer.length - 1) * gx;
    layer.forEach((id, i) => { pos[id] = { x: (width - span) / 2 + i * (w + gx), y: 16 + row * (h + gy), w, h }; });
    row += 1;
  }
  return { width, height: 32 + row * (h + gy) - gy, nodes: pos };
}

/** 邊的弧線：同一對節點的多條邊錯開；自環畫在節點右側。回傳 SVG path 與標籤位置。 */
export function edgePath(edge, pos, index = 0, total = 1) {
  const a = pos[edge.from], b = pos[edge.to];
  if (!a || !b) return null;
  if (edge.from === edge.to) {
    const x = a.x + a.w, y = a.y + a.h / 2, o = 26 + index * 12;
    return { d: `M${x},${y - 8} C${x + o},${y - 22} ${x + o},${y + 22} ${x},${y + 8}`, lx: x + o + 4, ly: y, anchor: "start" };
  }
  const ax = a.x + a.w / 2, ay = a.y + a.h / 2, bx = b.x + b.w / 2, by = b.y + b.h / 2;
  const dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy) || 1;
  // 法線方向固定（不隨邊的方向翻轉），來回兩條邊才會分在兩側而不重疊。
  const sign = edge.from > edge.to ? -1 : 1;
  const off = (index - (total - 1) / 2) * 40 + (total === 1 ? 0 : 0);
  const nx = (-dy / len) * sign, ny = (dx / len) * sign;
  const cx = (ax + bx) / 2 + nx * off, cy = (ay + by) / 2 + ny * off;
  const clip = (p, tx, ty) => {
    const vx = tx - (p.x + p.w / 2), vy = ty - (p.y + p.h / 2);
    const k = Math.min(vx ? p.w / 2 / Math.abs(vx) : Infinity, vy ? p.h / 2 / Math.abs(vy) : Infinity);
    return [p.x + p.w / 2 + vx * k, p.y + p.h / 2 + vy * k];
  };
  const [sx, sy] = clip(a, cx, cy), [ex, ey] = clip(b, cx, cy);
  // 標籤放在曲線中點（二次貝茲曲線 t=0.5）
  const mx = 0.25 * sx + 0.5 * cx + 0.25 * ex, my = 0.25 * sy + 0.5 * cy + 0.25 * ey;
  if (total === 1) return { d: `M${sx},${sy} Q${cx},${cy} ${ex},${ey}`, lx: mx, ly: my - 4, anchor: "middle" };
  // 來回的邊：標籤推到各自弧線的外側，靠左的邊文字向左延伸、靠右的向右延伸
  const side = off >= 0 ? 1 : -1;
  return { d: `M${sx},${sy} Q${cx},${cy} ${ex},${ey}`, lx: mx + nx * side * 8, ly: my + ny * side * 8, anchor: nx * side >= 0 ? "start" : "end" };
}

/** 用字檢查（測試用） */
export function badWords(text) {
  const out = [];
  const f = FORBIDDEN.exec(text); if (f) out.push(f[0]);
  const b = BACKEND.exec(text); if (b) out.push(b[0]);
  return out;
}
