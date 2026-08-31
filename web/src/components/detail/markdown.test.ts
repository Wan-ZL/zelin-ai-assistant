// markdown 解析器行为测试（fork 组件必须自带行为测试——BUILD-CONTRACT §0.5）。
import { describe, expect, it } from "vitest";
import { parseInline, parseMarkdown, sanitizeUrl, stripHtmlComments, type BlockNode } from "./markdown";

function types(blocks: BlockNode[]): string[] {
  return blocks.map((b) => b.type);
}

describe("parseMarkdown blocks", () => {
  it("parses headings, paragraphs, hr and fenced code", () => {
    const blocks = parseMarkdown("# 标题\n\ntext line\n\n---\n\n```py\nprint(1)\n```\n");
    expect(types(blocks)).toEqual(["heading", "paragraph", "hr", "codeBlock"]);
    expect(blocks[0]).toMatchObject({ depth: 1 });
    expect(blocks[3]).toMatchObject({ language: "py", value: "print(1)" });
  });

  it("keeps mermaid fences as codeBlock language mermaid", () => {
    const blocks = parseMarkdown("```mermaid\ngraph TD; A-->B\n```");
    expect(blocks[0]).toMatchObject({ type: "codeBlock", language: "mermaid", value: "graph TD; A-->B" });
  });

  it("parses nested lists with ordered start and looseness", () => {
    const blocks = parseMarkdown("2. one\n3. two\n   - sub\n");
    const list = blocks[0] as Extract<BlockNode, { type: "list" }>;
    expect(list.ordered).toBe(true);
    expect(list.start).toBe(2);
    expect(list.loose).toBe(false);
    expect(list.items).toHaveLength(2);
    expect(list.items[1].some((b) => b.type === "list")).toBe(true);
  });

  it("parses blockquotes recursively", () => {
    const blocks = parseMarkdown("> quoted **bold**\n> second line");
    expect(blocks[0].type).toBe("blockquote");
  });

  it("parses GFM pipe tables with alignment", () => {
    const blocks = parseMarkdown("| a | b |\n|:--|--:|\n| 1 | 2 |\n");
    const table = blocks[0] as Extract<BlockNode, { type: "table" }>;
    expect(table.align).toEqual(["left", "right"]);
    expect(table.header).toHaveLength(2);
    expect(table.rows).toHaveLength(1);
  });

  it("turns single newlines inside a paragraph into breaks (remark-breaks semantics)", () => {
    const blocks = parseMarkdown("line1\nline2");
    const para = blocks[0] as Extract<BlockNode, { type: "paragraph" }>;
    expect(para.children.some((n) => n.type === "break")).toBe(true);
  });

  it("strips HTML comments and keeps other raw HTML as literal text", () => {
    expect(stripHtmlComments("a <!-- hidden --> b")).toBe("a  b");
    const blocks = parseMarkdown("before <script>alert(1)</script> after");
    const para = blocks[0] as Extract<BlockNode, { type: "paragraph" }>;
    // 原始 HTML 绝不产出元素节点——只能是字面文本
    expect(para.children.every((n) => n.type === "text")).toBe(true);
  });
});

describe("parseInline", () => {
  it("parses code, strong, em, del, links and images", () => {
    const nodes = parseInline("`c` **b** *i* ~~d~~ [t](https://x.dev) ![a](https://x.dev/i.png)");
    expect(nodes.map((n) => n.type)).toEqual(
      ["code", "text", "strong", "text", "em", "text", "del", "text", "link", "text", "image"],
    );
  });

  it("does not emphasize snake_case underscores", () => {
    const nodes = parseInline("foo_bar_baz");
    expect(nodes).toEqual([{ type: "text", value: "foo_bar_baz" }]);
  });

  it("handles backslash escapes", () => {
    expect(parseInline("\\*not em\\*")).toEqual([{ type: "text", value: "*not em*" }]);
  });
});

describe("sanitizeUrl", () => {
  it("allows http(s), mailto and relative urls", () => {
    expect(sanitizeUrl("https://example.dev/x")).toBe("https://example.dev/x");
    expect(sanitizeUrl("mailto:a@b.c")).toBe("mailto:a@b.c");
    expect(sanitizeUrl("/files/deliverables/R-1/a.html")).toBe("/files/deliverables/R-1/a.html");
    expect(sanitizeUrl("#anchor")).toBe("#anchor");
  });

  it("rejects javascript:, data:, vbscript: and protocol-relative urls", () => {
    expect(sanitizeUrl("javascript:alert(1)")).toBeUndefined();
    expect(sanitizeUrl("JavaScript:alert(1)")).toBeUndefined();
    expect(sanitizeUrl("data:text/html,<b>x</b>")).toBeUndefined();
    expect(sanitizeUrl("vbscript:x")).toBeUndefined();
    expect(sanitizeUrl("//evil.dev/x")).toBeUndefined();
  });

  it("restricts image srcs to http(s)/relative (no mailto)", () => {
    expect(sanitizeUrl("mailto:a@b.c", true)).toBeUndefined();
    expect(sanitizeUrl("https://x.dev/i.png", true)).toBe("https://x.dev/i.png");
  });
});
