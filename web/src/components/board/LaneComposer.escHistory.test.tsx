// 列顶输入框的 Esc 作用域与命令历史（CONTRACT §34 2026-09-05 追记；原生 Composer.swift:233-245 escKey 返 .handled、
// AppDelegate.swift:1241 `if ok { CaptureHistory.push(text) }  // item 5: commands count too`、Composer.swift:221 historyIndex 归零）：
//   1) 成功的斜杠命令进历史：`/lang en` 成功后空草稿 ↑ 翻回 `/lang en`；报错的命令不进历史；
//   2) Esc 在框内就地吃掉（stopPropagation）：光标在输入框里按 ⎋ 只 blur、草稿不动，FilterBar 的 window ⎋
//      （清 ⌘F 搜索词 → 退出多选）一个字不动；
//   3) IME 候选期间的 Esc 归输入法：不 blur、草稿不动，同样不外泄到看板层；
//   4) 作用域不是禁用：光标离开输入框后的 ⎋ 照旧走 FilterBar 两段。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getState, resetStoreForTests, setFilters, setSelectionMode } from "../../store";
import { FilterBar } from "../chrome/FilterBar";
import { HISTORY_KEY } from "./composerCommands";
import { LaneComposer } from "./LaneComposer";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  fetchBoard: vi.fn(),
}));
import { postAction } from "../../api";

function composer() {
  return (
    <LaneComposer
      placeholder="type here"
      submitLabel="Capture"
      successNote="Submitted"
      buildBody={(t) => ({ action: "capture", text: t })}
    />
  );
}

function mount({ withFilterBar = false } = {}) {
  render(
    <>
      {withFilterBar && <FilterBar />}
      {composer()}
    </>,
  );
  return {
    field: screen.getByPlaceholderText("type here") as HTMLTextAreaElement,
    button: screen.getByRole("button", { name: "Capture" }) as HTMLButtonElement,
  };
}

function history(): string[] {
  return JSON.parse(window.localStorage.getItem(HISTORY_KEY) ?? "[]");
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  window.localStorage.clear();
  vi.mocked(postAction).mockClear();
  vi.mocked(postAction).mockResolvedValue({ ok: true });
});

afterEach(cleanup);

describe("LaneComposer — successful slash commands enter the ↑/↓ history (native 'commands count too')", () => {
  it("'/lang en' then ↑ on the empty draft recalls '/lang en'", async () => {
    const { field, button } = mount();
    fireEvent.change(field, { target: { value: "/lang en" } });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(field.value).toBe(""); // 命令成功清空草稿
    expect(screen.getByText("Language → en")).toBeTruthy();
    expect(history()).toEqual(["/lang en"]);
    expect(postAction).not.toHaveBeenCalled(); // 命令不发 inbox

    expect(fireEvent.keyDown(field, { key: "ArrowUp" })).toBe(false); // 接管（preventDefault）
    expect(field.value).toBe("/lang en");
    fireEvent.keyDown(field, { key: "ArrowDown" });
    expect(field.value).toBe("");
  });

  it("a recalled command resubmitted unedited resets the cursor: the next ↑ starts from the newest entry again", async () => {
    // 翻出一条旧命令原样重发（不经 onChange——onChange 自己也会归零 historyIndex，走那条路测不到提交分支的归零）：
    // 原生 Composer.submit 成功后 historyIndex = nil，下一次 ↑ 必须从最新一条重新开始，而不是接着上次的游标往旧处翻
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(["/lang zh", "older capture"]));
    const { field, button } = mount();
    fireEvent.keyDown(field, { key: "ArrowUp" }); // historyIndex = 0
    expect(field.value).toBe("/lang zh");
    await act(async () => {
      fireEvent.click(button); // 原样重发，历史里去重后顺序不变
    });
    expect(field.value).toBe("");
    expect(screen.getByText("Language → zh")).toBeTruthy();
    expect(history()).toEqual(["/lang zh", "older capture"]);
    fireEvent.keyDown(field, { key: "ArrowUp" }); // 游标已归零 → 最新一条；没归零会是 index 1 的 "older capture"
    expect(field.value).toBe("/lang zh");
    fireEvent.keyDown(field, { key: "ArrowUp" });
    expect(field.value).toBe("older capture");
  });

  it("a rejected slash command (bad argument) is not recorded", async () => {
    const { field, button } = mount();
    fireEvent.change(field, { target: { value: "/lang klingon" } });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(field.value).toBe("/lang klingon"); // 输入保留
    expect(document.querySelector(".composer-error")?.textContent).toContain("Unrecognized or bad argument:");
    expect(history()).toEqual([]);
  });
});

describe("LaneComposer — Esc is handled inside the field and never reaches the board's window listener", () => {
  it("Esc from the composer never reaches a window keydown listener (native escKey returns .handled); other keys still bubble", () => {
    const { field } = mount();
    const seen = vi.fn();
    window.addEventListener("keydown", seen);
    try {
      fireEvent.keyDown(field, { key: "Escape" });
      fireEvent.keyDown(field, { key: "Escape", isComposing: true }); // IME 撤销的那一下也不外泄
      expect(seen).not.toHaveBeenCalled();
      fireEvent.keyDown(field, { key: "a" });
      expect(seen).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("keydown", seen);
    }
  });

  it("Esc in the composer blurs it and keeps the draft; the ⌘F query and selection mode survive", () => {
    const { field } = mount({ withFilterBar: true });
    act(() => {
      setFilters({ search: "hello" });
      setSelectionMode(true);
    });
    fireEvent.change(field, { target: { value: "keep me" } });
    field.focus();
    expect(document.activeElement).toBe(field);

    fireEvent.keyDown(field, { key: "Escape" });

    expect(document.activeElement).not.toBe(field);
    expect(field.value).toBe("keep me");
    expect(getState().filters.search).toBe("hello");
    expect(getState().selectionMode).toBe(true);
  });

  it("Esc during an IME composition keeps focus and text, and still does not leak to the board", () => {
    const { field } = mount({ withFilterBar: true });
    act(() => {
      setFilters({ search: "hello" });
      setSelectionMode(true);
    });
    fireEvent.change(field, { target: { value: "ni hao" } });
    field.focus();

    const notPrevented = fireEvent.keyDown(field, { key: "Escape", isComposing: true });

    expect(notPrevented).toBe(true); // 不 preventDefault：输入法自己撤销拼音
    expect(document.activeElement).toBe(field); // 不 blur
    expect(field.value).toBe("ni hao");
    expect(getState().filters.search).toBe("hello");
    expect(getState().selectionMode).toBe(true);
  });

  it("the scope is not a kill switch: once the caret has left the composer, ⎋ runs the board's two-step again", () => {
    const { field } = mount({ withFilterBar: true });
    act(() => {
      setFilters({ search: "hello" });
      setSelectionMode(true);
    });
    field.focus();
    fireEvent.keyDown(field, { key: "Escape" }); // 第一下：只交还光标
    expect(getState().filters.search).toBe("hello");

    fireEvent.keyDown(window, { key: "Escape" }); // 光标已不在框里：清词
    expect(getState().filters.search).toBe("");
    expect(getState().selectionMode).toBe(true);
    fireEvent.keyDown(window, { key: "Escape" }); // 再按：退出多选
    expect(getState().selectionMode).toBe(false);
  });
});
