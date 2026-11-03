import React from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FsmView, StateGraph } from "./FsmPanel";
import { badWords, cexParts, cfgKey, cfgResult, cfgRows, edgePath, layoutGraph, pickTarget, staleInfo, verdictInfo } from "./fsm";

const CEX = "【反例】規則被打破。\n  此規則由交易員提出，已確認。\n\n【這個反例用到的模型數字與來源】\n  - 搜尋界。\n\n【它代表什麼、不代表什麼】\n  - 代表：模型內存在情境。";
const target = (over = {}) => ({
  id: "demo", title_zh: "示範", status: "counterexample",
  graph: { nodes: [{ id: "A", label_zh: "空手", initial: true }, { id: "B", label_zh: "多單" }],
    edges: [{ id: "T1", from: "A", to: "B", label_zh: "進場" }, { id: "T2", from: "B", to: "A", label_zh: "出場" }, { id: "T3", from: "B", to: "B", label_zh: "續抱" }] },
  invariants: [
    { id: "I1", label_zh: "一致", verdict: "pass_with_bounds", origin: "code_derived", confirm_status: "confirmed", profile: "core", cfg: "core.cfg", distinct_states: 9, enforced_in_code: "unknown" },
    { id: "I2", label_zh: "虧損上限", verdict: "counterexample", origin: "trader_stated", confirm_status: "confirmed", profile: "stress", cfg: "stress-I2.cfg", enforced_in_code: false,
      counterexample: { cfg: "stress-I2.cfg", text_zh: CEX } },
  ],
  mutations: [{ name: "m", cfg: "mutation-m.cfg", caught: true }],
  ...over,
});
const view = (t = target()) => ({ source: "atlas", generated_at: "2026-11-03T00:00:00Z", stale_days: 36500, disclaimer: "檢查的是規則的邏輯", current: t.id, targets: [t] });

describe("fsm pure logic", () => {
  it("cfgKey maps file names", () => {
    expect(cfgKey("core.cfg")).toBe("core");
    expect(cfgKey("stress-INV_X.cfg")).toBe("stress:INV_X");
    expect(cfgKey("mutation-a.cfg")).toBe("mutation:a");
    expect(cfgKey("other.txt")).toBeNull();
  });
  it("only an all-pass cfg is pass; any counterexample wins; unknown is never green", () => {
    expect(cfgResult(["pass_with_bounds", "pass_with_bounds"])).toBe("pass");
    expect(cfgResult(["pass_with_bounds", "counterexample"])).toBe("cex");
    expect(cfgResult(["pass_with_bounds", "needs_review"])).toBe("unknown");
    expect(cfgResult(["pass_with_bounds", "mutation_not_caught"])).toBe("unknown");
    expect(cfgResult(["weird"])).toBe("unknown");
    expect(cfgResult(["unchecked"])).toBe("unchecked");
    expect(cfgResult([])).toBe("unchecked");
  });
  it("cfgRows lists core, stress and mutation rows", () => {
    expect(cfgRows(target()).map((r) => [r.key, r.result])).toEqual([["core", "pass"], ["stress:I2", "cex"], ["mutation:m", "caught"]]);
  });
  it("cexParts requires the fixed three sections", () => {
    expect(cexParts(CEX).complete).toBe(true);
    expect(cexParts(CEX.replace("【它代表什麼、不代表什麼】", "【其他】")).complete).toBe(false);
    expect(cexParts("").complete).toBe(false);
  });
  it("staleInfo treats a missing timestamp as unknown, not fresh", () => {
    expect(staleInfo({})).toEqual({ known: false, stale: false });
    expect(staleInfo({ generated_at: "2026-01-01T00:00:00Z", stale_days: 7 }, Date.parse("2026-02-01T00:00:00Z")).stale).toBe(true);
  });
  it("unknown verdicts are warned, never shown as pass", () => {
    expect(verdictInfo("brand_new").cls).toBe("warn");
  });
  it("pickTarget prefers the loaded strategy, then the first", () => {
    const v = { current: "b", targets: [{ id: "a" }, { id: "b" }] };
    expect(pickTarget(v, null).id).toBe("b");
    expect(pickTarget(v, "a").id).toBe("a");
    expect(pickTarget({ targets: [] }, null)).toBeNull();
  });
  it("layout places every node and edges (incl. self loops) get paths", () => {
    const g = target().graph;
    const l = layoutGraph(g);
    expect(Object.keys(l.nodes).sort()).toEqual(["A", "B"]);
    for (const e of g.edges) expect(edgePath(e, l.nodes)?.d).toMatch(/^M/);
    expect(edgePath({ from: "A", to: "ZZ" }, l.nodes)).toBeNull();
  });
});

describe("fsm view rendering", () => {
  it("renders graph, invariants with origin/enforced, cfg results and the plain-language counterexample", () => {
    const html = renderToString(React.createElement(FsmView, { view: view() }));
    for (const s of ["空手", "多單", "交易員提出", "程式碼沒有實作", "stress:I2", "有限模型下不變量未被違反", "發現反例", "【它代表什麼、不代表什麼】"])
      expect(html).toContain(s);
    expect(html).not.toContain("fsm-cex-incomplete");
    expect(badWords(html.replace(/<[^>]+>/g, ""))).toEqual([]);
  });
  it("warns when the counterexample lacks the fixed ending", () => {
    const t = target();
    t.invariants[1].counterexample.text_zh = "【反例】只有這段，由交易員提出";
    expect(renderToString(React.createElement(FsmView, { view: view(t) }))).toContain("fsm-cex-incomplete");
  });
  it("shows what is missing when the model was refused and never shows a pass", () => {
    const t = target({ status: "unchecked", refused: [{ code: "TODO_FILL_TIMING", message: "成交時機未確認" }],
      invariants: [{ id: "I1", verdict: "unchecked", label_zh: "一致" }], mutations: [] });
    const html = renderToString(React.createElement(FsmView, { view: view(t) }));
    expect(html).toContain("沒有任何規則被檢查");
    expect(html).toContain("TODO_FILL_TIMING");
    expect(html).not.toContain("有限模型下不變量未被違反");
  });
  it("an empty view says there is no data", () => {
    expect(renderToString(React.createElement(FsmView, { view: { targets: [] } }))).toContain("還沒有資料");
  });
  it("renders an empty graph without crashing", () => {
    expect(renderToString(React.createElement(StateGraph, { graph: {} }))).toContain("沒有狀態圖資料");
  });
});
