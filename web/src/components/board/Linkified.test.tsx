// 纯文本 URL 变链接（原生 Utils.swift:877-889 linkified 的 web 落点，§54.1 追记）：
//   · 无 URL → 原样字符串（没有多出任何元素）；
//   · https?:// 段 → <a target=_blank rel=noreferrer class=linkified>，其余仍是文本节点；
//   · 「」()（） 与尾随标点（。，.,;:!? 右引号）不算进 URL（中文引文常用引号 / 括号包着链接，NSDataDetector 同判）；
//   · javascript: / data: 不是链接（正则只认 https?://；href 再过 sanitizeUrl 白名单）；
//   · 两个 URL 各自成链，中间文字保留。
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Linkified, linkifyParts } from "./Linkified";

afterEach(cleanup);

describe("linkifyParts (pure)", () => {
  it("no URL → single text part, verbatim", () => {
    expect(linkifyParts("大白话摘要一句")).toEqual([{ kind: "text", value: "大白话摘要一句" }]);
    expect(linkifyParts("")).toEqual([{ kind: "text", value: "" }]);
  });

  it("trailing 」 / ）/ 。 stay outside the URL", () => {
    expect(linkifyParts("见「https://example.dev/a/b」")).toEqual([
      { kind: "text", value: "见「" },
      { kind: "link", value: "https://example.dev/a/b" },
      { kind: "text", value: "」" },
    ]);
    expect(linkifyParts("（https://example.dev/x）。")).toEqual([
      { kind: "text", value: "（" },
      { kind: "link", value: "https://example.dev/x" },
      { kind: "text", value: "）。" },
    ]);
    expect(linkifyParts("see https://example.dev/x.")).toEqual([
      { kind: "text", value: "see " },
      { kind: "link", value: "https://example.dev/x" },
      { kind: "text", value: "." },
    ]);
  });

  it("javascript: / data: / bare host are text, not links", () => {
    expect(linkifyParts("javascript:alert(1)")).toEqual([{ kind: "text", value: "javascript:alert(1)" }]);
    expect(linkifyParts("data:text/html,<b>x</b>")).toEqual([{ kind: "text", value: "data:text/html,<b>x</b>" }]);
    expect(linkifyParts("example.dev/no-scheme")).toEqual([{ kind: "text", value: "example.dev/no-scheme" }]);
    expect(linkifyParts("https://. 不是链接")).toEqual([{ kind: "text", value: "https://. 不是链接" }]);
  });

  it("two URLs each become a link; text between is kept", () => {
    expect(linkifyParts("A http://a.dev/1 B https://b.dev/2?x=1&y=2 C")).toEqual([
      { kind: "text", value: "A " },
      { kind: "link", value: "http://a.dev/1" },
      { kind: "text", value: " B " },
      { kind: "link", value: "https://b.dev/2?x=1&y=2" },
      { kind: "text", value: " C" },
    ]);
  });
});

describe("<Linkified>", () => {
  it("no URL → the plain string, no extra element", () => {
    const { container } = render(<p><Linkified text="大白话摘要一句" /></p>);
    expect(container.querySelector("p")?.innerHTML).toBe("大白话摘要一句");
    expect(container.querySelector("a")).toBeNull();
  });

  it("URL → anchor with target=_blank rel=noreferrer; trailing 」 outside", () => {
    const { container } = render(<p><Linkified text="见「https://example.dev/a」" /></p>);
    const a = container.querySelector("a");
    expect(a?.getAttribute("href")).toBe("https://example.dev/a");
    expect(a?.textContent).toBe("https://example.dev/a");
    expect(a?.getAttribute("target")).toBe("_blank");
    expect(a?.getAttribute("rel")).toBe("noreferrer");
    expect(a?.className).toBe("linkified");
    expect(container.querySelector("p")?.textContent).toBe("见「https://example.dev/a」");
  });

  it("javascript: never renders an anchor", () => {
    const { container } = render(<p><Linkified text="javascript:alert(1) https://ok.dev" /></p>);
    const anchors = container.querySelectorAll("a");
    expect(anchors).toHaveLength(1);
    expect(anchors[0].getAttribute("href")).toBe("https://ok.dev");
  });

  it("two URLs → two anchors", () => {
    const { container } = render(<p><Linkified text="http://a.dev/1 和 https://b.dev/2" /></p>);
    const hrefs = Array.from(container.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["http://a.dev/1", "https://b.dev/2"]);
    expect(container.querySelector("p")?.textContent).toBe("http://a.dev/1 和 https://b.dev/2");
  });
});
