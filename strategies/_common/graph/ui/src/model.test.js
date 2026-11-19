import { describe, it, expect, vi, afterEach } from "vitest";
import { compatible, unavailable, estimateText, latestPreview } from "./model";
import { edgeId, costText, initialFit, pollJob, resumeOrStart, api } from "./model";

it.each(["graph_revision_conflict", "run_estimate_conflict"])("C1/C3 never retries %s", async (code) => {
  const error = Object.assign(new Error("圖已變更"), { status: 409, code });
  const start = vi.fn().mockRejectedValue(error), session = vi.fn(), follow = vi.fn();
  await expect(resumeOrStart({ start, session, follow })).rejects.toBe(error);
  expect(start).toHaveBeenCalledTimes(1);
  expect(session).not.toHaveBeenCalled();
  expect(follow).not.toHaveBeenCalled();
});

it("C4 propagates missing inputs through reserved node ids without inherited state", () => {
  const nodes = ["source", "__proto__", "constructor"].map((id) => ({ id, type: "data.test" }));
  const edges = [
    { from: ["source", "Bars"], to: ["__proto__", "Bars"] },
    { from: ["__proto__", "Bars"], to: ["constructor", "Bars"] },
  ];
  const ready = unavailable({ nodes, edges }, { "data.test": { inputs: {} } });
  expect(Object.keys(ready)).toEqual([]);
  const missing = unavailable({ nodes, edges }, { "data.test": { inputs: { Bars: "Bars" } } });
  expect(missing.__proto__).toContain("缺 Bars");
  expect(missing.constructor).toContain("缺 Bars");
});

it("C3 clears queued stale edits while recovering a conflict", async () => {
  vi.useFakeTimers();
  let reject;
  const apply = vi.fn(() => new Promise((_, fail) => { reject = fail; }));
  const preview = vi.fn();
  const q = latestPreview({ cancel: vi.fn(), apply, preview, onError: () => q.reset() });
  q.schedule("__proto__", "value", 1);
  await vi.advanceTimersByTimeAsync(300);
  expect(Object.hasOwn(apply.mock.calls[0][0], "__proto__")).toBe(true);
  q.schedule("constructor", "value", 2);
  reject(Object.assign(new Error("圖已變更"), { status: 409 }));
  await vi.advanceTimersByTimeAsync(1000);
  expect(apply).toHaveBeenCalledTimes(1);
  expect(preview).not.toHaveBeenCalled();
  expect(q.pending).toBe(false);
  q.dispose();
});

it("B6 preserves remaining edge identities after deletion", () => {
  const edges = [
    { from: ["a", "Bars"], to: ["b", "Bars"] },
    { from: ["b", "Score"], to: ["c", "Score"] },
  ];
  expect(edges.slice(1).map(edgeId)).toEqual([edges.map(edgeId)[1]]);
  expect(edgeId(edges[0])).not.toBe(edgeId(edges[1]));
});
it("B7 displays cost units without raw JSON", () => {
  expect(costText({ per_contract_cost: { TX: 200, MTX: 100 }, spread_points: 6 }))
    .toBe("TX 200・MTX 100 元／口，滑價 6 點");
});
it("B7 displays TX then MTX regardless of API key order, then sorts other symbols", () => {
  expect(costText({ per_contract_cost: { TF: 80, MTX: 100, TX: 200, TE: 90 }, spread_points: 6 }))
    .toBe("TX 200・MTX 100・TE 90・TF 80 元／口，滑價 6 點");
});
it("B3 fits once after measurement and ignores later selection or dimensions", () => {
  const fit = vi.fn(), once = initialFit();
  once(false, fit);
  expect(fit).not.toHaveBeenCalled();
  once(true, fit);
  once(true, fit);
  expect(fit).toHaveBeenCalledTimes(1);
});
it("B4 polls at 300ms and refreshes nodes only for changed states or completion", async () => {
  const get = vi.fn()
    .mockResolvedValueOnce({ status: "running", node_states: { a: "running" } })
    .mockResolvedValueOnce({ status: "running", node_states: { a: "running" } })
    .mockResolvedValueOnce({ status: "complete", node_states: { a: "cached" } });
  const nodes = vi.fn(), pause = vi.fn();
  await pollJob("job", { get, nodes, pause });
  expect(get).toHaveBeenCalledTimes(3);
  expect(nodes).toHaveBeenCalledTimes(2);
  expect(pause.mock.calls).toEqual([[300], [300]]);
});
it("B1 resumes an active job before starting its own preview", async () => {
  const events = [], job = { id: "existing", preview: true };
  await resumeOrStart({ activeJob: job,
    follow: async (j) => events.push(j.id),
    start: async () => events.push("preview"),
    session: vi.fn(),
  });
  expect(events).toEqual(["existing", "preview"]);
});
it("B1 recovers a preview 409 race by following the new active job", async () => {
  const follow = vi.fn(), start = vi.fn()
    .mockRejectedValueOnce(Object.assign(new Error("執行中"), { status: 409 }))
    .mockResolvedValueOnce("done");
  await resumeOrStart({ start, follow,
    session: async () => ({ active_job: { id: "raced" } }),
  });
  expect(follow).toHaveBeenCalledWith({ id: "raced" });
  expect(start).toHaveBeenCalledTimes(2);
});
it("B1 retries when a racing job finishes before the session response", async () => {
  const start = vi.fn()
    .mockRejectedValueOnce(Object.assign(new Error("執行中"), { status: 409 }))
    .mockResolvedValueOnce("done");
  expect(await resumeOrStart({ start, follow: vi.fn(),
    session: async () => ({ active_job: null }),
  })).toBe("done");
});
afterEach(() => vi.useRealTimers());
describe("typed ports", () => {
  it("only permits equal known types", () => {
    expect(compatible("Score", "Score")).toBe(true);
    expect(compatible("Sigma", "Score")).toBe(false);
    expect(compatible(undefined, undefined)).toBe(false);
  });
  it("mirrors the Python parametric rule", () => {
    expect(compatible("Signals[llm]", "Signals")).toBe(true);
    expect(compatible("Signals[llm]", "Signals[llm]")).toBe(true);
    expect(compatible("Signals[llm]", "Signals[*]")).toBe(true);
    expect(compatible("Signals[rule]", "Signals[llm]")).toBe(false);
    expect(compatible("Signals", "Signals[llm]")).toBe(false);
    expect(compatible("Signals[]", "Signals")).toBe(false);
  });
});
it("missing Sigma marks sizing and its downstream unavailable", () => {
  const graph = {
    nodes: [
      { id: "sizing", type: "size" },
      { id: "backtest", type: "bt" },
      { id: "report", type: "report" },
    ],
    edges: [
      { from: ["sizing", "Weights"], to: ["backtest", "Weights"] },
      { from: ["backtest", "Returns"], to: ["report", "Returns"] },
    ],
  };
  const m = unavailable(graph, {
    size: { inputs: { Sigma: "Sigma" } },
    bt: { inputs: { Weights: "Weights" } },
    report: { inputs: { Returns: "Returns" } },
  });
  expect(m.sizing).toBe("缺 Sigma");
  expect(m.backtest).toContain("缺 Sigma");
  expect(m.report).toContain("缺 Sigma");
});
describe("N estimate", () => {
  it("shows new configuration increment and replay unchanged", () => {
    expect(
      estimateText({
        selection_n: 34,
        next_selection_n: 35,
        already_recorded: false,
      }),
    ).toBe("執行・N 34→35");
    expect(estimateText({ already_recorded: true })).toBe(
      "執行・快取重播，N 不變",
    );
  });
});
describe("latest preview scheduler", () => {
  it("immediately cancels a running preview on a new edit and applies the newest payload after it ends", async () => {
    vi.useFakeTimers();
    let end;
    let running = false;
    const cancel = vi.fn(async () => {
      if (running) {
        running = false;
        end();
      }
    });
    const apply = vi.fn();
    const preview = vi
      .fn()
      .mockImplementationOnce(() => {
        running = true;
        return new Promise((r) => (end = r));
      })
      .mockResolvedValue();
    const q = latestPreview({ cancel, apply, preview });
    q.schedule("momentum", "lookback", 10);
    await vi.advanceTimersByTimeAsync(300);
    expect(running).toBe(true);
    q.schedule("momentum", "lookback", 20);
    expect(cancel).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(300);
    expect(apply).toHaveBeenLastCalledWith({ momentum: { lookback: 20 } });
    expect(preview).toHaveBeenCalledTimes(2);
    q.dispose();
  });
  it("marks the initial pending preview busy before its debounce fires", () => {
    vi.useFakeTimers();
    const onState = vi.fn();
    const q = latestPreview({
      cancel: vi.fn(),
      apply: vi.fn(),
      preview: vi.fn(),
      onState,
    });
    q.start();
    expect(q.pending).toBe(true);
    expect(onState).toHaveBeenCalledWith("debouncing");
    q.dispose();
  });
  it("debounces rapid edits and cancels before applying only latest value", async () => {
    vi.useFakeTimers();
    const calls = [];
    const q = latestPreview({
      cancel: async () => calls.push("cancel"),
      apply: async (x) => calls.push(x),
      preview: async () => calls.push("preview"),
    });
    q.schedule("momentum", "lookback", 10);
    await vi.advanceTimersByTimeAsync(200);
    q.schedule("momentum", "lookback", 20);
    await vi.advanceTimersByTimeAsync(299);
    expect(calls).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toEqual([
      "cancel",
      { momentum: { lookback: 20 } },
      "preview",
    ]);
    q.dispose();
  });
  it("waits for cancellation and suppresses obsolete preview without parallel jobs", async () => {
    vi.useFakeTimers();
    let release;
    const preview = vi.fn(),
      apply = vi.fn();
    const cancel = vi
      .fn()
      .mockImplementationOnce(() => new Promise((r) => (release = r)))
      .mockResolvedValue();
    const q = latestPreview({ cancel, apply, preview });
    q.schedule("momentum", "lookback", 10);
    await vi.advanceTimersByTimeAsync(300);
    q.schedule("momentum", "lookback", 30);
    release();
    await vi.advanceTimersByTimeAsync(0);
    expect(preview).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(300);
    expect(preview).toHaveBeenCalledTimes(1);
    expect(apply).toHaveBeenLastCalledWith({ momentum: { lookback: 30 } });
    q.dispose();
  });
});

describe("api 空回應防護", () => {
  afterEach(() => vi.unstubAllGlobals());
  const stub = (status, body, headers = {}, redirected = false) =>
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status, headers })));
  it("空 body 的 502 給出後端無法連線的中文訊息，而非瀏覽器原文", async () => {
    stub(502, "");
    await expect(api("/graph")).rejects.toMatchObject({ message: expect.stringContaining("無法連線"), status: 502 });
  });
  it("401/403 空 body 提示重新登入", async () => {
    stub(401, "");
    await expect(api("/graph")).rejects.toMatchObject({ message: expect.stringContaining("重新登入") });
  });
  it("200 但 body 為空（被導去登入）提示重新登入", async () => {
    stub(200, "");
    await expect(api("/graph")).rejects.toMatchObject({ message: expect.stringContaining("重新登入") });
  });
  it("200 但回 HTML（登入頁）提示重新登入", async () => {
    stub(200, "<html></html>", { "Content-Type": "text/html" });
    await expect(api("/graph")).rejects.toMatchObject({ message: expect.stringContaining("重新登入") });
  });
  it("正常 JSON 照舊回傳；帶 JSON 的錯誤保留 status/code", async () => {
    stub(200, JSON.stringify({ a: 1 }), { "Content-Type": "application/json" });
    await expect(api("/graph")).resolves.toEqual({ a: 1 });
    stub(409, JSON.stringify({ error: "圖已變更", code: "graph_revision_conflict", revision: 3 }), { "Content-Type": "application/json" });
    await expect(api("/graph")).rejects.toMatchObject({ status: 409, code: "graph_revision_conflict", revision: 3 });
  });
});
