// §37.1 摘要优先面的标题链（原生 Contract.swift displaySummary：钦定名 > summary > display_title > title）——
// 链的每一级、每一级为空 / 坏类型时的降级、以及 user_titled 只认 true 都钉死。
import { describe, expect, it } from "vitest";
import { cardHeadline } from "./cardHeadline";

describe("cardHeadline (§37 displaySummary chain)", () => {
  it("summary 压过 display_title：LLM 短名不许挤掉大白话摘要", () => {
    expect(cardHeadline({ title: "raw", summary: "大白话摘要一句", display_title: "短名字" })).toBe("大白话摘要一句");
  });

  it("user_titled=true 时钦定名压过一切", () => {
    expect(cardHeadline({ title: "raw", summary: "大白话摘要一句", display_title: "我起的名", user_titled: true })).toBe("我起的名");
  });

  it("user_titled 只认布尔 true——\"true\" / 1 / false 都不算钦定", () => {
    for (const bad of ["true", 1, false, null, undefined]) {
      expect(cardHeadline({ title: "raw", summary: "摘要", display_title: "短名字", user_titled: bad })).toBe("摘要");
    }
  });

  it("钦定但 display_title 空 → 退回 summary", () => {
    expect(cardHeadline({ title: "raw", summary: "摘要", display_title: "", user_titled: true })).toBe("摘要");
    expect(cardHeadline({ title: "raw", summary: "摘要", user_titled: true })).toBe("摘要");
  });

  it("summary 缺席 / 空白 / 坏类型 → display_title 顶上（裸 URL title 永不上面）", () => {
    expect(cardHeadline({ title: "https://x.y/z", display_title: "x.y ▸ z" })).toBe("x.y ▸ z");
    expect(cardHeadline({ title: "https://x.y/z", summary: "   ", display_title: "x.y ▸ z" })).toBe("x.y ▸ z");
    expect(cardHeadline({ title: "https://x.y/z", summary: 42, display_title: "x.y ▸ z" })).toBe("x.y ▸ z");
  });

  it("summary 与 display_title 都没有 → 冻结 title；title 也没有 → name；全空 → \"\"", () => {
    expect(cardHeadline({ title: "冻结标题" })).toBe("冻结标题");
    expect(cardHeadline({ name: "running 行名" })).toBe("running 行名");
    expect(cardHeadline({})).toBe("");
    expect(cardHeadline({ title: "", summary: "", display_title: "" })).toBe("");
  });
});
