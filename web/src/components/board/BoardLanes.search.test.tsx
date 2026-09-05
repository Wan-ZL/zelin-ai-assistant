// 看板装配 × ⌘F 搜索的 parity 判例（CONTRACT §37.2；原生 Store.boardApprovals + Kanban.swift）：
// 提案列 processing 占位卡不参与过滤隐藏（在途提交绝不「像丢了一样」消失）、搜索按 §37.2 归一化 AND
// 作用于全部列（display_title / former_titles / plan 也算）、运行中列的空态句 = 原生 composer 之下
// 真正渲染过的那句 lanePlaceholder（不是从未显示的 column(emptyText:) 参数）。
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchLanes } from "../../api";
import { refreshBoard, resetStoreForTests, setFilters } from "../../store";
import type { Board } from "../../types";
import { BoardLanes } from "./BoardLanes";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchLanes: vi.fn(), fetchCard: vi.fn() };
});

const board = {
  generated_at: "2026-09-01T12:00:00Z",
  counts: { needs_approval: 3, running: 2, needs_input: 0, review: 0, completed: 0, debt: 0, trash: 0, archived: 0 },
  needs_approval: [
    { id: "R-201", title: "EB-1A petition", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] },
    { id: "R-202", title: "https://example.com/a/b", display_title: "写推荐信", former_titles: ["green card memo"],
      tier: "T1", show_cost: false, processing: false, sources: [], plan: ["call lawyer"], dod: [] },
    { id: "R-203", title: "raising placeholder", tier: "T1", show_cost: false, processing: true, sources: [], plan: [], dod: [] },
  ],
  running: [
    { id: "R-301", name: "unrelated run", state: "working" },
    { id: "R-302", name: "H-1B transfer", state: "queued" },
  ],
  needs_input: [],
  review: [],
  completed: [],
  debt: [],
  trash: [],
  archived: [],
} as unknown as Board;

function laneTitles(container: HTMLElement, laneIndex: number): string[] {
  const lane = container.querySelectorAll(".board-column")[laneIndex];
  return Array.from(lane.querySelectorAll(".card-head .card-title")).map((el) => el.textContent ?? "");
}

function laneEmptyText(container: HTMLElement, laneIndex: number): string | null {
  const lane = container.querySelectorAll(".board-column")[laneIndex];
  return lane.querySelector(".column-empty")?.textContent ?? null;
}

beforeEach(async () => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.mocked(fetchBoard).mockResolvedValue(board);
  vi.mocked(fetchLanes).mockResolvedValue({ lanes: [] });
  await refreshBoard();
});

afterEach(cleanup);

describe("processing rows never hide behind a search (Store.boardApprovals)", () => {
  it("搜索词不命中占位卡也照样留在提案列顶；真实卡按 §37.2 过滤", () => {
    const { container } = render(<BoardLanes />);
    act(() => setFilters({ search: "eb1" }));
    expect(laneTitles(container, 0)).toEqual(["raising placeholder", "EB-1A petition"]);
    // 徽章 = 命中/总数（占位卡计入命中）
    const count = container.querySelectorAll(".board-column")[0].querySelector(".lane-count")?.textContent;
    expect(count).toBe("2/3");
  });

  it("tier chip 也放占位卡过——在途提交绝不因过滤器而消失", () => {
    const { container } = render(<BoardLanes />);
    act(() => setFilters({ tiers: ["T2"] }));
    expect(laneTitles(container, 0)).toEqual(["raising placeholder"]);
  });
});

describe("§37.2 fields + normalisation reach the board", () => {
  it("搜 display_title / former_titles / plan 命中改名过的卡；运行中列 h1b 命中 H-1B", () => {
    const { container } = render(<BoardLanes />);
    act(() => setFilters({ search: "推荐信" }));
    expect(laneTitles(container, 0)).toEqual(["raising placeholder", "写推荐信"]);
    act(() => setFilters({ search: "memo" }));
    expect(laneTitles(container, 0)).toEqual(["raising placeholder", "写推荐信"]);
    act(() => setFilters({ search: "lawyer" }));
    expect(laneTitles(container, 0)).toEqual(["raising placeholder", "写推荐信"]);
    act(() => setFilters({ search: "h1b" }));
    expect(laneTitles(container, 1)).toEqual(["H-1B transfer"]);
  });

  it("两个词 AND：'eb2 petition' 一张都不命中，提案列只剩占位卡", () => {
    const { container } = render(<BoardLanes />);
    act(() => setFilters({ search: "eb2 petition" }));
    expect(laneTitles(container, 0)).toEqual(["raising placeholder"]);
    expect(laneEmptyText(container, 1)).toBe("No matching cards");
  });
});

describe("running-lane empty copy (Kanban.swift lanePlaceholder under the composer)", () => {
  it("运行中列空时显示原生 composer 之下那句，不是从未渲染的 column(emptyText:) 参数", async () => {
    vi.mocked(fetchBoard).mockResolvedValue({ ...board, running: [], counts: { ...board.counts, running: 0 } } as Board);
    await refreshBoard();
    const { container } = render(<BoardLanes />);
    expect(laneEmptyText(container, 1)).toBe("Nothing running — approve a proposal, or type above to run one now");
    expect(screen.queryByText("Nothing running — approve a proposal to start")).toBeNull();
  });
});
