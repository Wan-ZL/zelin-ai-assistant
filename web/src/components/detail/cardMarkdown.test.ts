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
    expect(md).toContain("[radar] 又提了一次");
    expect(md).toContain("## 成稿");
    expect(md.endsWith("\n")).toBe(true);
  });

  it("omits empty sections and does not leak unknown fields", () => {
    const detail = { id: "R-7", internal_secret_field: "nope" } as unknown as CardDetail;
    const md = cardToMarkdown(detail, text);
    expect(md).toContain("# R-7");
    expect(md).not.toContain("internal_secret_field");
    expect(md).not.toContain("## 计划");
  });
});
