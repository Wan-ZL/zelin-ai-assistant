// 「筛选」面板的 **Tab 环**（§49 追记 2026-09-04 D31「Tab 在面板内循环」；issue #420 的孪生 R-222）。
// 此前 web/src 与 web/e2e 里对 FilterPopover 一次 Tab 都没被派发过，整段 handleKeyDown 的 Tab 分支是必然的变异幸存者
// （#427 正文自己列的最高价值空白）。#425 / #427 带来的 FilterPopover.escDelivery.test.tsx 钉的是 ⎋ 的交付路径与
// 「打开即同步聚焦」——两处都是 #420 的修复本体；这里钉的环本身**不依赖**那个修复，修复前后同样成立：
// 每条都显式 .focus() 到位再派发，不赌「打开即聚焦」是排在 rAF 里还是同步。
//   1) 环的站点按 accessible name 钉成一张确定的表：Tier / 期限 / 回锅 三颗 chip → 排序 <select> → 页脚「选择」
//      （有生效维度时再多一颗「清除（N）」，它才是最后一站）——不抄组件的 FOCUSABLE 选择器，判例自己的表就是 oracle；
//   2) 最后一站按 Tab → 回到第一站、事件被 preventDefault；第一站按 ⇧Tab → 到最后一站；compact / tight 同一套环；
//   3) 中间站与环边「顺向」那一下钉的是**不变量**而不是实现：要么不拦、焦点不动（今天的实现：中间交给浏览器文档顺序），
//      要么拦下并把焦点放到相邻站上（roving trap 也算对）——两种实现都绿，但「拦下却不动焦点」这种把用户卡死的红。
// 盲区（记账，不钉）：(a) jsdom 不实现 Tab 的默认焦点移动，「不拦」只能判 defaultPrevented 与焦点未动，判不出浏览器把焦点
// 移到了哪；真浏览器里 Tab 环目前没有任何 e2e 兜底——web/e2e 里一次 keyboard.press("Tab") 都没有，headerLayout.spec.ts
// 只按 Escape / Enter。(b) 面板里开着 chip 下拉（TaskPropertyPicker 把 listbox portal 进本面板）时，组件 FOCUSABLE 的
// `button:not([disabled])` 会把 role="option" 的按钮（含 tabindex=-1 的）也数进环，最后一站从「选择」变成最后一颗 option，
// 环在这个状态下是破的——既有 bug，修法在 FilterPopover.tsx（与 #425 同一文件），本卡只记账，见 progress 记录。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HeaderDensityContext, type HeaderDensity } from "../shell/headerDensity";
import { resetStoreForTests } from "../../store";
import { FilterBar } from "./FilterBar";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

/** 环的站点表（accessible name，DOM 顺序）；有生效维度时页脚多出「清除（N）」 */
const RING = ["Filter by tier", "Filter by deadline", "↩︎ Re-raised", "Card sorting", "Select"] as const;

function renderAt(density: HeaderDensity) {
  return render(
    <HeaderDensityContext.Provider value={density}>
      <FilterBar />
    </HeaderDensityContext.Provider>,
  );
}

/** 开面板，按站点表取回环上的元素（顺序即表的顺序） */
function openPanel(density: HeaderDensity = "compact", ring: readonly string[] = RING): HTMLElement[] {
  renderAt(density);
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  const dialog = screen.getByRole("dialog", { name: "Filters" });
  const stops = ring.map((name) =>
    name === "Card sorting" ? screen.getByRole("combobox", { name }) : screen.getByRole("button", { name }),
  );
  for (const stop of stops) expect(dialog.contains(stop)).toBe(true);
  // 表是全等的：面板里可聚焦的东西不多不少就是这几站（<select> 与按钮之外没有别的控件）
  const focusable = Array.from(dialog.querySelectorAll<HTMLElement>("button, select, input, a[href], [tabindex]"))
    .filter((el) => !el.hasAttribute("disabled") && el.getAttribute("tabindex") !== "-1");
  expect(focusable).toEqual(stops);
  return stops;
}

/** Tab 一下之后的不变量：没拦就没动；拦了就必须落在相邻站上 */
function expectTabInvariant(from: HTMLElement, neighbour: HTMLElement | undefined, shiftKey: boolean) {
  from.focus();
  const notPrevented = fireEvent.keyDown(from, { key: "Tab", shiftKey });
  if (notPrevented) {
    expect(document.activeElement).toBe(from);
  } else {
    expect(neighbour).toBeDefined();
    expect(document.activeElement).toBe(neighbour);
  }
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

      last.focus();
      expect(document.activeElement).toBe(last);
      expect(fireEvent.keyDown(last, { key: "Tab" })).toBe(false); // false = defaultPrevented
      expect(document.activeElement).toBe(first);

      expect(fireEvent.keyDown(first, { key: "Tab", shiftKey: true })).toBe(false);
      expect(document.activeElement).toBe(last);
    });
  }

  it("中间站按 Tab / ⇧Tab：要么不拦焦点不动，要么拦下并落在相邻站——绝不拦下却不动", () => {
    const stops = openPanel();
    for (let i = 1; i < stops.length - 1; i++) {
      expectTabInvariant(stops[i], stops[i + 1], false);
      expectTabInvariant(stops[i], stops[i - 1], true);
    }
  });

  it("第一站按 Tab、最后一站按 ⇧Tab（顺向进环）：同一条不变量，相邻站在环内", () => {
    const stops = openPanel();
    expectTabInvariant(stops[0], stops[1], false);
    expectTabInvariant(stops[stops.length - 1], stops[stops.length - 2], true);
  });

  it("有生效维度时页脚多出「清除（N）」：它才是最后一站，环覆盖页脚而不止 chips 那一行", () => {
    window.history.replaceState(null, "", "/?tier=T1&deadline=soon");
    const stops = openPanel("compact", [...RING, "Clear (2)"]);
    const clear = stops[stops.length - 1];
    expect(clear).toBe(screen.getByRole("button", { name: "Clear (2)" }));

    clear.focus();
    expect(fireEvent.keyDown(clear, { key: "Tab" })).toBe(false);
    expect(document.activeElement).toBe(stops[0]);
    expect(fireEvent.keyDown(stops[0], { key: "Tab", shiftKey: true })).toBe(false);
    expect(document.activeElement).toBe(clear);
  });
});
