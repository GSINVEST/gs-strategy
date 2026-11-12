import { it, expect } from "vitest";
import { humanizeOutputs, valueText } from "./humanize";

const bundle = {
  StrategyBundle: {
    meta: {
      id: "sample_dual_ma_tw",
      name: "Dual MA Crossover (TSMC)",
      description: "Long 100 shares of TSMC\nwhen fast SMA crosses above slow SMA.",
      asset_class: "equity",
      calendar: "TEJ_XTAI",
      origin: "external",
      tags: ["sample", "taiwan"],
      reference: "moving-average",
      symbols: ["2330 (TSMC)"],
      params: { fast_window: 10, slow_window: 30, position_shares: 100 },
      requires_tej_key: true,
      provenance: { kind: "manual", generator: "gs-zipline-tej/dashboard-team", generator_version: 1, generated_at: "2026-05-26 00:00:00+08:00" },
    },
    window: { start: "2023-02-01", end: "2023-12-29", capital_base: 1000000, bundle_version: "a67b9c13c41ca04ce" },
    version: "a67b9c13c41ca04ce",
  },
};

it("策略資料包：翻成中文欄位與單位，不輸出原始 JSON", () => {
  const [c] = humanizeOutputs(bundle);
  const rows = Object.fromEntries(c.rows);
  expect(c.title).toBe("Dual MA Crossover (TSMC)");
  expect(c.summary).not.toContain("\n");
  expect(rows["資產類別"]).toBe("股票（equity）");
  expect(rows["快線週期"]).toBe("10 根 K 棒");
  expect(rows["持有股數"]).toBe("100 股");
  expect(rows["回測區間"]).toBe("2023-02-01 ～ 2023-12-29");
  expect(rows["起始資金"]).toBe("1,000,000 元");
  expect(rows["需要 TEJ 金鑰"]).toBe("是");
  expect(rows["資料版本"]).toBe("a67b9c13…");
  expect(c.tags).toEqual(["sample", "taiwan"]);
});

it("策略宣告：params 與頂層欄位合併、驗證設定為空時標示尚未設定", () => {
  const [c] = humanizeOutputs({
    StrategySpec: { asset_class: "equity", bundle: "tquant", calendar: "TEJ_XTAI", universe: ["2330 (TSMC)"], symbol: "2330", params: { fast_window: 10 }, validation: {}, origin: "external", source: { kind: "manual", inputs: { description: "手寫範例" } } },
  });
  const rows = Object.fromEntries(c.rows);
  expect(c.title).toBe("策略宣告");
  expect(c.summary).toBe("手寫範例");
  expect(rows["標的代號"]).toBe("2330");
  expect(rows["快線週期"]).toBe("10 根 K 棒");
  expect(rows["驗證設定"]).toBe("尚未設定");
});

it("未知輸出：退回通用欄位表，空輸出回傳 null", () => {
  const [c] = humanizeOutputs({ Foo: { id: "x", start: "2020-01-01" } });
  expect(Object.fromEntries(c.rows)).toEqual({ 代號: "x", 起日: "2020-01-01" });
  expect(humanizeOutputs({})).toBeNull();
  expect(valueText(null)).toBe("—");
});
