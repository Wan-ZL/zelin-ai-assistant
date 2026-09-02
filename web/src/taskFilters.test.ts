// taskFilters 行为测试：URL 往返、计数、跨分区匹配语义（缺字段 = 维度不适用，行保留）。
import { describe, expect, it } from "vitest";
import {
  applyCardFilters,
  cardFilterCount,
  collectChannels,
  collectTypes,
  EMPTY_CARD_FILTERS,
  matchesCardFilters,
  readCardFilters,
  toggleFilterValue,
  type CardFilters,
} from "./taskFilters";
import type { Board } from "./types";

const FULL: CardFilters = {
  tiers: ["T1", "T2"],
  types: ["engineering"],
  channels: ["slack", "meeting"],
  deadline: "soon",
  reraisedOnly: true,
  search: "readme",
};

describe("URL 序列化", () => {
  it("写入后读回逐字段等价（round-trip）", () => {
    const url = applyCardFilters(new URL("http://x/?page=trash&card=R-101"), FULL);
    expect(readCardFilters(url.search)).toEqual(FULL);
    // route.ts 的参数不被覆写
    expect(url.searchParams.get("page")).toBe("trash");
    expect(url.searchParams.get("card")).toBe("R-101");
  });

  it("空过滤器清掉全部自有参数", () => {
    const dirty = applyCardFilters(new URL("http://x/"), FULL);
    const clean = applyCardFilters(dirty, EMPTY_CARD_FILTERS);
    expect(clean.search).toBe("");
  });

  it("非法 deadline 值回落 all", () => {
    expect(readCardFilters("?deadline=bogus").deadline).toBe("all");
  });
});

describe("计数与切换", () => {
  it("cardFilterCount 数激活维度", () => {
    expect(cardFilterCount(EMPTY_CARD_FILTERS)).toBe(0);
    expect(cardFilterCount(FULL)).toBe(6);
  });

  it("toggleFilterValue 开关成员", () => {
    expect(toggleFilterValue(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleFilterValue(["a", "b"], "a")).toEqual(["b"]);
  });
});

describe("匹配语义", () => {
  const proposal = {
    id: "R-1", title: "修 README", tier: "T1", deadline: "2099-01-01", days_left: 3,
    sources: [{ who: "a", channel: "slack", date: "d", quote: "readme broken" }],
  };
  const runningRow = { id: "R-2", name: "跑着的活", state: "working" }; // 无 tier/type/sources
  const debtRow = {
    id: "R-3", title: "日志太吵", type: "engineering",
    sources: [{ who: "b", channel: "meeting", date: "d", quote: "q" }],
  };

  const f = (patch: Partial<CardFilters>): CardFilters => ({ ...EMPTY_CARD_FILTERS, ...patch });

  it("tier 只约束携带 tier 的行；running 行保持可见", () => {
    expect(matchesCardFilters(proposal, f({ tiers: ["T2"] }))).toBe(false);
    expect(matchesCardFilters(proposal, f({ tiers: ["T1"] }))).toBe(true);
    expect(matchesCardFilters(runningRow, f({ tiers: ["T2"] }))).toBe(true);
  });

  it("channel 从 sources 取；空 sources 数组在渠道过滤下被排除", () => {
    expect(matchesCardFilters(debtRow, f({ channels: ["meeting"] }))).toBe(true);
    expect(matchesCardFilters(debtRow, f({ channels: ["gmail"] }))).toBe(false);
    expect(matchesCardFilters({ id: "R-4", tier: "T1", sources: [] }, f({ channels: ["slack"] }))).toBe(false);
    expect(matchesCardFilters(runningRow, f({ channels: ["slack"] }))).toBe(true); // 无 sources 概念
  });

  it("deadline：soon/overdue/none 按 days_left 判定，仅作用于提案形行", () => {
    expect(matchesCardFilters(proposal, f({ deadline: "soon" }))).toBe(true);
    expect(matchesCardFilters(proposal, f({ deadline: "overdue" }))).toBe(false);
    expect(matchesCardFilters(proposal, f({ deadline: "none" }))).toBe(false);
    expect(matchesCardFilters({ id: "R-5", tier: "T0", days_left: -2, deadline: "2020-01-01" }, f({ deadline: "overdue" }))).toBe(true);
    expect(matchesCardFilters(runningRow, f({ deadline: "has" }))).toBe(true);
  });

  it("reraisedOnly：提案形行缺 reraised = false 被滤掉；其他分区不受影响", () => {
    expect(matchesCardFilters(proposal, f({ reraisedOnly: true }))).toBe(false);
    expect(matchesCardFilters({ ...proposal, reraised: true }, f({ reraisedOnly: true }))).toBe(true);
    expect(matchesCardFilters(runningRow, f({ reraisedOnly: true }))).toBe(true);
  });

  it("search 作用于全部行：id/title/name/summary + sources 文本，大小写不敏感", () => {
    expect(matchesCardFilters(proposal, f({ search: "readme" }))).toBe(true);
    expect(matchesCardFilters(proposal, f({ search: "README" }))).toBe(true);
    expect(matchesCardFilters(runningRow, f({ search: "跑着" }))).toBe(true);
    expect(matchesCardFilters(runningRow, f({ search: "readme" }))).toBe(false);
  });

  it("§60：工作编号 / 展示编号也可搜——用户记的是 R-280，主键是 P-012", () => {
    const work = { ...runningRow, id: "P-012", work_id: "R-280", display_id: "R-280" };
    expect(matchesCardFilters(work, f({ search: "R-280" }))).toBe(true);
    expect(matchesCardFilters(work, f({ search: "P-012" }))).toBe(true);
  });
});

describe("词表收集", () => {
  const board = {
    generated_at: "g", counts: {},
    needs_approval: [{ id: "R-1", title: "t", tier: "T1", processing: false, show_cost: false,
      sources: [{ who: "a", channel: "slack", date: "d", quote: "q" }], plan: [], dod: [] }],
    running: [], needs_input: [],
    review: [{ id: "R-2", name: "n", dod: [], delivery_mode: "chat",
      sources: [{ who: "b", channel: "gmail", date: "d", quote: "q" }] }],
    completed: [],
    debt: [{ id: "R-3", title: "t", type: "process",
      sources: [{ who: "c", channel: "meeting", date: "d", quote: "q" }] }],
    trash: [{ id: "R-4", title: "t", permanent: false, trashed_at: "2026-01-01T00:00:00Z", type: "engineering" }],
  } as unknown as Board;

  it("collectTypes 收 debt+trash，排序去重", () => {
    expect(collectTypes(board)).toEqual(["engineering", "process"]);
    expect(collectTypes(null)).toEqual([]);
  });

  it("collectChannels 收 needs_approval+review+debt", () => {
    expect(collectChannels(board)).toEqual(["gmail", "meeting", "slack"]);
  });
});
