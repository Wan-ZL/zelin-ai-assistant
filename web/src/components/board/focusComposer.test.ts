// 「聚焦捕获框」落点判例（CONTRACT §54.4 2026-09-05 追记；原生 AppDelegate.swift focusCaptureField + Composer.swift
// `guard mode == .propose` / 「already open → just refocus」）：只聚焦第一个（提案列）composer、光标到末尾不全选、
// 不在看板页 = 留 sessionStorage `zai.pendingFocus` 接力棒 + 回看板、接力棒只消费一次。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { navigate } from "../../route";
import { COMPOSER_SELECTOR, consumePendingFocus, focusComposer, focusComposerField, PENDING_FOCUS_KEY } from "./focusComposer";

vi.mock("../../route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../route")>();
  return { ...actual, navigate: vi.fn() };
});

/** 两列各一个 composer（提案列在前、运行中列在后，与 BoardLanes 的 DOM 顺序一致）；提案框里预置草稿 */
function mountBoard(draft = "") {
  document.body.innerHTML = `
    <div class="board-main">
      <section class="board-column"><div class="lane-composer"><textarea data-lane="propose"></textarea><button>捕获</button></div></section>
      <section class="board-column"><div class="lane-composer"><textarea data-lane="run"></textarea><button>直跑</button></div></section>
    </div>`;
  const propose = document.querySelector<HTMLTextAreaElement>('textarea[data-lane="propose"]')!;
  const run = document.querySelector<HTMLTextAreaElement>('textarea[data-lane="run"]')!;
  propose.value = draft;
  return { propose, run };
}

beforeEach(() => {
  window.sessionStorage.clear();
  vi.mocked(navigate).mockReset();
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  document.body.innerHTML = "";
  window.sessionStorage.clear();
});

describe("focusComposer — ⌘L / quick_capture 的共同落点", () => {
  it("看板页：聚焦第一个（提案列）composer，光标在草稿末尾而不是全选；运行中列的框不响应", () => {
    const { propose, run } = mountBoard("已有的半句草稿");
    run.focus();
    expect(document.activeElement).toBe(run);
    focusComposer();
    expect(document.activeElement).toBe(propose);
    expect(propose.selectionStart).toBe(propose.value.length);
    expect(propose.selectionEnd).toBe(propose.value.length);
    expect(navigate).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem(PENDING_FOCUS_KEY)).toBeNull();
    expect(COMPOSER_SELECTOR).toBe(".board-column .lane-composer textarea");
  });

  it("空草稿也聚焦（caret 0 = 末尾）；已在提案框里再按 = 只是把光标交回去", () => {
    const { propose } = mountBoard("");
    focusComposer();
    expect(document.activeElement).toBe(propose);
    expect(propose.selectionStart).toBe(0);
    propose.value = "abc";
    propose.setSelectionRange(1, 1);
    focusComposer();
    expect(document.activeElement).toBe(propose);
    expect([propose.selectionStart, propose.selectionEnd]).toEqual([3, 3]);
  });

  it("看板还没渲染（没有 composer）：不抛、返回 false", () => {
    document.body.innerHTML = "<div>正在加载看板…</div>";
    expect(focusComposerField()).toBe(false);
    expect(() => focusComposer()).not.toThrow();
  });

  it("不在看板页：留下 zai.pendingFocus=composer 接力棒、整页导航回看板（去掉 ?page= / ?card=），不在旧文档里聚焦", () => {
    window.history.replaceState(null, "", "/?page=settings&anchor=deps&card=R-1");
    const { propose } = mountBoard();
    focusComposer();
    expect(window.sessionStorage.getItem(PENDING_FOCUS_KEY)).toBe("composer");
    expect(navigate).toHaveBeenCalledTimes(1);
    const target = new URL(String(vi.mocked(navigate).mock.calls[0][0]));
    expect(target.searchParams.get("page")).toBeNull();
    expect(target.searchParams.get("card")).toBeNull();
    expect(vi.mocked(navigate).mock.calls[0][1]).toBeUndefined(); // 进历史栈（⌘L 是导航手势，可 ← 回去）
    expect(document.activeElement).not.toBe(propose);
  });

  it("接力棒只消费一次：BoardPage 挂载时聚焦并删标记，再挂载（刷新）不重放；没标记 / 坏值不聚焦", () => {
    const { propose } = mountBoard("x");
    window.sessionStorage.setItem(PENDING_FOCUS_KEY, "composer");
    expect(consumePendingFocus()).toBe(true);
    expect(document.activeElement).toBe(propose);
    expect([propose.selectionStart, propose.selectionEnd]).toEqual([1, 1]);
    expect(window.sessionStorage.getItem(PENDING_FOCUS_KEY)).toBeNull();
    propose.blur();
    expect(consumePendingFocus()).toBe(false);
    expect(document.activeElement).not.toBe(propose);
    window.sessionStorage.setItem(PENDING_FOCUS_KEY, "somewhere-else");
    expect(consumePendingFocus()).toBe(false);
    expect(document.activeElement).not.toBe(propose);
    expect(window.sessionStorage.getItem(PENDING_FOCUS_KEY)).toBeNull(); // 坏值也清掉，不留残余
  });

  it("sessionStorage 不可写：照样回看板（只是到了不自动聚焦），不抛", () => {
    window.history.replaceState(null, "", "/?page=trash");
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    try {
      expect(() => focusComposer()).not.toThrow();
      expect(navigate).toHaveBeenCalledTimes(1);
    } finally {
      setItem.mockRestore();
    }
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    try {
      expect(consumePendingFocus()).toBe(false);
    } finally {
      getItem.mockRestore();
    }
  });
});
