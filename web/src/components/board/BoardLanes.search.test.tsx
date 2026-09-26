// 看板装配 × ⌘F 搜索的 parity 判例（CONTRACT §37.2；原生 Store.boardApprovals + Kanban.swift）：
// processing 占位卡不被搜索词藏起（在途提交绝不「像丢了一样」消失）——但只有搜索这一维：
// 原生没有 chips，tier / 期限 / 回锅 chips 是 web 加的，占位卡对它们照常判定；搜索按 §37.2 归一化 AND
// 作用于全部面（display_title / former_titles / plan 也算）、运行中列的空态句 = 原生 composer 之下
// 真正渲染过的那句 lanePlaceholder（不是从未显示的 column(emptyText:) 参数）。
// §78：提案列退役——占位卡（raising 灰卡）与它的「不被搜索藏起」规矩一起搬进左侧潜在任务条。
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
  counts: { needs_approval: 0, running: 2, needs_input: 0, review: 0, completed: 0, debt: 3, trash: 0, archived: 0 },
  needs_approval: [],
  debt: [
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
  trash: [],
  archived: [],
} as unknown as Board;

/** 左侧潜在任务书立条里的卡序（右条是 .is-archive） */
function backlogTitles(container: HTMLElement): string[] {
  const strip = container.querySelector(".backlog-strip:not(.is-archive)")!;
  return Array.from(strip.querySelectorAll(".card-head .card-title")).map((el) => el.textContent ?? "");
}

function backlogCount(container: HTMLElement): string | undefined {
  return container.querySelector(".backlog-strip:not(.is-archive) .backlog-strip-count")?.textContent ?? undefined;
}

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
  it("搜索词不命中占位卡也照样留在潜在任务条顶；真实卡按 §37.2 过滤", () => {
    const { container } = render(<BoardLanes />);
    act(() => setFilters({ search: "eb1" }));
    expect(backlogTitles(container)).toEqual(["raising placeholder", "EB-1A petition"]);
    // 计数 = 命中/总数（占位卡计入命中）
    expect(backlogCount(container)).toBe("2/3");
  });

  it("直通只有搜索这一维：tier chip 对占位卡照常判定（原生没有 chips，不在此扩权）", () => {
    const { container } = render(<BoardLanes />);
    act(() => setFilters({ tiers: ["T2"] }));
    expect(backlogTitles(container)).toEqual([]);
    expect(container.querySelector(".backlog-strip:not(.is-archive) .trash-empty")?.textContent).toBe("No matching cards");
    expect(backlogCount(container)).toBe("0/3");
  });

  it("chips + 搜索同时开：占位卡过 chip 就留（搜索词不看），不过 chip 就走", () => {
    const { container } = render(<BoardLanes />);
    act(() => setFilters({ tiers: ["T1"], search: "eb1" }));
    expect(backlogTitles(container)).toEqual(["raising placeholder", "EB-1A petition"]);
    act(() => setFilters({ tiers: ["T2"], search: "eb1" }));
    expect(backlogTitles(container)).toEqual([]);
    act(() => setFilters({ tiers: [], reraisedOnly: true, search: "eb1" }));
    expect(backlogTitles(container)).toEqual([]);
  });
});

describe("§37.2 fields + normalisation reach the board", () => {
  it("搜 display_title / former_titles / plan 命中改名过的卡；运行中列 h1b 命中 H-1B", () => {
    const { container } = render(<BoardLanes />);
    act(() => setFilters({ search: "推荐信" }));
    expect(backlogTitles(container)).toEqual(["raising placeholder", "写推荐信"]);
    act(() => setFilters({ search: "memo" }));
    expect(backlogTitles(container)).toEqual(["raising placeholder", "写推荐信"]);
    act(() => setFilters({ search: "lawyer" }));
    expect(backlogTitles(container)).toEqual(["raising placeholder", "写推荐信"]);
    act(() => setFilters({ search: "h1b" }));
    expect(laneTitles(container, 0)).toEqual(["H-1B transfer"]);
  });

  it("两个词 AND：'eb2 petition' 一张都不命中，潜在任务条只剩占位卡", () => {
    const { container } = render(<BoardLanes />);
    act(() => setFilters({ search: "eb2 petition" }));
    expect(backlogTitles(container)).toEqual(["raising placeholder"]);
    expect(laneEmptyText(container, 0)).toBe("No matching cards");
  });
});

describe("running-lane empty copy (Kanban.swift lanePlaceholder under the composer)", () => {
  it("运行中列空时显示原生 composer 之下那句，不是从未渲染的 column(emptyText:) 参数", async () => {
    vi.mocked(fetchBoard).mockResolvedValue({ ...board, running: [], counts: { ...board.counts, running: 0 } } as Board);
    await refreshBoard();
    const { container } = render(<BoardLanes />);
    expect(laneEmptyText(container, 0)).toBe("Nothing running — approve a proposal, or type above to run one now");
    expect(screen.queryByText("Nothing running — approve a proposal to start")).toBeNull();
  });
});
