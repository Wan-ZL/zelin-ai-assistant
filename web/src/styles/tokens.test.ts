// tokens.css 的暗色双写 drift-pin。
// 暗色 token 必须写两遍：显式 `[data-theme="dark"]`（用户选过）与
// `prefers-color-scheme: dark` + 未显式选 light（系统偏好兜底）。CSS 没有
// "别名一组声明"的写法，所以那 60 对值逐字抄了两份，此前只有一句"改一处必改
// 两处"的注释守着——drift 只在暗色主题下静默发作（改了显式块、没改兜底块 =
// 只有跟随系统的人看到旧色，没人会报 bug）。这里把两块钉成必须逐字相等。
//
// 只做相等断言，不重构 CSS：颜色值本身是 owner 验收过的（见 tokens.css 头注
// 的语义色阶梯规则），本测试不对取值有任何意见。
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

const explicit = declarations(blockBody(tokensCss, ':root[data-theme="dark"]'));
const systemFallback = declarations(
  blockBody(tokensCss, ':root:not([data-theme="light"])'),
);

describe("暗色 token 的两份声明", () => {
  it("解析出的声明数量是真实的（防正则空转让断言变废话）", () => {
    expect(explicit.length).toBeGreaterThan(50);
    expect(systemFallback.length).toBeGreaterThan(50);
  });

  it("显式 dark 块与 prefers-color-scheme 兜底块逐字相等", () => {
    expect(systemFallback).toEqual(explicit);
  });

  it("两块都以 color-scheme: dark 开头（缺它表单控件仍是亮色）", () => {
    for (const block of [explicit, systemFallback]) {
      expect(block[0]).toEqual(["color-scheme", "dark"]);
    }
  });
});
