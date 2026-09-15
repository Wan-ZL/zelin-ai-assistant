// 窗口从不整体滚动（owner 决策 D42，CONTRACT §54.4 2026-09-06 追记 + 2026-09-15 追记；issue #359）。
// D42 只写了「.shell 一屏高」，靠的是 `height: 100vh` 与 `#root { min-height: 100vh }` 各说各话——
// 壳里任何压不扁的盒子（横幅摞高过一屏、某页忘了进 .shell-main）一溢出，**文档**就成了第二个滚动容器：
// 滚到 .shell-main 的底还能接着滚，导航栏那片灰底随壳滚出窗口、只剩上面一截，内容下方露出一大片空白。
// 自此视口高的真源只有 html 一处，一路 100% 传到 .shell，纵向溢出在 .shell-body 与视口两道夹掉；
// 横向仍 auto——窄于 720（.shell 的 min-width）时靠文档横向滚动逃生（§54.4 (f)）。
// jsdom 没有布局，这里只钉样式文本；真实几何由 e2e/settingsScroll.spec.ts 在真浏览器里验。
import { describe, expect, it } from "vitest";
import shellCss from "./shell.css?raw";
import tokensCss from "./tokens.css?raw";

const strip = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

/** 取 `selector { body }` 的 body（选择器逐字匹配，空白归一） */
function ruleBody(css: string, selector: string): string {
  const clean = strip(css).replace(/\s+/g, " ");
  const idx = clean.indexOf(`${selector} {`);
  if (idx < 0) throw new Error(`找不到规则：${selector}`);
  const open = clean.indexOf("{", idx);
  const close = clean.indexOf("}", open);
  return clean.slice(open + 1, close).trim();
}

describe("窗口从不整体滚动（D42 / #359）", () => {
  it("视口高的真源在 html，一路 100% 传到 #root 与 .shell", () => {
    expect(ruleBody(tokensCss, "html, body, #root")).toContain("height: 100%");
    expect(ruleBody(shellCss, ".shell")).toContain("height: 100%");
    // #root 的 min-height: 100vh 是文档整体滚动时代的遗物：它只会让文档比窗口更高
    expect(strip(tokensCss).replace(/\s+/g, " ")).not.toContain("min-height: 100vh");
  });

  it("纵向溢出锁死、横向留给 720 下限逃生", () => {
    const html = ruleBody(tokensCss, "html");
    expect(html).toContain("overflow-y: hidden");
    expect(html).toContain("overflow-x: auto");
  });

  it("横幅摞高过一屏时溢出在 .shell-body 夹掉，不许漏给文档", () => {
    // clip 而不是 hidden：hidden 会让 .shell-body 变成滚动容器（程序滚动能把顶栏推走），clip 只裁不滚
    expect(ruleBody(shellCss, ".shell-body")).toContain("overflow: clip");
  });

  it("页面内容仍只在 .shell-main 里滚", () => {
    const main = ruleBody(shellCss, ".shell-main");
    expect(main).toContain("overflow: auto");
    expect(main).toContain("min-height: 0");
  });
});
