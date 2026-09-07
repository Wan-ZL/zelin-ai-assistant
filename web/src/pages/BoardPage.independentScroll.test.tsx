// 列各自滚、列头与列顶输入框钉在列顶（owner 决策 D42，CONTRACT §54.4 2026-09-06 追记；原生 Kanban.swift：
// 横向 ScrollView 里每列 VStack { header; ScrollView(.vertical) { cards } }，窗口从不整体滚）。
// jsdom 没有布局，这里钉两样东西：(a) DOM 结构——滚动容器只有 `.column-list`，列头 / 输入框 / 「仅显示最近 N 条」
// 是它的兄弟节点；多选操作条是 `.board-page`（pages/BoardPage）的最后一个子项、不在 `.board-main` 横排里；(b) 样式文本——壳钉一屏高、
// `.shell-main` 是非看板页的滚动容器、`.column-list` / `.backlog-strip-list` 是各自的滚动容器、卡不被压扁。
// 真实的滚动几何由 e2e/lanesScroll.spec.ts 在真浏览器里验。
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchLanes } from "../api";
import chromeCss from "../components/chrome/chrome.css?raw";
import { refreshBoard, resetStoreForTests, setSelectionMode } from "../store";
import boardCss from "../styles/board.css?raw";
import shellCss from "../styles/shell.css?raw";
import type { Board } from "../types";
import { BoardPage } from "./BoardPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchLanes: vi.fn(), fetchCard: vi.fn() };
});

const board = {
  generated_at: "2026-09-06T12:00:00Z",
  counts: { needs_approval: 1, running: 1, needs_input: 0, review: 0, completed: 5, debt: 0, trash: 0, archived: 0 },
  needs_approval: [
    { id: "P-201", title: "a proposal", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] },
  ],
  running: [{ id: "R-100", name: "a run", state: "working" }],
  needs_input: [],
  review: [],
  // counts.completed（5）> 实际条数（1）→ 阶段性完成列渲染「仅显示最近 N 条」
  completed: [{ id: "R-050", name: "done", state: "delivered", delivered_at: "2026-09-06T10:00:00Z" }],
  debt: [],
  trash: [],
  archived: [],
} as unknown as Board;

const strip = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

/** 取 `selector { body }` 的 body（选择器逐字匹配，空白归一） */
function ruleBody(css: string, selector: string): string | null {
  const clean = strip(css).replace(/\s+/g, " ");
  const idx = clean.indexOf(`${selector} {`);
  if (idx < 0) return null;
  const open = clean.indexOf("{", idx);
  return clean.slice(open + 1, clean.indexOf("}", open)).trim();
}

beforeEach(async () => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.mocked(fetchBoard).mockResolvedValue(board);
  vi.mocked(fetchLanes).mockResolvedValue({ lanes: [] });
  await refreshBoard();
});

afterEach(cleanup);

describe("board DOM: only .column-list scrolls; header / composer / cap note are pinned siblings", () => {
  it("根是 .board-page > .board-main；每列的列头与输入框在 .column-list 之外、之前", () => {
    const { container } = render(<BoardPage />);
    const page = container.firstElementChild!;
    expect(page.classList.contains("board-page")).toBe(true);
    const main = page.firstElementChild!;
    expect(main.classList.contains("board-main")).toBe(true);

    const columns = Array.from(main.querySelectorAll(".board-column"));
    expect(columns.length).toBe(4);
    for (const column of columns) {
      const header = column.querySelector(".column-header")!;
      const list = column.querySelector(".column-list")!;
      expect(header.parentElement).toBe(column);
      expect(list.parentElement).toBe(column);
      // 列头在列表之前——滚动容器只是列的一段，不是整列
      expect(header.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      // 列表里只有卡 / 空态，没有列头 / 输入框
      expect(list.querySelector(".column-header, .lane-composer")).toBeNull();
      // 只有 .column-list 一个滚动容器（板上没有第二层会滚的东西）
      expect(column.querySelectorAll(".column-list").length).toBe(1);
    }

    // 提案列 / 运行中列的输入框：列的直接子项，位于列头与列表之间
    const composers = Array.from(main.querySelectorAll(".board-column .lane-composer"));
    expect(composers.length).toBe(2);
    for (const composer of composers) {
      const column = composer.closest(".board-column")!;
      expect(composer.parentElement).toBe(column);
      const list = column.querySelector(".column-list")!;
      expect(composer.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("「仅显示最近 N 条」是列表的兄弟节点、钉在列底（不随卡滚走）", () => {
    const { container } = render(<BoardPage />);
    const note = container.querySelector(".column-cap-note")!;
    expect(note).toBeTruthy();
    expect(note.textContent).toMatch(/仅显示最近 1 条|Showing the latest 1 only/);
    const column = note.closest(".board-column")!;
    expect(note.parentElement).toBe(column);
    expect(column.querySelector(".column-list")!.contains(note)).toBe(false);
  });

  it("多选操作条是 .board-page 的最后一个子项、不在 .board-main 的横排里；书立条仍是横排的首尾", () => {
    const { container } = render(<BoardPage />);
    act(() => setSelectionMode(true));
    const page = container.querySelector(".board-page")!;
    const main = page.querySelector(".board-main")!;
    const bar = page.querySelector(".selection-bar")!;
    expect(bar).toBeTruthy();
    expect(bar.parentElement).toBe(page);
    expect(page.lastElementChild).toBe(bar);
    expect(main.querySelector(".selection-bar")).toBeNull();
    expect(main.firstElementChild?.classList.contains("backlog-strip")).toBe(true);
    expect(main.lastElementChild?.classList.contains("is-archive")).toBe(true);
  });
});

describe("stylesheet pins (shell.css / board.css / chrome.css)", () => {
  it("壳钉一屏高（height: 100vh，不再 min-height）；.shell-main 是非看板页的滚动容器", () => {
    const shell = ruleBody(shellCss, ".shell")!;
    expect(shell).toContain("height: 100vh");
    expect(shell).not.toContain("min-height: 100vh");
    const body = ruleBody(shellCss, ".shell-body")!;
    expect(body).toContain("min-height: 0");
    const main = ruleBody(shellCss, ".shell-main")!;
    expect(main).toContain("min-height: 0");
    expect(main).toContain("overflow: auto");
  });

  it("导航栏与顶栏不再 sticky（文档不滚了，它们天然常驻）；拖宽把手仍有 relative 容器、顶栏仍是层叠上下文", () => {
    const rail = ruleBody(shellCss, ".rail")!;
    expect(rail).toContain("position: relative");
    expect(rail).not.toContain("sticky");
    const header = ruleBody(shellCss, ".shell-header")!;
    expect(header).toContain("position: relative");
    expect(header).toContain("z-index: 20");
    expect(header).not.toContain("sticky");
    expect(strip(shellCss)).not.toContain("position: sticky");
  });

  it(".board-page 竖排吃满一屏；.board-main 只横向滚（min-height: 0 让它缩得下去）", () => {
    const page = ruleBody(boardCss, ".board-page")!;
    expect(page).toContain("flex: 1");
    expect(page).toContain("min-height: 0");
    expect(page).toContain("flex-direction: column");
    const main = ruleBody(boardCss, ".board-main")!;
    expect(main).toContain("min-height: 0");
    expect(main).toContain("overflow-x: auto");
    expect(main).not.toMatch(/overflow-y|overflow: auto/);
    expect(ruleBody(boardCss, ".board-column")).toContain("min-height: 0");
  });

  it(".column-list 是列的滚动容器：吃掉剩余列高、overflow-y auto；卡不被压扁（子项 flex: 0 0 auto）", () => {
    const list = ruleBody(boardCss, ".column-list")!;
    expect(list).toContain("flex: 1 1 auto");
    expect(list).toContain("min-height: 0");
    expect(list).toContain("overflow-y: auto");
    // 负外边距 + 同宽内边距：滚动容器撑到列的全宽（焦点环 / 阴影不被裁、覆盖式滚动条不压卡），卡的几何不动
    expect(list).toContain("margin: 0 -10px");
    expect(list).toMatch(/padding: 0 10px/);
    expect(ruleBody(boardCss, ".column-list > *")).toContain("flex: 0 0 auto");
  });

  it("多选操作条不再 sticky / grid-column（那是横排里的死规则）：flex: none 贴在看板底", () => {
    const bar = ruleBody(boardCss, ".selection-bar")!;
    expect(bar).toContain("flex: none");
    expect(bar).not.toContain("position: sticky");
    expect(bar).not.toContain("grid-column");
    expect(bar).toContain("padding: 8px var(--native-layout-board-padding)");
  });

  it("两根书立条的展开列表是各自的滚动容器（与 .column-list 同一套）", () => {
    const list = ruleBody(chromeCss, ".backlog-strip-list")!;
    expect(list).toContain("flex: 1 1 auto");
    expect(list).toContain("min-height: 0");
    expect(list).toContain("overflow-y: auto");
    expect(list).toContain("margin: 0 -10px");
    expect(ruleBody(chromeCss, ".backlog-strip-list > *")).toContain("flex: 0 0 auto");
    expect(ruleBody(chromeCss, ".backlog-strip")).toContain("min-height: 0");
  });

  it("收起的书立条：竖排按钮靠 flex 撑满条高，不写 height: 100%（竖排百分比高会回落到视口高、把 .board-main 撑出纵向滚动）", () => {
    const head = ruleBody(chromeCss, ".backlog-strip.is-collapsed .backlog-strip-head")!;
    expect(head).toContain("flex: 1 1 auto");
    expect(head).toContain("min-height: 0");
    const toggle = ruleBody(chromeCss, ".backlog-strip.is-collapsed .backlog-strip-toggle")!;
    expect(toggle).toContain("writing-mode: vertical-rl");
    expect(toggle).toContain("align-self: stretch");
    expect(toggle).not.toMatch(/height: 100(%|vh)/);
  });

  it("列头 / 输入框不靠 sticky 钉住——它们在滚动容器之外（board.css 里没有 sticky 的列头规则）", () => {
    const clean = strip(boardCss);
    expect(clean).not.toMatch(/\.column-header[^{]*\{[^}]*position: sticky/);
    expect(clean).not.toMatch(/\.lane-composer[^{]*\{[^}]*position: sticky/);
  });
});
