// 滚动记忆 × 列各自滚动（D40 × D42，CONTRACT §54.4 2026-09-06 追记）：D42 起文档不滚、非看板页在跨页常驻的 `.shell-main`
// 里滚——restoreScroll 的「没记过 → 到顶」因此也要把 `[data-scroll-memory]` 容器归零，否则上一页滚到哪、第一次到的新页就从哪
// 开始；记过的页里没有这个容器的键（记的时候它还不在 DOM 里）同样归零。记过的容器照旧还原（route.router.test.ts 既有判例）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { rememberScroll, resetRouterForTests, restoreScroll } from "./route";

function scroller(key: string, top: number, left = 0): HTMLElement {
  const el = document.createElement("div");
  el.dataset.scrollMemory = key;
  Object.defineProperty(el, "scrollTop", { value: top, writable: true, configurable: true });
  Object.defineProperty(el, "scrollLeft", { value: left, writable: true, configurable: true });
  document.body.appendChild(el);
  return el;
}

beforeEach(() => {
  resetRouterForTests();
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
  Object.defineProperty(window, "scrollX", { value: 0, configurable: true });
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("restoreScroll — 容器的「到顶」", () => {
  it("没记过的页：每个 [data-scroll-memory] 容器归零（跨页常驻的 .shell-main 不把上一页的位置带进新页）", () => {
    const main = scroller("shell-main", 640, 12);
    restoreScroll("about");
    expect(main.scrollTop).toBe(0);
    expect(main.scrollLeft).toBe(0);
    // window 已在顶 → 不多余地调 scrollTo（既有判例）
    expect(window.scrollTo).not.toHaveBeenCalled();
  });

  it("记过的页、但快照里没有这个容器的键（记的时候它还不在 DOM 里）→ 归零；有键的照旧还原", () => {
    const main = scroller("shell-main", 300);
    rememberScroll("settings"); // 只记到 shell-main
    main.scrollTop = 900;
    const lane = scroller("lane:needs_approval", 77);
    restoreScroll("settings");
    expect(main.scrollTop).toBe(300);
    expect(lane.scrollTop).toBe(0);
  });

  it("已经在 0 的容器不写（scrollTop 赋值会触发布局，首帧不多余动一下）", () => {
    const el = scroller("shell-main", 0);
    const spy = vi.fn();
    Object.defineProperty(el, "scrollTop", { get: () => 0, set: spy, configurable: true });
    restoreScroll("trash");
    expect(spy).not.toHaveBeenCalled();
  });
});
