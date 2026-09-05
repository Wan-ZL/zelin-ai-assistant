// ⌘F 搜索的 §37.2 判例（CONTRACT §37.2；原生 shared/Sources/SearchMatch.swift + Store.swift searchFields）：
// 归一化（剥 - / _ / . / 空白，lowercase）、空白切词 AND、CJK 原样子串、词表全量（display_title /
// former_titles / notes_text / plan / dod / final_draft / agent_name）、改名后旧名仍可搜。
import { describe, expect, it } from "vitest";
import { matchesCardSearch, normalizeSearchText, searchHaystack, searchTerms } from "./taskFilters";

describe("§37.2 归一化（SearchMatch.normalize 孪生）", () => {
  it("lowercase + 剥掉 - / _ / . / 空白；CJK 与其余字符原样通过", () => {
    expect(normalizeSearchText("EB-1A")).toBe("eb1a");
    expect(normalizeSearchText("H-1B petition_draft v1.2")).toBe("h1bpetitiondraftv12");
    expect(normalizeSearchText("写 推荐信\t给 Chen")).toBe("写推荐信给chen");
    expect(normalizeSearchText("　全角空格　")).toBe("全角空格"); // U+3000 也是空白
    expect(normalizeSearchText("")).toBe("");
  });

  it("searchTerms：空白切词、逐词归一化、去空；空查询 = []", () => {
    expect(searchTerms("  draft   EB-1A ")).toEqual(["draft", "eb1a"]);
    expect(searchTerms("- _ .")).toEqual([]); // 只剩分隔符的词剥完为空，不算词
    expect(searchTerms("")).toEqual([]);
    expect(searchTerms("   ")).toEqual([]);
  });
});

describe("§37.2 匹配语义", () => {
  const eb1a = { id: "R-7", title: "EB-1A petition", tier: "T1" };

  it("eb1 命中 EB-1A、h1b 命中 H-1B；eb2 不误命中 EB-1A", () => {
    expect(matchesCardSearch(eb1a, "eb1")).toBe(true);
    expect(matchesCardSearch(eb1a, "EB-1")).toBe(true);
    expect(matchesCardSearch({ id: "R-8", title: "H-1B transfer" }, "h1b")).toBe(true);
    expect(matchesCardSearch(eb1a, "eb2")).toBe(false);
  });

  it("两个词 = AND：每个词都得命中某个字段，命中的字段可以不同", () => {
    const row = { id: "R-9", title: "EB-1A petition", display_title: "写推荐信", plan: ["step chen"] };
    expect(matchesCardSearch(row, "draft eb-1a")).toBe(false); // draft 没出现
    expect(matchesCardSearch(row, "petition eb-1a")).toBe(true);
    expect(matchesCardSearch(row, "推荐信 chen")).toBe(true); // display_title + plan 跨字段
    expect(matchesCardSearch(row, "推荐信 lawyer")).toBe(false);
  });

  it("空 / 纯空白查询直通", () => {
    expect(matchesCardSearch(eb1a, "")).toBe(true);
    expect(matchesCardSearch(eb1a, "   ")).toBe(true);
    expect(matchesCardSearch({}, "")).toBe(true);
  });

  it("字段自身的分隔符也剥掉：查询 'petitiondraft' 命中 'petition draft'", () => {
    expect(matchesCardSearch({ id: "R-1", title: "petition draft" }, "petitiondraft")).toBe(true);
  });

  it("没有任何可搜字段的行对非空查询不命中（不崩）", () => {
    expect(matchesCardSearch({}, "x")).toBe(false);
    expect(matchesCardSearch({ plan: null, sources: [null] }, "x")).toBe(false);
  });
});

describe("§37.2 词表：display_title / former_titles / notes_text / plan / dod / final_draft / agent_name", () => {
  it("卡面渲染的是 display_title——搜看见的名字要能命中", () => {
    const renamed = { id: "R-10", title: "https://example.com/x/y", display_title: "EB-1A petition draft" };
    expect(matchesCardSearch(renamed, "petition")).toBe(true);
  });

  it("改名后旧名仍可搜（former_titles）", () => {
    const row = { id: "R-11", title: "frozen", display_title: "新名字", former_titles: ["green card memo", "旧名"] };
    expect(matchesCardSearch(row, "memo")).toBe(true);
    expect(matchesCardSearch(row, "旧名")).toBe(true);
    expect(matchesCardSearch(row, "新名字")).toBe(true);
  });

  it("plan / dod 数组逐条可搜", () => {
    expect(matchesCardSearch({ id: "R-12", plan: ["call lawyer", "file form"] }, "lawyer")).toBe(true);
    expect(matchesCardSearch({ id: "R-13", dod: ["PR merged"] }, "merged")).toBe(true);
  });

  it("definition_of_done 只是容错兜底（lane 行今天一律发 dod，没有行带这个键）——出现了也照收，不是现役词表", () => {
    expect(matchesCardSearch({ id: "R-14", definition_of_done: ["tests green"] }, "green")).toBe(true);
  });

  it("notes_text（折叠备注）/ final_draft / agent_name / delivered_summary 可搜", () => {
    expect(matchesCardSearch({ id: "R-15", notes_text: "[radar] 并入 R-3「合同」" }, "合同")).toBe(true);
    expect(matchesCardSearch({ id: "R-16", final_draft: "Dear Prof. Smith" }, "smith")).toBe(true);
    expect(matchesCardSearch({ id: "R-17", agent_name: "zai-r17" }, "zair17")).toBe(true);
    expect(matchesCardSearch({ id: "R-18", delivered_summary: "已交付 README" }, "readme")).toBe(true);
  });

  it("web 追加的字段不回退：work_id / display_id（§60）、tier / type、sources 的 who / quote / channel", () => {
    const row = {
      id: "P-012", work_id: "R-280", display_id: "R-280", tier: "T2", type: "paperwork",
      sources: [{ who: "Alice", channel: "slack", quote: "please file it" }],
    };
    for (const q of ["r280", "R-280", "P012", "t2", "paperwork", "alice", "slack", "file it"]) {
      expect(matchesCardSearch(row, q), q).toBe(true);
    }
  });

  it("searchHaystack：每个非空字段一条、已归一化；非字符串值静默跳过", () => {
    const hay = searchHaystack({ id: "R-1", title: "EB-1A", plan: ["A b", 3, null], processing: true, cost_usd: 1.5 });
    expect(hay).toEqual(["r1", "eb1a", "ab"]);
  });
});
