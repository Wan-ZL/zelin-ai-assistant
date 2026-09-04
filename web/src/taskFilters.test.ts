// taskFilters 行为测试：URL 往返、计数、跨分区匹配语义（缺字段 = 维度不适用，行保留）、
// D28 退役维度（type / channel）的旧 URL 参数容忍。
import { describe, expect, it } from "vitest";
import {
  applyCardFilters,
  cardFilterCount,
  EMPTY_CARD_FILTERS,
  matchesCardFilters,
  readCardFilters,
  toggleFilterValue,
  type CardFilters,
} from "./taskFilters";

const FULL: CardFilters = {
  tiers: ["T1", "T2"],
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

  it("D28：旧 URL 的 type= / channel= 容忍读取（忽略），下次写回时丢弃，其余参数不动", () => {
    const legacy = "?page=board&tier=T1&type=engineering&channel=slack,meeting&q=readme";
    const filters = readCardFilters(legacy);
    expect(filters).toEqual({ ...EMPTY_CARD_FILTERS, tiers: ["T1"], search: "readme" });
    expect(filters).not.toHaveProperty("types");
    expect(filters).not.toHaveProperty("channels");

    const url = applyCardFilters(new URL(`http://x/${legacy}`), filters);
    expect(url.searchParams.has("type")).toBe(false);
    expect(url.searchParams.has("channel")).toBe(false);
    expect(url.searchParams.get("page")).toBe("board");
    expect(url.searchParams.get("tier")).toBe("T1");
    expect(url.searchParams.get("q")).toBe("readme");
  });
});

describe("计数与切换", () => {
  it("cardFilterCount 数激活维度", () => {
    expect(cardFilterCount(EMPTY_CARD_FILTERS)).toBe(0);
    expect(cardFilterCount(FULL)).toBe(4);
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
  const runningRow = { id: "R-2", name: "跑着的活", state: "working" }; // 无 tier/sources
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

  it("D28：debt 行的 type / sources[].channel 不再是过滤维度，但仍可被 ⌘F 搜到", () => {
    expect(matchesCardFilters(debtRow, f({ tiers: ["T2"], deadline: "soon", reraisedOnly: true }))).toBe(true);
    expect(matchesCardFilters(debtRow, f({ search: "engineering" }))).toBe(true);
    expect(matchesCardFilters(debtRow, f({ search: "meeting" }))).toBe(true);
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
