"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> HTTP ${r.status}`);
  return r.json();
}

function showError(msg) {
  $("#err-banner").textContent = msg ? `⚠ ${msg}` : "";
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

// ---------- summary cards + source bars ----------
async function loadSummary() {
  const s = await getJSON("/api/summary");
  $("#generated-at").textContent = `更新於 ${s.generated_at} · 今日 ${s.today}`;
  $("#export-dir-hint").textContent = `匯出目標：${s.export_dir}`;
  if (s.server_started) {
    $("#server-stamp").textContent =
      `server 啟動於 ${s.server_started} · code ${s.code_rev || "?"}（改碼後需重啟 server 才生效）`;
  }

  const kindc = s.papers_by_kind || { strategy: 0, factor: 0 };
  const cards = [
    { label: "論文/報告總量", value: s.papers_total, cls: "accent" },
    { label: "策略 / 因子", value: `${kindc.strategy}/${kindc.factor}`, cls: "" },
    { label: "已下載 PDF", value: s.pdfs_downloaded ?? 0, cls: "" },
    { label: "今日 routine", value: s.runs_today, cls: "" },
    { label: "策略總數", value: s.strategies_total,
      sub: `${s.strategies_manual} 手寫 / ${s.strategies_generated} 自動`, cls: "" },
    { label: "已匯出", value: s.strategies_exported, cls: "green" },
    { label: "待匯出", value: s.strategies_pending, cls: "amber" },
  ];
  const wrap = $("#cards");
  wrap.replaceChildren();
  for (const c of cards) {
    wrap.append(el("div", { class: `card ${c.cls}` },
      el("div", { class: "label" }, c.label),
      el("div", { class: "value" },
        String(c.value),
        c.sub ? el("small", {}, ` ${c.sub}`) : null),
    ));
  }

  // source bars
  const bars = $("#source-bars");
  bars.replaceChildren();
  const max = Math.max(1, ...s.papers_by_source.map((x) => x.count));
  for (const row of s.papers_by_source) {
    bars.append(el("div", { class: "bar-row" },
      el("span", { class: "name" }, row.source),
      el("div", { class: "bar-track" },
        el("div", { class: "bar-fill", style: `width:${(row.count / max) * 100}%` })),
      el("span", { class: "n" }, String(row.count)),
    ));
  }
}

// ---------- crawl runs ----------
async function loadDates() {
  const { dates } = await getJSON("/api/dates");
  const sel = $("#runs-date");
  sel.replaceChildren();
  const today = new Date().toISOString().slice(0, 10);
  const opts = dates.length ? dates : [today];
  if (!opts.includes(today)) opts.unshift(today);
  for (const d of opts) sel.append(el("option", { value: d }, d));
  sel.value = opts.includes(today) ? today : opts[0];
}

async function loadRuns() {
  const date = $("#runs-date").value;
  const { runs } = await getJSON(`/api/runs?date=${encodeURIComponent(date)}`);
  const tbody = $("#runs-table tbody");
  tbody.replaceChildren();
  $("#runs-empty").hidden = runs.length > 0;
  $("#runs-table").hidden = runs.length === 0;
  for (const r of runs) {
    let badge;
    if (r.error) badge = el("span", { class: "badge err" }, "錯誤");
    else if (!r.finished_at) badge = el("span", { class: "badge review" }, "進行中");
    else badge = el("span", { class: "badge ok" }, "成功");
    tbody.append(el("tr", {},
      el("td", { class: "mono" }, r.source),
      el("td", { class: "mono" }, (r.started_at || "").replace("T", " ").replace("Z", "")),
      el("td", { class: "mono" }, (r.finished_at || "—").replace("T", " ").replace("Z", "")),
      el("td", { class: "mono" }, String(r.items_seen)),
      el("td", { class: "mono" }, String(r.items_kept)),
      el("td", {}, badge),
    ));
  }
}

// ---------- new papers ----------
let CURRENT_KIND = "strategy";
let TAXONOMY = { strategy: [], factor: [] };

async function loadTaxonomy() {
  try { TAXONOMY = await getJSON("/api/taxonomy"); } catch { /* keep empty */ }
}

function populateSubcatFilter() {
  const sel = $("#subcat-filter");
  const cur = sel.value;
  sel.replaceChildren(el("option", { value: "" }, "全部子類別"));
  for (const s of (TAXONOMY[CURRENT_KIND] || [])) {
    sel.append(el("option", { value: s }, s));
  }
  // keep selection if still valid
  if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
}

async function postLabel(source, source_id, op, value) {
  const r = await fetch("/api/labels", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, source_id, op, value }),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.error || `HTTP ${r.status}`);
  }
  return r.json();
}

function subcatChip(p, tag, manual) {
  const chip = el("span", { class: `tag ${manual ? "tag-manual" : ""}`, title: manual ? "手動標籤" : "自動分類" }, tag);
  if (manual) {
    const x = el("span", { class: "chip-x", title: "移除" }, " ×");
    x.addEventListener("click", async () => {
      try { await postLabel(p.source, p.source_id, "remove_subcat", tag); await loadPapers(); }
      catch (e) { showError(e.message); }
    });
    chip.append(x);
  }
  return chip;
}

function renderPaperRow(p) {
  // subcats cell: auto chips + manual chips + add control
  const subWrap = el("div", { class: "tags" });
  for (const t of p.subcats_auto || []) subWrap.append(subcatChip(p, t, false));
  for (const t of p.subcats_manual || []) subWrap.append(subcatChip(p, t, true));
  const addInput = el("input", { type: "text", class: "subcat-add", placeholder: "+標籤", list: "subcat-options" });
  addInput.addEventListener("keydown", async (ev) => {
    if (ev.key === "Enter" && addInput.value.trim()) {
      try { await postLabel(p.source, p.source_id, "add_subcat", addInput.value.trim()); await loadPapers(); }
      catch (e) { showError(e.message); }
    }
  });
  subWrap.append(addInput);

  // PDF / page links
  let pdfCell;
  if (p.pdf_local) {
    pdfCell = el("a", { href: `/files/pdf/${encodeURIComponent(p.pdf_local)}`, target: "_blank", rel: "noopener", title: p.pdf_local }, "📄本地");
  } else if (p.pdf_url) {
    pdfCell = el("a", { href: p.pdf_url, target: "_blank", rel: "noopener" }, "⬇遠端");
  } else { pdfCell = document.createTextNode("—"); }
  const links = el("span", {}, p.url ? el("a", { href: p.url, target: "_blank", rel: "noopener" }, "open") : "—", document.createTextNode(" · "), pdfCell);

  // kind override select
  const kindSel = el("select", { class: "kind-select", title: p.kind_overridden ? "已手動覆寫" : "自動分類" });
  for (const [v, label] of [["", "自動"], ["strategy", "strategy"], ["factor", "factor"]]) {
    const o = el("option", { value: v }, label);
    if ((v === "" && !p.kind_overridden) || (p.kind_overridden && v === p.kind)) o.selected = true;
    kindSel.append(o);
  }
  if (p.kind_overridden) kindSel.classList.add("overridden");
  kindSel.addEventListener("change", async () => {
    try { await postLabel(p.source, p.source_id, "set_kind", kindSel.value || null); await loadPapers(); }
    catch (e) { showError(e.message); }
  });

  return el("tr", {},
    el("td", { class: "mono" }, p.source),
    el("td", {}, p.title || "(無標題)"),
    el("td", {}, subWrap),
    el("td", {}, links),
    el("td", {}, kindSel),
  );
}

async function loadPapers() {
  const pdf = $("#papers-filter").value;       // "" | "any" | "local"
  const subcat = $("#subcat-filter").value;    // "" | <tag>
  const params = new URLSearchParams({ kind: CURRENT_KIND, limit: "500" });
  if (pdf) params.set("pdf", pdf);
  if (subcat) params.set("subcat", subcat);
  const data = await getJSON(`/api/papers?${params.toString()}`);
  const rows = data.papers;
  const kindLabel = CURRENT_KIND === "factor" ? "因子" : "策略";
  $("#papers-hint").textContent =
    `${kindLabel} ${rows.length} 筆` + (subcat ? ` · 子類別=${subcat}` : "") + (pdf ? ` · ${pdf === "local" ? "本地PDF" : "有PDF"}` : "");
  const tbody = $("#papers-table tbody");
  tbody.replaceChildren();
  $("#papers-empty").hidden = rows.length > 0;
  $("#papers-table").hidden = rows.length === 0;
  for (const p of rows) tbody.append(renderPaperRow(p));
}

// ---------- strategies ----------
let STRATEGIES = [];

function renderStrategies(filter = "") {
  const q = filter.trim().toLowerCase();
  const tbody = $("#strat-table tbody");
  tbody.replaceChildren();
  const rows = STRATEGIES.filter((s) => {
    if (!q) return true;
    const hay = [s.id, s.template, s.origin, ...(s.tags || [])].join(" ").toLowerCase();
    return q.split(/\s+/).every((tok) => hay.includes(tok));
  });
  $("#strat-empty").hidden = rows.length > 0;
  $("#strat-table").hidden = rows.length === 0;
  for (const s of rows) {
    const tags = el("div", { class: "tags" });
    for (const t of (s.tags || [])) tags.append(el("span", { class: "tag" }, t));
    const exported = s.exported
      ? el("span", { class: "badge ok" }, "✓ 已匯出")
      : el("span", { class: "badge no" }, "未匯出");
    const review = s.requires_review
      ? el("span", { class: "badge review" }, "待審")
      : el("span", { class: "badge no" }, "—");
    // 「已登記 zkperf」僅表示 manifest 宣告了 id，不代表已驗證；未設定則不顯示
    const zk = s.zkperf_registered
      ? el("span", { class: "badge no zk", title: `zkperf_id: ${s.zkperf_id}（僅登記，非驗證結果）` }, "已登記 zkperf")
      : document.createTextNode("");
    // spec links: README.md (the human spec) + manifest.yaml
    const specCell = el("span", { class: "spec-links" });
    if (s.has_spec_md) {
      specCell.append(el("a", {
        href: `/files/strategy/${encodeURIComponent(s.id)}/README.md`,
        target: "_blank", rel: "noopener", title: "策略 spec markdown",
      }, "📑 spec"));
    }
    if ((s.spec_files || []).includes("manifest.yaml")) {
      if (specCell.childNodes.length) specCell.append(document.createTextNode(" · "));
      specCell.append(el("a", {
        href: `/files/strategy/${encodeURIComponent(s.id)}/manifest.yaml`,
        target: "_blank", rel: "noopener", title: "manifest.yaml",
      }, "manifest"));
    }
    if (!specCell.childNodes.length) specCell.append(document.createTextNode("—"));
    tbody.append(el("tr", {},
      el("td", { class: "mono" }, s.id, zk),
      el("td", {}, el("span", { class: `badge ${s.origin}` }, s.origin)),
      el("td", { class: "mono" }, s.template || "—"),
      el("td", {}, tags),
      el("td", {}, specCell),
      el("td", {}, review),
      el("td", {}, exported),
    ));
  }
}

async function loadStrategies() {
  const { strategies } = await getJSON("/api/strategies");
  STRATEGIES = strategies;
  renderStrategies($("#strat-filter").value);
}

function syncDatalist() {
  // autocomplete options for the manual-tag input = current kind's vocabulary
  let dl = $("#subcat-options");
  if (!dl) {
    dl = el("datalist", { id: "subcat-options" });
    document.body.append(dl);
  }
  dl.replaceChildren(...(TAXONOMY[CURRENT_KIND] || []).map((s) => el("option", { value: s })));
}

// ---------- RAG browser ----------
async function loadRagStats() {
  const d = await getJSON("/api/rag/stats");
  $("#rag-stat-hint").textContent = `已索引 ${d.papers_indexed} 篇 · ${d.chunks} chunks`;
  const ul = $("#rag-paper-list");
  ul.replaceChildren();
  for (const p of d.papers || []) {
    const li = el("li", { class: "rag-paper", title: `${p.source}:${p.source_id}` },
      el("span", { class: `badge ${p.kind}` }, p.kind || "?"),
      document.createTextNode(" "),
      el("span", {}, `${p.title || p.source_id}`),
      el("span", { class: "hint" }, ` (${p.n_chunks})`),
    );
    li.addEventListener("click", () => showRagPaper(p.source, p.source_id));
    ul.append(li);
  }
}

async function ragSearch() {
  const q = $("#rag-q").value.trim();
  const kind = $("#rag-kind").value;
  const tbody = $("#rag-table tbody");
  if (!q) { tbody.replaceChildren(); $("#rag-empty").hidden = false; $("#rag-table").hidden = true; return; }
  const params = new URLSearchParams({ q, limit: "25" });
  if (kind) params.set("kind", kind);
  const d = await getJSON(`/api/rag/search?${params}`);
  tbody.replaceChildren();
  $("#rag-empty").hidden = d.chunks.length > 0;
  $("#rag-table").hidden = d.chunks.length === 0;
  if (!d.chunks.length) { $("#rag-empty").textContent = `「${q}」無命中。`; }
  for (const h of d.chunks) {
    const titleCell = el("span", { class: "rag-link", title: `${h.source}:${h.source_id}` }, h.title || h.source_id);
    titleCell.addEventListener("click", () => showRagPaper(h.source, h.source_id, q));
    tbody.append(el("tr", {},
      el("td", {}, titleCell),
      el("td", {}, el("span", { class: `badge ${h.kind}` }, h.kind || "?")),
      el("td", { class: "mono" }, String(h.page ?? "—")),
      el("td", { class: "mono" }, String(h.score)),
      el("td", { class: "rag-snippet" }, h.text),
    ));
  }
}

async function showRagPaper(source, source_id, q) {
  const box = $("#rag-detail");
  box.hidden = false;
  box.textContent = "載入中…";
  const params = new URLSearchParams({ source, source_id });
  if (q) params.set("q", q);
  const d = await getJSON(`/api/rag/paper?${params}`);
  box.replaceChildren();
  box.append(el("div", { class: "rag-detail-head" },
    el("strong", {}, d.title || `${source}:${source_id}`),
    el("span", { class: "hint" }, ` ${source}:${source_id} · ${d.kind || "?"}`),
    d.url ? el("a", { href: d.url, target: "_blank", rel: "noopener" }, " · 原文連結") : null,
    el("button", { class: "rag-close" }, " 收起"),
  ));
  box.querySelector(".rag-close").addEventListener("click", () => { box.hidden = true; });
  if (d.chunks) {
    for (const c of d.chunks) {
      box.append(el("div", { class: "rag-chunk" },
        el("span", { class: "hint" }, `p${c.page} · score ${c.score}`),
        el("p", {}, c.text)));
    }
  } else if (d.fulltext) {
    box.append(el("pre", { class: "rag-fulltext" }, d.fulltext));
  } else {
    box.append(el("p", { class: "empty" }, "此論文尚未索引全文。"));
  }
}

// ---------- MCP server info ----------
async function loadMcpInfo() {
  const d = await getJSON("/api/mcp/info");
  const running = (d.running || []).length;
  $("#mcp-running-hint").textContent = running
    ? `🟢 偵測到 ${running} 個運行中 process`
    : "⚪ stdio：由 Claude Code 按需啟動（目前無常駐 process）";
  const box = $("#mcp-info");
  box.replaceChildren();
  for (const s of d.config.servers || []) {
    box.append(el("div", { class: "mcp-server" },
      el("div", {}, el("strong", {}, s.name),
        el("span", { class: "badge manual" }, ` ${s.transport}`)),
      el("div", { class: "mono hint" }, `${s.command || ""} ${(s.args || []).join(" ")}`),
    ));
  }
  // index health
  box.append(el("div", { class: "hint", style: "margin:8px 0" },
    `索引：${d.rag.papers_indexed} 篇 / ${d.rag.chunks} chunks`));
  // running pids
  if (running) {
    box.append(el("div", { class: "hint" },
      "運行中: " + d.running.map((p) => `pid ${p.pid} (${p.started})`).join("; ")));
  }
  // tools
  const tbl = el("table", { class: "grid" });
  tbl.append(el("thead", {}, el("tr", {}, el("th", {}, "MCP tool"), el("th", {}, "說明"))));
  const tb = el("tbody", {});
  for (const t of d.tools || []) {
    tb.append(el("tr", {}, el("td", { class: "mono" }, t.name), el("td", {}, t.description)));
  }
  tbl.append(tb);
  box.append(tbl);
}

// ---------- orchestration ----------
async function refreshAll() {
  showError("");
  try {
    await loadSummary();
    await loadTaxonomy();
    populateSubcatFilter();
    syncDatalist();
    await loadDates();
    await loadRuns();
    await loadPapers();
    await loadStrategies();
    await loadRagStats();
    await loadMcpInfo();
  } catch (e) {
    showError(e.message || String(e));
  }
}

async function reloadPapersSafe() {
  showError("");
  try { await loadPapers(); } catch (e) { showError(e.message || String(e)); }
}

// ---------- 手動批次上傳 PDF ----------
async function uploadPdfs() {
  const input = $("#upload-input");
  const files = input.files;
  const status = $("#upload-status");
  const resultBox = $("#upload-result");
  if (!files || files.length === 0) {
    status.textContent = "請先選 PDF 檔";
    return;
  }
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const kind = $("#upload-kind").value;       // "" | "strategy" | "factor"
  if (kind) form.append("kind", kind);
  status.textContent = `上傳中… (${files.length} 檔${kind ? " → " + kind : ""})`;
  $("#upload-btn").disabled = true;
  try {
    const r = await fetch("/api/upload", { method: "POST", body: form });
    const data = await r.json();
    if (data.error && !(data.uploaded || []).length) {
      status.textContent = `✗ ${data.error}`;
    } else {
      status.textContent = `✓ ${data.summary || ""}`;
    }
    // 結果明細
    resultBox.hidden = false;
    resultBox.replaceChildren();
    for (const u of data.uploaded || []) {
      resultBox.append(el("div", { class: "upload-ok" },
        `✓ ${u.filename} → manual:${u.source_id} (${(u.bytes / 1024).toFixed(0)} KB)`));
    }
    for (const s of data.skipped || []) {
      resultBox.append(el("div", { class: "upload-skip" },
        `✗ ${s.filename} — ${s.reason}`));
    }
    input.value = "";   // 清空選取
    // 上傳成功 → 刷新 summary + papers（切到「有 PDF」看得到）
    if ((data.uploaded || []).length) {
      await loadSummary();
      await loadRagStats().catch(() => {});
      await reloadPapersSafe();
    }
  } catch (e) {
    status.textContent = `✗ ${e.message || e}`;
  } finally {
    $("#upload-btn").disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("#refresh-btn").addEventListener("click", refreshAll);
  $("#runs-date").addEventListener("change", async () => {
    showError("");
    try { await loadRuns(); } catch (e) { showError(e.message || String(e)); }
  });
  $("#strat-filter").addEventListener("input", (e) => renderStrategies(e.target.value));
  $("#papers-filter").addEventListener("change", reloadPapersSafe);
  $("#subcat-filter").addEventListener("change", reloadPapersSafe);
  const ragGo = async () => { showError(""); try { await ragSearch(); } catch (e) { showError(e.message); } };
  $("#rag-search-btn").addEventListener("click", ragGo);
  $("#rag-q").addEventListener("keydown", (e) => { if (e.key === "Enter") ragGo(); });
  $("#rag-kind").addEventListener("change", ragGo);
  $("#upload-btn").addEventListener("click", uploadPdfs);
  // kind tabs
  for (const btn of $$("#kind-tabs .tab")) {
    btn.addEventListener("click", async () => {
      $$("#kind-tabs .tab").forEach((b) => b.classList.toggle("active", b === btn));
      CURRENT_KIND = btn.dataset.kind;
      $("#subcat-filter").value = "";
      populateSubcatFilter();
      syncDatalist();
      await reloadPapersSafe();
    });
  }
  refreshAll();
});
