// 侧栏 ⎋ 的作用域（CONTRACT §49 追记 `store-resilience-drawer`；同 §15 追记 FilterBar 的两道门，原生 Kanban.swift:186 /
// :225-236 的 scoped Esc）：① IME 候选期间的 ⎋（isComposing / keyCode 229）归输入法——撤销一串拼音不许顺手关侧栏；
// ② ⎋ 的 target 是文字输入框（侧栏内的改名框、Tab 出去的 ⌘F 搜索框、任何 textarea / contenteditable）→ 归那个框，侧栏不关；
// 焦点在 <aside> 本身 / 按钮 / 非输入元素上的 ⎋ 照旧关侧栏（键盘主路不变）。fetch 全程 stub。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DetailDrawer } from "./DetailDrawer";
import { getState, resetStoreForTests, selectCard } from "../../store";

const DETAIL = { id: "R-101", title: "给 example-bench 加导出", lane: "needs_approval", plan: ["step A"] };

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.stubGlobal("fetch", vi.fn(async (url: unknown) => {
    if (String(url).includes("/api/cards/")) return jsonResponse(DETAIL);
    return jsonResponse({ ok: true });
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

async function openDrawer() {
  act(() => selectCard("R-101"));
  await screen.findByText("step A");
  return screen.getByRole("dialog");
}

describe("DetailDrawer ⎋ scope", () => {
  it("⎋ in the drawer's own rename input keeps the drawer open (the field owns its ⎋)", async () => {
    render(<DetailDrawer />);
    await openDrawer();
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "New title" });
    input.focus();
    fireEvent.keyDown(input, { key: "Escape" });
    // TitleEditor 自己吃掉了 ⎋（退出编辑），侧栏还在
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "New title" })).toBeNull());
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(getState().selectedCardId).toBe("R-101");
  });

  it("⎋ dispatched from a foreign text input / textarea / contenteditable outside the drawer keeps it open", async () => {
    render(<DetailDrawer />);
    await openDrawer();
    const input = document.createElement("input");
    input.type = "search";
    const textarea = document.createElement("textarea");
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    const inner = document.createElement("span");
    editable.appendChild(inner);
    document.body.append(input, textarea, editable);
    try {
      for (const target of [input, textarea, inner]) {
        fireEvent.keyDown(target, { key: "Escape" });
        expect(screen.getByRole("dialog")).toBeTruthy();
        expect(getState().selectedCardId).toBe("R-101");
      }
    } finally {
      input.remove();
      textarea.remove();
      editable.remove();
    }
  });

  it("⎋ during IME composition (isComposing / keyCode 229) is left to the input method", async () => {
    render(<DetailDrawer />);
    const dialog = await openDrawer();
    fireEvent.keyDown(dialog, { key: "Escape", isComposing: true });
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.keyDown(dialog, { key: "Escape", keyCode: 229 });
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(getState().selectedCardId).toBe("R-101");
  });

  it("plain ⎋ on the dialog / window / a button still closes the drawer (keyboard main path unchanged)", async () => {
    render(<DetailDrawer />);
    const dialog = await openDrawer();
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(getState().selectedCardId).toBeNull();

    await openDrawer();
    fireEvent.keyDown(screen.getByRole("button", { name: "Close" }), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await openDrawer();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("⎋ on a checkbox / button-type input is not a text field — the drawer closes", async () => {
    render(<DetailDrawer />);
    await openDrawer();
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    document.body.append(checkbox);
    try {
      fireEvent.keyDown(checkbox, { key: "Escape" });
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    } finally {
      checkbox.remove();
    }
  });
});
