// 复制为 Markdown 的成文测试。
import { describe, expect, it } from "vitest";
import { cardToMarkdown } from "./cardMarkdown";
import type { CardDetail } from "../../types";

const text = (zh: string, _en: string) => zh;

describe("cardToMarkdown", () => {
  it("serializes known semantic fields into a pasteable document", () => {
    const detail = {
      id: "R-101",
      title: "给 example-bench 加导出",
      lane: "needs_approval",
      tier: "T1",
      delivery_mode: "repo",
      summary: "一句话摘要。",
      plan: ["step A", "step B"],
      dod: ["有导出按钮"],
      sources: [{ who: "manager", channel: "slack", date: "2026-08-20", quote: "要能导出", ref: "slack://x" }],
      notes: "[radar] 又提了一次 [@2026-08-21T00:00:00Z]",
      final_draft: "# 成稿",
    } as unknown as CardDetail;
    const md = cardToMarkdown(detail, text);
    expect(md).toContain("# 给 example-bench 加导出");
    expect(md).toContain("- ID: R-101");
    expect(md).toContain("1. step A");
    expect(md).toContain("- [ ] 有导出按钮");
    expect(md).toContain('"要能导出"');
    // 小节标题 = 侧栏 DetailFields 的积木标签（D34 / §49 追记）：读到什么词、复制出去就是什么词；旧的 web 自造小节名不再出现
    expect(md.split("\n").filter((line) => line.startsWith("## "))).toEqual(["## 📋 要做什么", "## 怎样算办完", "## 💬 需求来自", "## 并入记录", "## 成稿"]);
    expect(md).not.toMatch(/^## (计划|验收标准|来源引文|交付总结)$/m);
    expect(md).toContain("[radar] 又提了一次");
    expect(md).toContain("## 成稿");
    expect(md.endsWith("\n")).toBe(true);
  });

  it("omits empty sections and does not leak unknown fields", () => {
    const detail = { id: "R-7", internal_secret_field: "nope" } as unknown as CardDetail;
    const md = cardToMarkdown(detail, text);
    expect(md).toContain("# R-7");
    expect(md).not.toContain("internal_secret_field");
    expect(md).not.toContain("## 📋 要做什么");
  });

  it("多行引文（D52）仍是「需求来自」下的同一个列表项：续行缩进到内容列，空行不带尾随空格", () => {
    const detail = {
      id: "R-9",
      sources: [
        { who: "zelin", channel: "quick_capture", date: "2026-09-06", quote: "给 my-bench 加导出按钮\n- 支持 CSV\n- 支持 PDF\n\n附：上周会议提过一次", ref: "inbox:1" },
        { who: "manager", channel: "slack", date: "2026-08-20", quote: "要能导出" },
      ],
    } as unknown as CardDetail;
    const md = cardToMarkdown(detail, text);
    expect(md).toContain([
      "## 💬 需求来自",
      "",
      "- zelin · quick_capture · 2026-09-06",
      '  - "给 my-bench 加导出按钮',
      "    - 支持 CSV",
      "    - 支持 PDF",
      "",
      '    附：上周会议提过一次"',
      "  - ref: inbox:1",
      "- manager · slack · 2026-08-20",
      '  - "要能导出"',
    ].join("\n"));
    // 引文自己的项目符号不会逃成小节的顶层项；单行引文的成文与此前逐字相同
    const sectionBody = md.slice(md.indexOf("## 💬 需求来自"));
    expect(sectionBody.split("\n").filter((line) => line.startsWith("- "))).toEqual(["- zelin · quick_capture · 2026-09-06", "- manager · slack · 2026-08-20"]);
    expect(md).not.toMatch(/ +$/m);
  });

  it("delivered_summary 的小节叫「交付了什么」（侧栏 ReviewRow 同词）", () => {
    const md = cardToMarkdown({ id: "R-8", delivered_summary: "已按 DoD 完成成稿" } as unknown as CardDetail, text);
    expect(md).toContain("## 交付了什么\n\n已按 DoD 完成成稿");
  });
});
