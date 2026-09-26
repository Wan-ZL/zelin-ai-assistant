// 「聚焦捕获框」落点判例（CONTRACT §54.4 2026-09-05 追记；原生 AppDelegate.swift focusCaptureField + Composer.swift
// `guard mode == .propose` / 「already open → just refocus」）：只聚焦**捕获框**——§78 之后它住在潜在任务条
// （BacklogStrip）的条头，运行中列的直跑框**永不**接这一下（原生「只归提案 composer」的实质是全局键只许记一件事、
// 不许替 owner 起跑花钱）；光标到末尾不全选；不在看板页 = 留 sessionStorage `zai.pendingFocus` 接力棒 + 回看板、
// 接力棒只消费一次。本文件用手搭 DOM（真组件的装配判例在 BoardPage.pendingFocus / BacklogStrip）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { navigate } from "../../route";
import { COMPOSER_SELECTOR, consumePendingFocus, focusComposer, focusComposerField, PENDING_FOCUS_KEY } from "./focusComposer";

vi.mock("../../route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../route")>();
  return { ...actual, navigate: vi.fn() };
});

/** 展开的潜在任务条（捕获框，预置草稿）+ 运行中列（直跑框）+ 右书立条，与 BoardLanes 的 DOM 顺序一致 */
function mountBoard(draft = "") {
  document.body.innerHTML = `
    <div class="board-main">
      <aside class="backlog-strip"><div class="lane-composer"><textarea data-lane="capture"></textarea><button>捕获</button></div></aside>
      <section class="board-column"><div class="lane-composer"><textarea data-lane="run"></textarea><button>直跑</button></div></section>
      <aside class="backlog-strip is-archive"><input class="trash-search" /></aside>
    </div>`;
  const capture = document.querySelector<HTMLTextAreaElement>('textarea[data-lane="capture"]')!;
  const run = document.querySelector<HTMLTextAreaElement>('textarea[data-lane="run"]')!;
  capture.value = draft;
  return { capture, run };
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
  it("看板页：聚焦潜在任务条的捕获框，光标在草稿末尾而不是全选；运行中列的直跑框不响应", () => {
    const { capture, run } = mountBoard("已有的半句草稿");
    run.focus();
    expect(document.activeElement).toBe(run);
    focusComposer();
    expect(document.activeElement).toBe(capture);
    expect(capture.selectionStart).toBe(capture.value.length);
    expect(capture.selectionEnd).toBe(capture.value.length);
    expect(navigate).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem(PENDING_FOCUS_KEY)).toBeNull();
    expect(COMPOSER_SELECTOR).toBe(".backlog-strip:not(.is-archive) .lane-composer textarea");
  });

  it("空草稿也聚焦（caret 0 = 末尾）；已在捕获框里再按 = 只是把光标交回去", () => {
    const { capture } = mountBoard("");
    focusComposer();
    expect(document.activeElement).toBe(capture);
    expect(capture.selectionStart).toBe(0);
    capture.value = "abc";
    capture.setSelectionRange(1, 1);
    focusComposer();
    expect(document.activeElement).toBe(capture);
    expect([capture.selectionStart, capture.selectionEnd]).toEqual([3, 3]);
  });

  it("看板还没渲染（没有 composer）：不抛、返回 false", () => {
    document.body.innerHTML = "<div>正在加载看板…</div>";
    expect(focusComposerField()).toBe(false);
    expect(() => focusComposer()).not.toThrow();
  });

  // §78 的安全底线：捕获框住在条的展开态里，条收起来 = 框不在 DOM 里。这一下宁可什么都不做，
  // 也绝不退到直跑框——一个全局键不许把「记一件事」变成「现在就花钱开跑」。
  it("潜在任务条收起（只剩直跑框）：返回 false，焦点不落到直跑框上", () => {
    document.body.innerHTML = `
      <div class="board-main">
        <aside class="backlog-strip is-collapsed"><button>Backlog</button></aside>
        <section class="board-column"><div class="lane-composer"><textarea data-lane="run"></textarea></div></section>
      </div>`;
    const run = document.querySelector<HTMLTextAreaElement>('textarea[data-lane="run"]')!;
    expect(focusComposerField()).toBe(false);
    expect(document.activeElement).not.toBe(run);
  });

  it("看板缺席态（dashboard.json 还没写出来）：退到 BoardMissingState 的捕获框", () => {
    document.body.innerHTML = `
      <div class="shell-board-missing"><div class="lane-composer"><textarea data-lane="missing"></textarea></div></div>`;
    const missing = document.querySelector<HTMLTextAreaElement>('textarea[data-lane="missing"]')!;
    expect(focusComposerField()).toBe(true);
    expect(document.activeElement).toBe(missing);
  });

  it("不在看板页：留下 zai.pendingFocus=composer 接力棒、整页导航回看板（去掉 ?page= / ?card=），不在旧文档里聚焦", () => {
    window.history.replaceState(null, "", "/?page=settings&anchor=deps&card=R-1");
    const { capture } = mountBoard();
    focusComposer();
    expect(window.sessionStorage.getItem(PENDING_FOCUS_KEY)).toBe("composer");
    expect(navigate).toHaveBeenCalledTimes(1);
    const target = new URL(String(vi.mocked(navigate).mock.calls[0][0]));
    expect(target.searchParams.get("page")).toBeNull();
    expect(target.searchParams.get("card")).toBeNull();
    expect(vi.mocked(navigate).mock.calls[0][1]).toBeUndefined(); // 进历史栈（⌘L 是导航手势，可 ← 回去）
    expect(document.activeElement).not.toBe(capture);
  });

  it("接力棒只消费一次：BoardPage 挂载时聚焦并删标记，再挂载（刷新）不重放；没标记 / 坏值不聚焦", () => {
    const { capture } = mountBoard("x");
    window.sessionStorage.setItem(PENDING_FOCUS_KEY, "composer");
    expect(consumePendingFocus()).toBe(true);
    expect(document.activeElement).toBe(capture);
    expect([capture.selectionStart, capture.selectionEnd]).toEqual([1, 1]);
    expect(window.sessionStorage.getItem(PENDING_FOCUS_KEY)).toBeNull();
    capture.blur();
    expect(consumePendingFocus()).toBe(false);
    expect(document.activeElement).not.toBe(capture);
    window.sessionStorage.setItem(PENDING_FOCUS_KEY, "somewhere-else");
    expect(consumePendingFocus()).toBe(false);
    expect(document.activeElement).not.toBe(capture);
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
