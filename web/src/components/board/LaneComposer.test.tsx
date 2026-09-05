// 列顶输入框的键盘纪律（CONTRACT §41 2026-09-04 追记，owner 决策 D35「这个我回车我不希望是直接跑而是下一行，
// 要跑是需要点击按钮。」）：
//   1) Enter / Shift+Enter / ⌘Enter / Ctrl+Enter 都不提交——键盘上没有提交键，Enter 也不被 preventDefault（浏览器原生换行）；
//   2) 只有按钮提交，换行原样进 payload.text；成功后清空、失败草稿留着（§41 草稿保留）；
//   3) textarea 随内容 1 → 5 行增高、第 6 行起不再长（rows 停在 5）；清空回到 1 行；空草稿不量 scrollHeight（placeholder 软换行不长高）；
//   4) ↑/↓ 历史只在草稿为空（或已在翻历史）时接管；多行草稿里的 ↑ 归光标；翻历史途中一改字就退出翻历史；
//   5) Esc 只交还光标，草稿不动。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests } from "../../store";
import { HISTORY_KEY } from "./composerCommands";
import { COMPOSER_MAX_ROWS, LaneComposer } from "./LaneComposer";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
}));
import { postAction } from "../../api";

const LINE = 16.8; // --type-composer：12px × 1.4
const PADDING_Y = 10; // 5px 上 + 5px 下

/** jsdom 没有布局：把行高 / 内边距与「内容需要的 scrollHeight」桩成 tokens.css 的真值，让 fitComposerRows 有东西量 */
function stubLayout() {
  const real = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((el, pseudo) =>
    el instanceof HTMLTextAreaElement
      ? ({ lineHeight: `${LINE}px`, paddingTop: "5px", paddingBottom: "5px" }) as unknown as CSSStyleDeclaration
      : real(el, pseudo));
  Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", {
    configurable: true,
    get(this: HTMLTextAreaElement) {
      return this.value.split("\n").length * LINE + PADDING_Y;
    },
  });
}

function mount() {
  render(
    <LaneComposer
      placeholder="One sentence — AI researches and proposes…"
      submitLabel="Capture"
      successNote="Submitted; AI is analyzing"
      buildBody={(t) => ({ action: "capture", text: t })}
    />,
  );
  return {
    field: screen.getByPlaceholderText("One sentence — AI researches and proposes…") as HTMLTextAreaElement,
    button: screen.getByRole("button", { name: "Capture" }) as HTMLButtonElement,
  };
}

beforeEach(() => {
  resetStoreForTests();
  window.localStorage.clear();
  vi.mocked(postAction).mockClear();
  vi.mocked(postAction).mockResolvedValue({ ok: true });
  stubLayout();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  delete (HTMLTextAreaElement.prototype as { scrollHeight?: unknown }).scrollHeight;
});

describe("LaneComposer — Enter is a newline, only the button submits (D35)", () => {
  it("renders a textarea (not an <input>) with the native placeholder", () => {
    const { field } = mount();
    expect(field.tagName).toBe("TEXTAREA");
    expect(field.rows).toBe(1);
  });

  for (const [label, init] of [
    ["Enter", {}],
    ["Shift+Enter", { shiftKey: true }],
    ["⌘Enter", { metaKey: true }],
    ["Ctrl+Enter", { ctrlKey: true }],
  ] as const) {
    it(`${label} does not submit and is not intercepted`, () => {
      const { field } = mount();
      fireEvent.change(field, { target: { value: "first line" } });
      const notPrevented = fireEvent.keyDown(field, { key: "Enter", code: "Enter", ...init });
      expect(notPrevented).toBe(true); // 没 preventDefault → 浏览器原生换行
      expect(postAction).not.toHaveBeenCalled();
      expect(field.value).toBe("first line"); // 草稿还在（jsdom 不模拟默认动作，换行由浏览器做）
    });
  }

  it("the button submits with newlines preserved, then clears the draft", async () => {
    const { field, button } = mount();
    expect(button.disabled).toBe(true);
    fireEvent.change(field, { target: { value: "  line one\nline two\n\nline four  " } });
    expect(button.disabled).toBe(false);
    await act(async () => {
      fireEvent.click(button);
    });
    expect(postAction).toHaveBeenCalledTimes(1);
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "capture", text: "line one\nline two\n\nline four" });
    expect(field.value).toBe("");
    expect(field.rows).toBe(1);
    expect(screen.getByText("Submitted; AI is analyzing")).toBeTruthy();
    expect(JSON.parse(window.localStorage.getItem(HISTORY_KEY) ?? "[]")).toEqual(["line one\nline two\n\nline four"]);
  });

  it("whitespace-only drafts (including bare newlines) keep the button disabled", () => {
    const { field, button } = mount();
    fireEvent.change(field, { target: { value: "\n\n  \n" } });
    expect(button.disabled).toBe(true);
  });

  it("a rejected submit keeps the multi-line draft and shows the native failure line", async () => {
    vi.mocked(postAction).mockRejectedValueOnce(new Error("inbox not writable"));
    const { field, button } = mount();
    fireEvent.change(field, { target: { value: "a\nb" } });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(field.value).toBe("a\nb");
    expect(screen.getByText("Submit failed — input kept")).toBeTruthy();
  });
});

describe("LaneComposer — auto-grow 1…5 rows", () => {
  it("grows one row per line up to COMPOSER_MAX_ROWS and no further; shrinks back when cleared", () => {
    const { field } = mount();
    expect(COMPOSER_MAX_ROWS).toBe(5);
    fireEvent.change(field, { target: { value: "1\n2\n3" } });
    expect(field.rows).toBe(3);
    fireEvent.change(field, { target: { value: "1\n2\n3\n4\n5" } });
    expect(field.rows).toBe(5);
    fireEvent.change(field, { target: { value: "1\n2\n3\n4\n5\n6\n7\n8" } });
    expect(field.rows).toBe(5); // 第 6 行起不再长：textarea 自己滚
    fireEvent.change(field, { target: { value: "" } });
    expect(field.rows).toBe(1);
  });

  it("a long soft-wrapped line grows too (measured by scrollHeight, not by newline count)", () => {
    Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", {
      configurable: true,
      get: () => 2 * LINE + PADDING_Y, // 浏览器把一句长话折成两行
    });
    const { field } = mount();
    fireEvent.change(field, { target: { value: "one very long sentence that wraps" } });
    expect(field.rows).toBe(2);
  });

  it("an empty draft stays at 1 row even when the placeholder soft-wraps (scrollHeight includes the placeholder)", () => {
    // 英文 + 大字号：占位句折两行，Chromium / WebKit 的 scrollHeight 都把它算进去——空草稿不量
    Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", {
      configurable: true,
      get: () => 2 * LINE + PADDING_Y,
    });
    const { field } = mount();
    expect(field.rows).toBe(1); // 首次挂载
    fireEvent.change(field, { target: { value: "x" } });
    expect(field.rows).toBe(2); // 有内容才按 scrollHeight 量
    fireEvent.change(field, { target: { value: "" } });
    expect(field.rows).toBe(1); // 删空回到 1 行，不跳回 2
  });

  it("leaves rows alone when there is no layout to measure (line-height empty)", () => {
    vi.mocked(window.getComputedStyle).mockImplementation((el) =>
      ({ lineHeight: el instanceof HTMLTextAreaElement ? "" : "normal", getPropertyValue: () => "" }) as unknown as CSSStyleDeclaration);
    const { field } = mount();
    fireEvent.change(field, { target: { value: "1\n2\n3" } });
    expect(field.rows).toBe(1);
  });
});

describe("LaneComposer — ↑/↓ history and Esc", () => {
  it("↑ on an empty draft recalls history; ↓ walks back; ↑ inside a typed draft is left to the caret", () => {
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(["newest", "older"]));
    const { field } = mount();
    expect(fireEvent.keyDown(field, { key: "ArrowUp" })).toBe(false); // 接管了（preventDefault）
    expect(field.value).toBe("newest");
    fireEvent.keyDown(field, { key: "ArrowUp" });
    expect(field.value).toBe("older");
    fireEvent.keyDown(field, { key: "ArrowDown" });
    expect(field.value).toBe("newest");
    fireEvent.keyDown(field, { key: "ArrowDown" });
    expect(field.value).toBe("");
    // 多行草稿：↑ 不接管、草稿不动
    fireEvent.change(field, { target: { value: "line 1\nline 2" } });
    expect(fireEvent.keyDown(field, { key: "ArrowUp" })).toBe(true);
    expect(field.value).toBe("line 1\nline 2");
  });

  it("editing while browsing history exits browsing: the next ↑ no longer hijacks", () => {
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(["newest", "older"]));
    const { field } = mount();
    fireEvent.keyDown(field, { key: "ArrowUp" });
    expect(field.value).toBe("newest");
    fireEvent.change(field, { target: { value: "newest\nplus a line" } });
    expect(fireEvent.keyDown(field, { key: "ArrowUp" })).toBe(true);
    expect(field.value).toBe("newest\nplus a line");
  });

  it("Esc blurs the field and keeps the draft", () => {
    const { field } = mount();
    fireEvent.change(field, { target: { value: "keep me\nplease" } });
    field.focus();
    expect(document.activeElement).toBe(field);
    fireEvent.keyDown(field, { key: "Escape" });
    expect(document.activeElement).not.toBe(field);
    expect(field.value).toBe("keep me\nplease");
  });

  it("keys during IME composition are never intercepted", () => {
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(["newest"]));
    const { field } = mount();
    expect(fireEvent.keyDown(field, { key: "ArrowUp", isComposing: true })).toBe(true);
    expect(field.value).toBe("");
  });
});
