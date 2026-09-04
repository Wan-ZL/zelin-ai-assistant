// BacklogStrip 行为：折叠/展开、吃全局过滤器（计数 x/y）、行点击开抽屉 + ?card= 深链。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { getState, refreshBoard, resetStoreForTests, setFilters } from "../../store";
import { fetchBoard, fetchCard } from "../../api";
import type { Board } from "../../types";
import { BacklogStrip } from "./BacklogStrip";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchCard: vi.fn() };
});

const board = {
  generated_at: "2026-08-30T12:00:00Z",
  counts: { debt: 2 },
  needs_approval: [], running: [], needs_input: [], review: [], completed: [],
  debt: [
    { id: "R-301", title: "README 安装一节过时", type: "engineering",
      sources: [{ who: "sam", channel: "slack", date: "d", quote: "q" }] },
    { id: "R-302", title: "周会纪要没人整理", type: "process",
      sources: [{ who: "manager", channel: "meeting", date: "d", quote: "q" }] },
  ],
  trash: [],
} as unknown as Board;

beforeEach(async () => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.mocked(fetchBoard).mockResolvedValue(board);
  vi.mocked(fetchCard).mockResolvedValue({ id: "R-302" });
  await refreshBoard();
});

afterEach(cleanup);

describe("BacklogStrip", () => {
  it("默认折叠只显计数；点击展开列出 debt 行", () => {
    render(<BacklogStrip />);
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.queryByText(/README/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Backlog/ }));
    expect(screen.getByText(/README/)).toBeTruthy();
    expect(screen.getByText(/周会纪要/)).toBeTruthy();
  });

  it("吃全局 ⌘F 搜索（D28 后 debt 行唯一适用的维度）：计数显 1/2，只剩匹配行", () => {
    render(<BacklogStrip />);
    fireEvent.click(screen.getByRole("button", { name: /Backlog/ }));
    act(() => setFilters({ search: "周会" }));

    expect(screen.getByText("1/2")).toBeTruthy();
    expect(screen.queryByText(/README/)).toBeNull();
    expect(screen.getByText(/周会纪要/)).toBeTruthy();
  });

  it("行点击 = selectCard + ?card= 深链同步", () => {
    render(<BacklogStrip />);
    fireEvent.click(screen.getByRole("button", { name: /Backlog/ }));
    act(() => {
      fireEvent.click(screen.getByText(/周会纪要/));
    });
    expect(getState().selectedCardId).toBe("R-302");
    expect(new URLSearchParams(window.location.search).get("card")).toBe("R-302");
  });
});
