// 显示偏好落地（CONTRACT §54.1 第 12 项）：server 三键 → <html> data-* → tokens.css 的三个变量。
// 这里钉 (a) applier 只写三个白名单属性 + 首帧缓存，(b) tokens.css 里三把旋钮的默认值与每一档的映射
// 都在、且只有这三个变量是旋钮，(c) 字体平滑不再强制 grayscale、字体栈以 -apple-system 领头并含
// SF Pro Text / PingFang SC，(d) 全站 CSS/TSX 没有任何字面 hairline 描边（0.5px / 1px / 1.5px 的
// border / outline）——描边只许 var(--stroke-w)，(e) 组件 CSS 没有字面 font-size / font-weight
// （全部 calc(… * var(--text-scale)) / var(--w-…)），否则「一个变量全站缩放」是假话。
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import tokensCss from "./styles/tokens.css?raw";
import { applyDisplayPrefs, DISPLAY_STORAGE_KEY, prefsOf, readAppliedDisplayPrefs } from "./displayPrefs";

// vite 的 import.meta.glob：web 侧不许 import node:*，用它把 web/src 全部样式与组件原文读进来做 lint
const SOURCES: Record<string, string> = import.meta.glob(
  ["/src/**/*.css", "/src/**/*.tsx"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

const stripComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");
const tokens = stripComments(tokensCss);

describe("applyDisplayPrefs", () => {
  beforeEach(() => {
    window.localStorage.clear();
    for (const key of ["textSize", "textWeight", "stroke"]) delete document.documentElement.dataset[key];
  });
  afterEach(() => {
    for (const key of ["textSize", "textWeight", "stroke"]) delete document.documentElement.dataset[key];
  });

  it("writes exactly the three data-* attributes on <html> and caches them for the first frame", () => {
    applyDisplayPrefs({ text_size: "l", text_weight: "bold", stroke: "thick" });
    const html = document.documentElement;
    expect(html.getAttribute("data-text-size")).toBe("l");
    expect(html.getAttribute("data-text-weight")).toBe("bold");
    expect(html.getAttribute("data-stroke")).toBe("thick");
    expect(JSON.parse(window.localStorage.getItem(DISPLAY_STORAGE_KEY) ?? "null"))
      .toEqual({ text_size: "l", text_weight: "bold", stroke: "thick" });
    expect(readAppliedDisplayPrefs()).toEqual({ text_size: "l", text_weight: "bold", stroke: "thick" });
  });

  it("an empty value removes the attribute (tokens.css default tier takes over)", () => {
    applyDisplayPrefs({ text_size: "xl", text_weight: "medium", stroke: "thin" });
    applyDisplayPrefs({ text_size: "", text_weight: "regular", stroke: "" });
    expect(document.documentElement.hasAttribute("data-text-size")).toBe(false);
    expect(document.documentElement.getAttribute("data-text-weight")).toBe("regular");
    expect(document.documentElement.hasAttribute("data-stroke")).toBe(false);
  });

  it("prefsOf keeps only the three wire keys out of a snapshot", () => {
    expect(prefsOf({ text_size: "s", text_weight: "regular", stroke: "normal" })).toEqual({
      text_size: "s", text_weight: "regular", stroke: "normal",
    });
  });
});

describe("tokens.css：三把旋钮", () => {
  it("defaults = the visual-golden capture condition (scale 1 / shift 0 / 1.5px)", () => {
    expect(tokens).toContain("--text-scale: 1;");
    expect(tokens).toContain("--weight-shift: 0;");
    expect(tokens).toContain("--stroke-w: 1.5px;");
  });

  it("every non-default tier of the server vocabulary has a :root[data-…] mapping", () => {
    const expectations: Array<[string, string, string]> = [
      ["data-text-size", "s", "--text-scale: 0.9"],
      ["data-text-size", "l", "--text-scale: 1.1"],
      ["data-text-size", "xl", "--text-scale: 1.25"],
      ["data-text-weight", "medium", "--weight-shift: 100"],
      ["data-text-weight", "bold", "--weight-shift: 200"],
      ["data-stroke", "thin", "--stroke-w: 1px"],
      ["data-stroke", "thick", "--stroke-w: 2px"],
    ];
    for (const [attr, value, decl] of expectations) {
      const re = new RegExp(`:root\\[${attr}="${value}"\\]\\s*\\{\\s*${decl.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")};`);
      expect(tokens, `${attr}=${value} → ${decl}`).toMatch(re);
    }
  });

  it("prefers-contrast: more bumps the stroke one tier (thin→1.5, normal→2, thick→2.5)", () => {
    const start = tokens.indexOf("@media (prefers-contrast: more)");
    expect(start).toBeGreaterThan(-1);
    const block = tokens.slice(start, tokens.indexOf("}\n}", start));
    expect(block).toMatch(/:root\[data-stroke="thin"\]\s*\{\s*--stroke-w: 1\.5px;/);
    expect(block).toMatch(/:root:not\(\[data-stroke\]\),\s*:root\[data-stroke="normal"\]\s*\{\s*--stroke-w: 2px;/);
    expect(block).toMatch(/:root\[data-stroke="thick"\]\s*\{\s*--stroke-w: 2\.5px;/);
  });

  it("the three knobs are the only knobs: no other :root[data-…] variable mapping exists", () => {
    const mapped = new Set<string>();
    for (const m of tokens.matchAll(/:root(?::not\(\[data-stroke\]\),\s*:root)?\[data-[a-z-]+="[a-z]+"\]\s*\{\s*(--[a-z-]+):/g)) {
      mapped.add(m[1]);
    }
    expect([...mapped].sort()).toEqual(["--stroke-w", "--text-scale", "--weight-shift"]);
  });

  it("font smoothing is the platform default (never forced grayscale) and the stack leads with the system font", () => {
    expect(tokens).not.toMatch(/-webkit-font-smoothing:\s*antialiased/);
    expect(tokens).toMatch(/--font-sans:\s*-apple-system,\s*BlinkMacSystemFont,\s*"SF Pro Text",\s*"PingFang SC"/);
  });
});

describe("web/src：描边与字号字重只经 token", () => {
  const files = Object.entries(SOURCES).filter(([path]) => !path.endsWith("tokens.css") && !/\.test\.tsx?$/.test(path));

  it("the lint actually sees the stylesheets and components", () => {
    expect(files.length).toBeGreaterThan(20);
    expect(files.some(([path]) => path.endsWith("board.css"))).toBe(true);
    expect(files.some(([path]) => path.endsWith("cardChrome.tsx"))).toBe(true);
  });

  it("no hard-coded hairline border / outline anywhere outside tokens.css", () => {
    // border / border-<side> / border-width / outline with a literal 0.5px, 1px or 1.5px width
    const hairline = /\b(?:border(?:-(?:top|right|bottom|left|inline|block|width))?|outline)\s*:\s*["']?(?:0?\.5|1|1\.5)px\b/g;
    const offenders: string[] = [];
    for (const [path, source] of files) {
      for (const m of stripComments(source).matchAll(hairline)) offenders.push(`${path}: ${m[0]}`);
    }
    expect(offenders).toEqual([]);
  });

  it("borders that carry a width use var(--stroke-w)", () => {
    let count = 0;
    for (const [, source] of files) count += (stripComments(source).match(/var\(--stroke-w\)/g) ?? []).length;
    expect(count).toBeGreaterThan(40);
    expect(tokens).not.toContain("--border-hairline");
  });

  it("no literal font-size / font-weight in any stylesheet (only calc(… * var(--text-scale)) / var(--w-…))", () => {
    const offenders: string[] = [];
    for (const [path, source] of files.filter(([p]) => p.endsWith(".css"))) {
      const clean = stripComments(source);
      for (const m of clean.matchAll(/font-size\s*:\s*([^;]+);/g)) {
        if (!/^calc\([\d.]+px \* var\(--text-scale\)\)$/.test(m[1].trim()) && !/^[\d.]+em$/.test(m[1].trim())) {
          offenders.push(`${path}: font-size: ${m[1].trim()}`);
        }
      }
      for (const m of clean.matchAll(/font-weight\s*:\s*([^;]+);/g)) {
        if (!/^var\(--w-(regular|medium|semibold|bold)\)$/.test(m[1].trim())) offenders.push(`${path}: font-weight: ${m[1].trim()}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
