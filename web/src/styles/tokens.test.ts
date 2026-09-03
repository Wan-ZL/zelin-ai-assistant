// tokens.css 的主题纪律 drift-pin（CONTRACT §54.4 / §66.2 theme:default / §66.3）。
// owner 2026-09-02 (b)：原生默认浅色，web 不得默认深色。落法 = index.html 首帧脚本在没有存储偏好时
// 显式写 dataset.theme = "light"，tokens.css 因此只有 :root（light）与 :root[data-theme="dark"] 两块，
// **没有** prefers-color-scheme 兜底块（此前那块要与显式块逐字双写，现在整块退役）。
// 另钉：两个主题的窗口底与状态色点经 var(--native-…) 取 @generated 块的原生数值（§66.3 单源），
// 布局定点 token 也在生成块里齐全（layout:* 探针消费它们）。
import indexHtml from "../../index.html?raw";
import tokensCss from "./tokens.css?raw";
import { describe, expect, it } from "vitest";

/** 取 `selector {` 之后到最近一个 `}` 之前的块体（token 块内部无嵌套花括号）。 */
function blockBody(css: string, selector: string): string {
  const start = css.indexOf(selector);
  if (start < 0) throw new Error(`tokens.css 里找不到选择器：${selector}`);
  const open = css.indexOf("{", start + selector.length - 1);
  const close = css.indexOf("}", open);
  if (open < 0 || close < 0) throw new Error(`选择器 ${selector} 的块不完整`);
  return css.slice(open + 1, close);
}

/** 块体 → 有序的 [属性, 值] 对（剥注释，忽略空行）。 */
function declarations(body: string): Array<[string, string]> {
  return body
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split(";")
    .map((line) => line.trim())
    .filter((line) => line.includes(":"))
    .map((line) => {
      const cut = line.indexOf(":");
      return [line.slice(0, cut).trim(), line.slice(cut + 1).trim()] as [string, string];
    });
}

// 头注里也会提到选择器名——先剥注释再找块，免得 indexOf 命中注释
const stripped = tokensCss.replace(/\/\*[\s\S]*?\*\//g, "");
const light = declarations(blockBody(stripped, ":root {"));
const dark = declarations(blockBody(stripped, ':root[data-theme="dark"]'));
const lookup = (block: Array<[string, string]>, name: string) => block.find(([k]) => k === name)?.[1];

describe("主题默认浅色（theme:default）", () => {
  it("tokens.css 没有 prefers-color-scheme 兜底块——主题只由 data-theme 决定", () => {
    expect(stripped).not.toContain("prefers-color-scheme");
    expect(stripped).not.toContain(':root:not([data-theme="light"])');
  });

  it("light 块是 :root 默认（color-scheme: light 开头），dark 块只在显式 data-theme=dark 下", () => {
    expect(light[0]).toEqual(["color-scheme", "light"]);
    expect(dark[0]).toEqual(["color-scheme", "dark"]);
    expect(dark.length).toBeGreaterThan(50);
  });

  it("index.html 首帧脚本：无存储偏好时显式写 dataset.theme = \"light\"（不跟随系统深色）", () => {
    expect(indexHtml).toContain('dataset.theme = "light"');
    expect(indexHtml).not.toContain("prefers-color-scheme");
  });
});

describe("原生数值单源（§66.3 @generated 块 → 语义 token）", () => {
  it("两个主题的窗口底取原生 windowBackgroundColor 解析值", () => {
    expect(lookup(light, "--bg")).toBe("var(--native-color-window-background-light)");
    expect(lookup(dark, "--bg")).toBe("var(--native-color-window-background-dark)");
  });

  it("状态色点（--status-*）逐色取原生系统色", () => {
    for (const [name, hue] of [["progress", "orange"], ["review", "green"], ["done", "purple"], ["backlog", "gray"]] as const) {
      expect(lookup(light, `--status-${name}`)).toBe(`var(--native-color-${hue}-light)`);
      expect(lookup(dark, `--status-${name}`)).toBe(`var(--native-color-${hue}-dark)`);
    }
  });

  it("布局定点 token 在生成块里齐全（列宽 400 / 书立条 44 / 列距 12 / 内边距 16 / 侧栏 48）", () => {
    for (const [token, value] of [
      ["--native-layout-lane-width", "400px"], ["--native-layout-strip-width", "44px"], ["--native-layout-lane-gap", "12px"],
      ["--native-layout-board-padding", "16px"], ["--native-layout-rail-collapsed-width", "48px"], ["--native-default-theme", "light"],
    ]) {
      expect(stripped).toContain(`${token}: ${value};`);
    }
  });
});
