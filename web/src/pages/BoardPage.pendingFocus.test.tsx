// BoardPage 挂载消费「聚焦捕获框」接力棒（CONTRACT §54.4 2026-09-05 追记；原生 AppDelegate.swift focusCaptureField 的
// 跨页半程）：从设置页等非看板页 ⌘L / quick_capture 过来时 focusComposer 只留下 sessionStorage `zai.pendingFocus`
// 并整页导航，真正那一下聚焦由新文档的 BoardPage 挂载时补上。这里钉的是接线本身——真 BoardLanes / LaneComposer 的
// DOM（不是手写的 fixture），挂载 = 聚焦捕获框 + 删标记；没标记不抢焦点；再挂载（刷新）不重放。
// §78：提案列退役，捕获框搬到潜在任务条（BacklogStrip）的条头——⌘L 的落点跟着它走，运行中列的直跑框
// 永不接这一下（全局键只许记一件事，不许替 owner 起跑）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "../api";
import { PENDING_FOCUS_KEY } from "../components/board/focusComposer";
import { refreshBoard, resetStoreForTests } from "../store";
import type { Board } from "../types";
import { BoardPage } from "./BoardPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchLanes: vi.fn(), fetchCard: vi.fn() };
});

const CAPTURE_PLACEHOLDER = "One line — jot it down, the AI fills in the plan…";
const RUN_PLACEHOLDER = "One line — run it now…";
const RETIRED_PROPOSE_PLACEHOLDER = "One sentence — AI researches and proposes…";

const board = {
  generated_at: "2026-09-05T12:00:00Z",
  counts: { needs_approval: 0, running: 0, needs_input: 0, review: 0, completed: 0, debt: 1, trash: 0, archived: 0 },
  needs_approval: [],
  debt: [
    { id: "R-001", title: "a backlog card", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] },
  ],
  running: [], needs_input: [], review: [], completed: [], trash: [], archived: [],
} as unknown as Board;

beforeEach(async () => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.mocked(fetchBoard).mockResolvedValue(board);
  await refreshBoard();
});

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
});

describe("BoardPage — 挂载消费 zai.pendingFocus 接力棒", () => {
  it("有标记：挂载即聚焦潜在任务条的捕获框（不是直跑框），并把标记删掉", () => {
    window.sessionStorage.setItem(PENDING_FOCUS_KEY, "composer");
    render(<BoardPage />);
    expect(screen.queryByPlaceholderText(RETIRED_PROPOSE_PLACEHOLDER)).toBeNull();
    expect(document.activeElement).toBe(screen.getByPlaceholderText(CAPTURE_PLACEHOLDER));
    expect(document.activeElement).not.toBe(screen.getByPlaceholderText(RUN_PLACEHOLDER));
    expect(window.sessionStorage.getItem(PENDING_FOCUS_KEY)).toBeNull();
  });

  it("没标记：挂载不抢焦点（普通打开看板，焦点留在 body）", () => {
    render(<BoardPage />);
    screen.getByPlaceholderText(CAPTURE_PLACEHOLDER);
    expect(document.activeElement).toBe(document.body);
    expect(window.sessionStorage.getItem(PENDING_FOCUS_KEY)).toBeNull();
  });

  it("接力棒一次性：消费过再挂载（刷新）不重放聚焦", () => {
    window.sessionStorage.setItem(PENDING_FOCUS_KEY, "composer");
    const first = render(<BoardPage />);
    expect(document.activeElement).toBe(screen.getByPlaceholderText(CAPTURE_PLACEHOLDER));
    first.unmount();
    expect(document.activeElement).toBe(document.body);
    render(<BoardPage />);
    screen.getByPlaceholderText(CAPTURE_PLACEHOLDER);
    expect(document.activeElement).toBe(document.body);
  });
});
