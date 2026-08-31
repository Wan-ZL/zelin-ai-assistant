// MarkdownDocument 行为测试（fork 组件必须自带行为测试——BUILD-CONTRACT §0.5）：
// 渲染面 + 安全性质（URL 消毒、原始 HTML 字面化、注释剥除、mermaid 不直渲）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MarkdownDocument } from "./MarkdownDocument";

afterEach(cleanup);

describe("MarkdownDocument", () => {
  it("renders headings, lists and inline formatting", () => {
    const { container } = render(
      <MarkdownDocument value={"# Title\n\n- item **bold**\n- second\n\n1. ordered"} />,
    );
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Title");
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("ol li")?.textContent).toBe("ordered");
  });

  it("renders links with target=_blank rel=noreferrer and sanitized href", () => {
    const { container } = render(<MarkdownDocument value="[doc](https://example.dev/a)" />);
    const anchor = container.querySelector("a")!;
    expect(anchor.getAttribute("href")).toBe("https://example.dev/a");
    expect(anchor.getAttribute("target")).toBe("_blank");
    expect(anchor.getAttribute("rel")).toBe("noreferrer");
  });

  it("neutralizes javascript: links into plain text (no anchor)", () => {
    const { container } = render(<MarkdownDocument value="[click](javascript:alert(1))" />);
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("click");
  });

  it("never renders raw HTML as elements and strips comments", () => {
    const { container } = render(
      <MarkdownDocument value={'before <img src=x onerror="alert(1)"> <!-- gone --> after'} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(container.textContent).not.toContain("gone");
  });

  it("routes mermaid fences to the sandbox component (source fallback, never direct svg)", () => {
    const { container } = render(<MarkdownDocument value={"```mermaid\ngraph TD; A-->B\n```"} />);
    expect(container.querySelector(".zai-mermaid")).not.toBeNull();
    // 依赖缺席 → 永远 fallback：源码在 <details> 里，绝无注入的 svg
    expect(container.querySelector(".zai-mermaid svg")).toBeNull();
    expect(container.textContent).toContain("graph TD; A-->B");
  });

  it("renders GFM tables", () => {
    const { container } = render(<MarkdownDocument value={"| a | b |\n|---|---|\n| 1 | 2 |"} />);
    expect(container.querySelectorAll("th")).toHaveLength(2);
    expect(container.querySelectorAll("td")).toHaveLength(2);
  });
});
