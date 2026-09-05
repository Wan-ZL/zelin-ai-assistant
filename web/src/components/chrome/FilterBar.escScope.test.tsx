// FilterBar 的 window ⎋ 作用域（CONTRACT §15 2026-09-05 追记；原生 Kanban.swift:186 escClearSearch 只挂在搜索 TextField、
// :225-236 hasMarkedText → .ignored 的 IME 红线）：
//   1) IME 候选期间的 ⎋（isComposing / keyCode 229）归输入法——搜索词一个字不动；
//   2) 光标在别人的文字输入框里（回收站搜索 <input type=search>、textarea、contenteditable）时 ⎋ 归那个框——
//      搜索词与多选态都不动；
//   3) 不承载文字的 <input>（多选态卡上的勾选框）不算「别人的框」：焦点在它上面按 ⎋ 仍走两段（退出多选）；
//   4) ⌘F 搜索框自己与非输入元素的 ⎋ 照旧：清词 → 退出多选。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getState, resetStoreForTests, setFilters, setSelectionMode } from "../../store";
import { escapeBelongsToForeignField, FilterBar } from "./FilterBar";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

/** FilterBar 旁边放几个「别人的」控件：回收站同款搜索框、一个 textarea、一个 contenteditable、一个多选勾选框 */
function mountWithNeighbours() {
  render(
    <>
      <FilterBar />
      <input type="search" className="chrome-search trash-search" aria-label="foreign search" />
      <textarea aria-label="foreign textarea" />
      <div contentEditable suppressContentEditableWarning data-testid="editable">note</div>
      <input type="checkbox" aria-label="select card" />
    </>,
  );
  act(() => {
    setFilters({ search: "readme" });
    setSelectionMode(true);
  });
  return {
    search: screen.getByRole("searchbox", { name: "Search cards" }) as HTMLInputElement,
    foreignSearch: screen.getByRole("searchbox", { name: "foreign search" }) as HTMLInputElement,
    foreignTextarea: screen.getByRole("textbox", { name: "foreign textarea" }) as HTMLTextAreaElement,
    editable: screen.getByTestId("editable"),
    checkbox: screen.getByRole("checkbox", { name: "select card" }) as HTMLInputElement,
  };
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
});

afterEach(cleanup);

describe("FilterBar ⎋ — IME red line", () => {
  it("⎋ with isComposing keeps the query (the input method owns that keystroke)", () => {
    const { search } = mountWithNeighbours();
    search.focus();
    fireEvent.keyDown(search, { key: "Escape", isComposing: true });
    expect(getState().filters.search).toBe("readme");
    expect(getState().selectionMode).toBe(true);
    expect(document.activeElement).toBe(search);
  });

  it("⎋ arriving as keyCode 229 (composition in progress) keeps the query", () => {
    const { search } = mountWithNeighbours();
    search.focus();
    fireEvent.keyDown(search, { key: "Escape", keyCode: 229 });
    expect(getState().filters.search).toBe("readme");
    expect(getState().selectionMode).toBe(true);
  });
});

describe("FilterBar ⎋ — scoped to the board, not to every text field on the page", () => {
  it("⎋ in a foreign <input type=search> (archive / trash search) keeps the query and selection mode", () => {
    const { foreignSearch } = mountWithNeighbours();
    foreignSearch.focus();
    fireEvent.keyDown(foreignSearch, { key: "Escape" });
    expect(getState().filters.search).toBe("readme");
    expect(getState().selectionMode).toBe(true);
  });

  it("⎋ in a foreign textarea keeps the query", () => {
    const { foreignTextarea } = mountWithNeighbours();
    foreignTextarea.focus();
    fireEvent.keyDown(foreignTextarea, { key: "Escape" });
    expect(getState().filters.search).toBe("readme");
    expect(getState().selectionMode).toBe(true);
  });

  it("⎋ inside a contenteditable keeps the query", () => {
    const { editable } = mountWithNeighbours();
    fireEvent.keyDown(editable, { key: "Escape" });
    expect(getState().filters.search).toBe("readme");
    expect(getState().selectionMode).toBe(true);
  });

  it("⎋ with focus on a selection checkbox is the board's: clears the query, then exits selection mode", () => {
    const { checkbox } = mountWithNeighbours();
    checkbox.focus();
    fireEvent.keyDown(checkbox, { key: "Escape" });
    expect(getState().filters.search).toBe("");
    expect(getState().selectionMode).toBe(true);
    fireEvent.keyDown(checkbox, { key: "Escape" });
    expect(getState().selectionMode).toBe(false);
  });

  it("⎋ in the ⌘F search box itself still clears the query, and a plain ⎋ still runs the two-step", () => {
    const { search } = mountWithNeighbours();
    search.focus();
    fireEvent.keyDown(search, { key: "Escape" });
    expect(getState().filters.search).toBe("");
    expect(getState().selectionMode).toBe(true);
    fireEvent.keyDown(search, { key: "Escape" }); // 已空 → 交还光标
    expect(document.activeElement).not.toBe(search);
    expect(getState().selectionMode).toBe(true);
    fireEvent.keyDown(window, { key: "Escape" }); // 非输入元素 → 退出多选
    expect(getState().selectionMode).toBe(false);
  });
});

describe("escapeBelongsToForeignField", () => {
  it("text-bearing fields other than our own are foreign; buttons, checkboxes, plain elements and our own box are not", () => {
    const own = document.createElement("input");
    own.type = "search";
    const otherSearch = document.createElement("input");
    otherSearch.type = "search";
    const textInput = document.createElement("input");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    const button = document.createElement("input");
    button.type = "button";
    const textarea = document.createElement("textarea");
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    const notEditable = document.createElement("div");
    notEditable.setAttribute("contenteditable", "false");
    const span = document.createElement("span");
    editable.appendChild(span);

    expect(escapeBelongsToForeignField(own, own)).toBe(false);
    expect(escapeBelongsToForeignField(otherSearch, own)).toBe(true);
    expect(escapeBelongsToForeignField(textInput, own)).toBe(true);
    expect(escapeBelongsToForeignField(textarea, own)).toBe(true);
    expect(escapeBelongsToForeignField(editable, own)).toBe(true);
    expect(escapeBelongsToForeignField(span, own)).toBe(true); // 可编辑区里的子节点
    expect(escapeBelongsToForeignField(notEditable, own)).toBe(false);
    expect(escapeBelongsToForeignField(checkbox, own)).toBe(false);
    expect(escapeBelongsToForeignField(button, own)).toBe(false);
    expect(escapeBelongsToForeignField(document.createElement("button"), own)).toBe(false);
    expect(escapeBelongsToForeignField(document.body, own)).toBe(false);
    expect(escapeBelongsToForeignField(window, own)).toBe(false);
    expect(escapeBelongsToForeignField(null, own)).toBe(false);
    expect(escapeBelongsToForeignField(otherSearch, null)).toBe(true); // 搜索框折成放大镜时（tight）也照判
  });
});
