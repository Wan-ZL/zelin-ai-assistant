// 「筛选」面板的 **Tab 环**（§49 追记 2026-09-04 D31「Tab 在面板内循环」；issue #420 的孪生 R-222）。
// 此前 web/src 与 web/e2e 里对 FilterPopover 一次 Tab 都没被派发过，整段 handleKeyDown 的 Tab 分支是必然的变异幸存者
// （#427 正文自己列的最高价值空白）。#425 / #427 带来的 FilterPopover.escDelivery.test.tsx 钉的是 ⎋ 的交付路径与
// 「打开即同步聚焦」——两处都是 #420 的修复本体；这里钉的环本身**不依赖**那个修复，修复前后同样成立：
// 每条都显式 .focus() 到位再派发，不赌「打开即聚焦」是排在 rAF 里还是同步。
//   1) 焦点在环的最后一站按 Tab → 回到第一站、事件被 preventDefault；第一站按 ⇧Tab → 到最后一站；compact / tight 同一套环；
//   2) 环的中间按 Tab / ⇧Tab 不拦（fireEvent 返回 true）、焦点不动——中间靠浏览器的文档顺序自己走，面板不多管；
//   3) 第一站按 Tab、最后一站按 ⇧Tab 同样不拦——只有越出环边的那一下才被接住；
//   4) 有生效维度时页脚多出「清除（N）」，它才是最后一站——环覆盖页脚，不止 chips 那一行。
// jsdom 不实现 Tab 的默认焦点移动，所以「不拦」只能判 defaultPrevented 与焦点未动、判不出「移到了哪」；真浏览器里的
// 几何与一下就开住 e2e/headerLayout.spec.ts。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HeaderDensityContext, type HeaderDensity } from "../shell/headerDensity";
import { resetStoreForTests } from "../../store";
import { FilterBar } from "./FilterBar";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

// 与 FilterPopover.tsx 的 FOCUSABLE 同义——判例自己数一遍环的站点，不信组件的说法
const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])";

function renderAt(density: HeaderDensity) {
  return render(
    <HeaderDensityContext.Provider value={density}>
      <FilterBar />
    </HeaderDensityContext.Provider>,
  );
}

/** 开面板，返回环的站点（DOM 顺序） */
function openPanel(density: HeaderDensity = "compact") {
  renderAt(density);
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  const dialog = screen.getByRole("dialog", { name: "Filters" });
  const stops = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE));
  // Tier / 期限 / 回锅 三颗 chip + 排序 <select> + 页脚「选择」（有生效维度时再多一颗「清除」）
  expect(stops.length).toBeGreaterThanOrEqual(5);
  return stops;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
});

afterEach(cleanup);

describe("FilterPopover Tab 环（D31「Tab 在面板内循环」）", () => {
  for (const density of ["compact", "tight"] as const) {
    it(`最后一站按 Tab 回到第一站、第一站按 ⇧Tab 到最后一站，两下都被拦下 · ${density}`, () => {
      const stops = openPanel(density);
      const first = stops[0];
      const last = stops[stops.length - 1];
      expect(first).toBe(screen.getByRole("button", { name: "Filter by tier" }));
      expect(last).toBe(screen.getByRole("button", { name: "Select" }));

      last.focus();
      expect(document.activeElement).toBe(last);
      expect(fireEvent.keyDown(last, { key: "Tab" })).toBe(false); // false = defaultPrevented
      expect(document.activeElement).toBe(first);

      expect(fireEvent.keyDown(first, { key: "Tab", shiftKey: true })).toBe(false);
      expect(document.activeElement).toBe(last);
    });
  }

  it("环的中间按 Tab / ⇧Tab 不拦、焦点不动——中间的移动归浏览器的文档顺序", () => {
    const stops = openPanel();
    const middle = screen.getByRole("button", { name: "Filter by deadline" });
    expect(stops.indexOf(middle)).toBeGreaterThan(0);
    expect(stops.indexOf(middle)).toBeLessThan(stops.length - 1);

    middle.focus();
    expect(fireEvent.keyDown(middle, { key: "Tab" })).toBe(true);
    expect(document.activeElement).toBe(middle);
    expect(fireEvent.keyDown(middle, { key: "Tab", shiftKey: true })).toBe(true);
    expect(document.activeElement).toBe(middle);
  });

  it("第一站按 Tab、最后一站按 ⇧Tab 同样不拦——只有越出环边的那一下才被接住", () => {
    const stops = openPanel();
    const first = stops[0];
    const last = stops[stops.length - 1];

    first.focus();
    expect(fireEvent.keyDown(first, { key: "Tab" })).toBe(true);
    expect(document.activeElement).toBe(first);
    last.focus();
    expect(fireEvent.keyDown(last, { key: "Tab", shiftKey: true })).toBe(true);
    expect(document.activeElement).toBe(last);
  });

  it("有生效维度时页脚多出「清除（N）」：它才是最后一站，环覆盖页脚而不止 chips 那一行", () => {
    window.history.replaceState(null, "", "/?tier=T1&deadline=soon");
    const stops = openPanel();
    const clear = screen.getByRole("button", { name: /Clear \(2\)/ });
    expect(stops[stops.length - 1]).toBe(clear);
    expect(stops[stops.length - 2]).toBe(screen.getByRole("button", { name: "Select" }));

    clear.focus();
    expect(fireEvent.keyDown(clear, { key: "Tab" })).toBe(false);
    expect(document.activeElement).toBe(stops[0]);
    expect(fireEvent.keyDown(stops[0], { key: "Tab", shiftKey: true })).toBe(false);
    expect(document.activeElement).toBe(clear);
  });
});
