// ⌘L 聚焦捕获框判例（CONTRACT §54.4 2026-09-05 追记；原生 AppDelegate.swift View ▸ 聚焦捕获框 keyEquivalent "l"）：
// 看板页 ⌘L → 提案列 composer 获焦、事件被 preventDefault、光标在草稿末尾；⌘1…⌘7 换页照旧；⌥⌘S（原生折叠侧栏，
// s4 DELETE 退役）什么都不做；⌃L / ⌥⌘L / ⇧⌘L 不算；输入框里 ⌘L 也接（从运行中 composer 跳到提案框）；
// 不在看板页 ⌘L = 留接力棒 + 回看板。
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { navigate } from "../../route";
import { PENDING_FOCUS_KEY } from "../board/focusComposer";
import { isFocusComposerShortcut, NavRail } from "./NavRail";

vi.mock("../../route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../route")>();
  return { ...actual, navigate: vi.fn() };
});

/** 栏 + 一个假看板（提案列 / 运行中列各一个 composer，DOM 顺序同 BoardLanes） */
function renderRailWithBoard(search = "", draft = "") {
  window.history.replaceState(null, "", `/${search}`);
  const board = document.createElement("div");
  board.innerHTML = `
    <section class="board-column"><div class="lane-composer"><textarea data-lane="propose"></textarea></div></section>
    <section class="board-column"><div class="lane-composer"><textarea data-lane="run"></textarea></div></section>`;
  document.body.appendChild(board);
  const propose = board.querySelector<HTMLTextAreaElement>('textarea[data-lane="propose"]')!;
  const run = board.querySelector<HTMLTextAreaElement>('textarea[data-lane="run"]')!;
  propose.value = draft;
  render(
    <LanguageContext.Provider value="en">
      <NavRail />
    </LanguageContext.Provider>,
  );
  return { propose, run };
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  vi.mocked(navigate).mockReset();
});

afterEach(() => {
  cleanup();
  document.body.innerHTML = "";
});

describe("NavRail — ⌘L 聚焦捕获框", () => {
  it("看板页 ⌘L：提案列 composer 获焦、光标到草稿末尾、事件 preventDefault、不导航", () => {
    const { propose } = renderRailWithBoard("", "半句草稿");
    const notPrevented = fireEvent.keyDown(window, { key: "l", metaKey: true });
    expect(notPrevented).toBe(false); // fireEvent 返回 !defaultPrevented
    expect(document.activeElement).toBe(propose);
    expect([propose.selectionStart, propose.selectionEnd]).toEqual([propose.value.length, propose.value.length]);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("输入框里 ⌘L 也接：从运行中 composer 跳到提案框；大写 L（Caps Lock）同样算", () => {
    const { propose, run } = renderRailWithBoard();
    run.focus();
    fireEvent.keyDown(run, { key: "l", metaKey: true });
    expect(document.activeElement).toBe(propose);
    propose.blur();
    run.focus();
    fireEvent.keyDown(run, { key: "L", metaKey: true });
    expect(document.activeElement).toBe(propose);
  });

  it("只认 ⌘ 单修饰：⌃L / ⌥⌘L / ⇧⌘L / 裸 l 都不聚焦也不 preventDefault", () => {
    const { propose } = renderRailWithBoard();
    for (const init of [
      { key: "l", ctrlKey: true },
      { key: "l", metaKey: true, altKey: true },
      { key: "L", metaKey: true, shiftKey: true },
      { key: "l" },
    ]) {
      expect(fireEvent.keyDown(window, init)).toBe(true);
      expect(document.activeElement).not.toBe(propose);
    }
    expect(isFocusComposerShortcut(new KeyboardEvent("keydown", { key: "l", metaKey: true }))).toBe(true);
    expect(isFocusComposerShortcut(new KeyboardEvent("keydown", { key: "l", metaKey: true, ctrlKey: true }))).toBe(false);
  });

  it("⌘1…⌘7 换页不受影响；⌥⌘S（原生折叠侧栏快捷键，已退役）什么都不做", () => {
    renderRailWithBoard();
    fireEvent.keyDown(window, { key: "4", metaKey: true });
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(new URL(String(vi.mocked(navigate).mock.calls[0][0])).searchParams.get("page")).toBe("trash");
    fireEvent.keyDown(window, { key: "1", metaKey: true });
    expect(navigate).toHaveBeenCalledTimes(2);
    expect(new URL(String(vi.mocked(navigate).mock.calls[1][0])).searchParams.get("page")).toBeNull();
    // ⌥⌘S：不折叠、不持久化、不导航、不 preventDefault
    expect(document.querySelector(".rail")?.classList.contains("is-collapsed")).toBe(false);
    expect(fireEvent.keyDown(window, { key: "s", metaKey: true, altKey: true })).toBe(true);
    expect(document.querySelector(".rail")?.classList.contains("is-collapsed")).toBe(false);
    expect(window.localStorage.getItem("sidebarCollapsed")).toBeNull();
    expect(navigate).toHaveBeenCalledTimes(2);
  });

  it("不在看板页 ⌘L：留 zai.pendingFocus=composer 接力棒、整页导航回看板", () => {
    const { propose } = renderRailWithBoard("?page=settings");
    fireEvent.keyDown(window, { key: "l", metaKey: true });
    expect(window.sessionStorage.getItem(PENDING_FOCUS_KEY)).toBe("composer");
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(new URL(String(vi.mocked(navigate).mock.calls[0][0])).searchParams.get("page")).toBeNull();
    expect(document.activeElement).not.toBe(propose);
  });
});
