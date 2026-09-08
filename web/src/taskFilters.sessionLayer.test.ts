// §37.2 会话内容层（LAST layer）的匹配语义（CONTRACT §37.2 第三条；原生 Store.hitInfo / SearchMatch；D45）：
// 跨层合并 AND——每个查询词可由行字段**或**会话正文满足（"推荐信 chen" 命中标题含推荐信、只有会话里提过 chen 的卡）；
// sessionOnly（「命中会话」章的诚实条件）= 命中且仅靠行字段不命中；没有会话文本 = 只有字段一层；空查询直通。
// 同一套 normalizeSearchText / searchTerms——没有第二个匹配器。会话正文在落地时归一化一次（normalizeSessionIndex）。
import { describe, expect, it } from "vitest";
import {
  EMPTY_CARD_FILTERS,
  matchesCardFilters,
  matchesCardSearch,
  normalizeSearchText,
  normalizeSessionIndex,
  searchHit,
} from "./taskFilters";

const card = { id: "P-1", title: "写推荐信", tier: "T1", sources: [{ who: "sam", quote: "帮我写 letter", channel: "slack" }] };
const session = normalizeSearchText("… we discussed Chen's H-1B timeline and the EB-1A draft …");

describe("searchHit · 跨层合并 AND（原生 hitInfo）", () => {
  it("空查询直通、不出章", () => {
    expect(searchHit(card, "", session)).toEqual({ hit: true, sessionOnly: false });
    expect(searchHit(card, "   ", undefined)).toEqual({ hit: true, sessionOnly: false });
  });

  it("字段本就命中 → hit、sessionOnly=false（章不是「有会话」的意思，会话里也有这个词也不出章）", () => {
    expect(searchHit(card, "推荐信", session)).toEqual({ hit: true, sessionOnly: false });
    expect(searchHit(card, "letter", session)).toEqual({ hit: true, sessionOnly: false });
  });

  it("词只在会话正文里 → hit、sessionOnly=true；归一化同一套（h1b 命中 H-1B、eb1 命中 EB-1A、大小写不敏感）", () => {
    expect(searchHit(card, "chen", session)).toEqual({ hit: true, sessionOnly: true });
    expect(searchHit(card, "h1b", session)).toEqual({ hit: true, sessionOnly: true });
    expect(searchHit(card, "EB1", session)).toEqual({ hit: true, sessionOnly: true });
  });

  it("跨层 AND：'推荐信 chen' —— 一个词靠字段、一个词靠会话，命中且算会话命中；'eb2 chen' 哪层都没有 eb2 → 不命中", () => {
    expect(searchHit(card, "推荐信 chen", session)).toEqual({ hit: true, sessionOnly: true });
    expect(searchHit(card, "chen 推荐信", session)).toEqual({ hit: true, sessionOnly: true });
    expect(searchHit(card, "eb2 chen", session)).toEqual({ hit: false, sessionOnly: false });
  });

  it("没有会话文本（层缺席 / 这张卡没条目）→ 只剩字段层", () => {
    expect(searchHit(card, "chen", undefined)).toEqual({ hit: false, sessionOnly: false });
    expect(searchHit(card, "chen", "")).toEqual({ hit: false, sessionOnly: false });
    expect(searchHit(card, "推荐信", undefined)).toEqual({ hit: true, sessionOnly: false });
  });

  it("不命中永远 sessionOnly=false（章只挂在命中的卡上）", () => {
    expect(searchHit(card, "nothing", session).sessionOnly).toBe(false);
  });
});

describe("matchesCardSearch / matchesCardFilters 透传会话文本", () => {
  it("matchesCardSearch 第三个参数 = 会话正文；缺省与旧签名同义", () => {
    expect(matchesCardSearch(card, "chen")).toBe(false);
    expect(matchesCardSearch(card, "chen", session)).toBe(true);
  });

  it("matchesCardFilters：chips 照常判定，搜索这一维多一层；chip 不过就不看会话", () => {
    const filters = { ...EMPTY_CARD_FILTERS, search: "chen" };
    expect(matchesCardFilters(card, filters)).toBe(false);
    expect(matchesCardFilters(card, filters, session)).toBe(true);
    expect(matchesCardFilters(card, { ...filters, tiers: ["T2"] }, session)).toBe(false);
  });
});

describe("normalizeSessionIndex · server entries → 归一化文本表", () => {
  it("每条 normalizeSearchText 一次；非 str / 归一化后为空的条目丢掉", () => {
    expect(normalizeSessionIndex({
      "P-1": "Chen's H-1B  Draft",
      "P-2": 42,
      "P-3": "",
      "P-4": " - _ . ",
      "P-5": "推荐信 EB-1A",
    })).toEqual({ "P-1": "chen'sh1bdraft", "P-5": "推荐信eb1a" });
  });

  it("空表 → 空表", () => {
    expect(normalizeSessionIndex({})).toEqual({});
  });
});
