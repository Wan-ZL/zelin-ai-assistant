// 字号/字重梯的 drift-pin（CONTRACT §54.1 第 10 项：web 排版跟原生看板一致，truth = tokens.css）。
// 本文件钉 CSS ↔ 对照表：tokens.css 的每个 --type-* 值与 typeScale.ts 逐字相等、字重/字号
// 与表里声明的 Swift size/weight 一致、每个 token 至少被一处组件 CSS 消费（防死 token）、
// board.css（镜像原生卡面/列的那份）不再出现字面 font-size / font-weight——组件只许
// `font: var(--type-…)`。shell.css / chrome.css 里 web 独有的面（过滤 chips、排序 select、
// 回收站页标题、壳内菜单）没有原生对应，不在此钉。
// 第三方（表 ↔ mac/Sources 被引用的那一行真的写着这个 size/weight/design）由
// tests/test_web_type_scale_mirror.py 钉——web 侧不许 import node:*（@types/node 不在白名单）。
import { describe, expect, it } from "vitest";
import boardCss from "./board.css?raw";
import chromeCss from "../components/chrome/chrome.css?raw";
import shellCss from "./shell.css?raw";
import tokensCss from "./tokens.css?raw";
import { TYPE_SCALE, WEIGHT_OF } from "./typeScale";

/** tokens.css 里所有 `--type-*: value;` 声明（去注释） */
function typeTokens(css: string): Map<string, string> {
  const out = new Map<string, string>();
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, "");
  for (const m of clean.matchAll(/(--type-[a-z-]+)\s*:\s*([^;]+);/g)) {
    out.set(m[1], m[2].trim());
  }
  return out;
}

const cssTokens = typeTokens(tokensCss);
const consumerCss = [boardCss, shellCss, chromeCss].join("\n");

describe("type scale（tokens.css ↔ typeScale.ts）", () => {
  it("对照表非空且 token 名唯一", () => {
    expect(TYPE_SCALE.length).toBeGreaterThanOrEqual(20);
    expect(new Set(TYPE_SCALE.map((r) => r.token)).size).toBe(TYPE_SCALE.length);
  });

  it("tokens.css 的每个 --type-* 都在表里，表里的每个都在 tokens.css 里", () => {
    expect([...cssTokens.keys()].sort()).toEqual(TYPE_SCALE.map((r) => r.token).sort());
  });

  for (const role of TYPE_SCALE) {
    describe(role.token, () => {
      it("CSS 值与表逐字相等", () => {
        expect(cssTokens.get(role.token)).toBe(role.font);
      });

      it("font 简写的字重/字号/字族与表里声明的 Swift size/weight/design 一致", () => {
        const m = /^(\d{3}) (\d+)px\/[\d.]+(?:px)? var\(--font-(sans|mono)\)$/.exec(role.font);
        expect(m, `font 简写格式不对：${role.font}`).not.toBeNull();
        const [, weight, size, family] = m!;
        expect(Number(weight)).toBe(WEIGHT_OF[role.swift.weight]);
        expect(Number(size)).toBe(role.swift.size);
        expect(family).toBe(role.swift.mono ? "mono" : "sans");
      });

      it("至少被一处组件 CSS 消费", () => {
        expect(consumerCss).toContain(`var(${role.token})`);
      });
    });
  }

  it("board.css 不再有字面 font-size / font-weight（组件只许 font: var(--type-…)）", () => {
    const clean = boardCss.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(clean.match(/font-size\s*:/g) ?? []).toEqual([]);
    expect(clean.match(/font-weight\s*:/g) ?? []).toEqual([]);
  });

  it("字体栈单源：:root 的 font-family 走 --font-sans", () => {
    expect(tokensCss).toContain("font-family: var(--font-sans)");
    expect(cssTokens.size).toBeGreaterThan(0);
  });
});
