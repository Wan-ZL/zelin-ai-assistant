// 多选态选中卡的 accent 淡底必须在指针悬停时也可见（CONTRACT §54.1 2026-09-05 追记 (a)）——drift-pin。
// animations.css 的 `.task-card:hover` / `.task-card.is-context-open`（特异度 (0,2,0)）在 main.tsx 里
// 后于 board.css 加载；`.task-card.is-selected` 若只写裸形也是 (0,2,0)，后加载者胜，鼠标用户点卡的那一刻
// （指针必然在卡上）看不到淡底——PR #258 review 用真浏览器 computed-style 抓到的。jsdom 没有级联，
// 这里钉 board.css 文本：选中规则带 `:hover` / `.is-context-open` 变体（(0,3,0)），需输入行的橙色左边
// 在选中时由 `.task-card.is-blocked.is-selected` 保留。真实级联由 e2e / review probe 验。
import { describe, expect, it } from "vitest";
import animationsCss from "./animations.css?raw";
import boardCss from "./board.css?raw";

const strip = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

/** 取 `selectorList { body }` 的 body（选择器列表逐字匹配，空白归一） */
function ruleBody(css: string, selectors: string): string | null {
  const clean = strip(css).replace(/\s+/g, " ");
  const idx = clean.indexOf(`${selectors} {`);
  if (idx < 0) return null;
  const open = clean.indexOf("{", idx);
  return clean.slice(open + 1, clean.indexOf("}", open)).trim();
}

describe("selected card beats hover in the cascade (board.css)", () => {
  it("选中规则带 :hover 与 .is-context-open 变体，设 accent 边 + accent-soft 底", () => {
    const body = ruleBody(boardCss, ".task-card.is-selected, .task-card.is-selected:hover, .task-card.is-selected.is-context-open");
    expect(body).not.toBeNull();
    expect(body).toContain("border-color: var(--accent)");
    expect(body).toContain("background: var(--accent-soft)");
  });

  it("animations.css 的 hover 规则仍是 (0,2,0) 的裸 .task-card:hover——被压过的前提没变", () => {
    expect(ruleBody(animationsCss, ".task-card:hover")).toContain("background: var(--surface-raised)");
    expect(ruleBody(animationsCss, ".task-card.is-context-open")).toContain("background: var(--surface-raised)");
    // 不许有人在 animations.css 里另写一条更高特异度的 hover 规则把选中态再盖回去
    expect(strip(animationsCss)).not.toMatch(/\.task-card\.[a-z-]+:hover/);
  });

  it("需输入行的橙色左边在选中时保留（.is-blocked.is-selected 在选中规则之后）", () => {
    const body = ruleBody(boardCss, ".task-card.is-blocked.is-selected");
    expect(body).toContain("border-left-color: var(--warning)");
    const clean = strip(boardCss);
    expect(clean.indexOf(".task-card.is-blocked.is-selected")).toBeGreaterThan(clean.indexOf(".task-card.is-selected:hover"));
  });
});
