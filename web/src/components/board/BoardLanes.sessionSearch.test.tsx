// 看板 × ⌘F 的会话内容层（CONTRACT §37.2 第三条；原生 Store.hitInfo + Kanban.swift 六处 `sessionHit:` + Cards.swift
// SessionHitBadge；owner 决策 D45）：只靠会话正文命中的卡留在列里、计入「命中/总数」、章行长出紫章「命中会话」；
// 卡面字段本就命中的卡不出章（诚实条件）；跨层 AND（"推荐信 chen"）；六列（提案 / 运行中 / 需输入 / 待验收 /
// 阶段性完成 / 潜在任务）都参与；层缺席（文件不在 → 空快照）= 字段搜索照常、零章。fetchSearchIndex 经 vi.mock 注入，零真实网络。
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchLanes, fetchSearchIndex } from "../../api";
import { refreshBoard, refreshSessionIndex, resetStoreForTests, setFilters } from "../../store";
import type { Board } from "../../types";
import { BoardLanes } from "./BoardLanes";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchLanes: vi.fn(), fetchCard: vi.fn(), fetchSearchIndex: vi.fn() };
});

const board = {
  generated_at: "2026-09-06T12:00:00Z",
  counts: { needs_approval: 2, running: 1, needs_input: 1, review: 1, completed: 1, debt: 2, trash: 0, archived: 0 },
  needs_approval: [
    { id: "P-201", title: "写推荐信", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] },
    { id: "P-202", title: "EB-1A petition", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] },
  ],
  running: [{ id: "P-301", name: "unrelated run", state: "working" }],
  needs_input: [{ id: "P-401", name: "blocked one", state: "working", waiting_for: "answer" }],
  review: [{ id: "P-501", name: "review one", delivered_summary: "done", copy_cmd: "claude --resume abc" }],
  completed: [{ id: "P-601", name: "shipped one", state: "delivered", accepted_at: 1_757_000_000 }],
  debt: [
    { id: "P-701", title: "backlog one", type: "research" },
    { id: "P-702", title: "backlog two", type: "research" },
  ],
  trash: [],
  archived: [],
} as unknown as Board;

// 六列各一张卡只有会话里提到 chen；P-202（卡面就有 EB-1A）的会话也提 chen——它字段命中 eb1、不该出章
const INDEX = {
  etag: '"7-70"',
  snapshot: {
    entries: {
      "P-201": "we discussed Chen's timeline",
      "P-202": "chen asked about EB-1A",
      "P-301": "chen pinged the run",
      "P-401": "waiting on chen",
      "P-501": "chen reviewed",
      "P-601": "chen accepted",
      "P-701": "chen's backlog idea",
    },
    truncated: false,
  },
};

const BADGE = "Session match";

function lane(container: HTMLElement, index: number): Element {
  return container.querySelectorAll(".board-column")[index];
}
/** 列里的卡面标题（排序偏好不是本判例的事——按字典序比） */
function titles(el: ParentNode): string[] {
  return Array.from(el.querySelectorAll(".card-head .card-title")).map((n) => n.textContent ?? "").sort();
}
function badgesOf(el: ParentNode): number {
  return Array.from(el.querySelectorAll(".chip")).filter((c) => c.textContent === BADGE).length;
}
async function search(query: string) {
  await act(async () => {
    setFilters({ search: query });
    await refreshSessionIndex(); // 合并在途的懒加载；已加载则 304 mock 也照样落地
  });
}

beforeEach(async () => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.mocked(fetchBoard).mockResolvedValue(board);
  vi.mocked(fetchLanes).mockResolvedValue({ lanes: [] });
  vi.mocked(fetchSearchIndex).mockReset();
  vi.mocked(fetchSearchIndex).mockResolvedValue(INDEX);
  await refreshBoard();
});

afterEach(cleanup);

describe("session-only hits stay on the board with the 「命中会话」 badge", () => {
  it("搜 chen：六列各留下那张只有会话提过的卡、每张一颗紫章；字段命中的 EB-1A 卡不出章；计数 = 命中/总数", async () => {
    const { container } = render(<BoardLanes />);
    await search("chen");
    // 提案列：P-201（会话）+ P-202（会话里也有 chen，但卡面无 chen → 也是会话命中）
    expect(titles(lane(container, 0))).toEqual(["EB-1A petition", "写推荐信"]);
    expect(badgesOf(lane(container, 0))).toBe(2);
    expect(lane(container, 0).querySelector(".lane-count")?.textContent).toBe("2");
    expect(titles(lane(container, 1))).toEqual(["blocked one", "unrelated run"]);
    expect(badgesOf(lane(container, 1))).toBe(2);
    expect(titles(lane(container, 2))).toEqual(["review one"]);
    expect(badgesOf(lane(container, 2))).toBe(1);
    expect(titles(lane(container, 3))).toEqual(["shipped one"]);
    expect(badgesOf(lane(container, 3))).toBe(1);
    // 潜在任务条：命中 → 强制展开，只剩 P-701 且带章，计数 1/2
    const strip = container.querySelector(".backlog-strip")!;
    expect(strip.classList.contains("is-collapsed")).toBe(false);
    expect(titles(strip)).toEqual(["backlog one"]);
    expect(badgesOf(strip)).toBe(1);
    expect(strip.querySelector(".backlog-strip-count")?.textContent).toBe("1/2");
    // 章的样式 = 原生 .purple
    const chip = Array.from(container.querySelectorAll(".chip")).find((c) => c.textContent === BADGE)!;
    expect(chip.className).toBe("chip chip-purple");
  });

  it("字段命中不出章（章不是「有会话」）：搜 eb1 只留 EB-1A 卡、零章", async () => {
    const { container } = render(<BoardLanes />);
    await search("eb1");
    expect(titles(lane(container, 0))).toEqual(["EB-1A petition"]);
    expect(badgesOf(container)).toBe(0);
    expect(lane(container, 0).querySelector(".lane-count")?.textContent).toBe("1/2");
  });

  it("跨层 AND：'推荐信 chen' 只留 写推荐信（推荐信靠字段、chen 靠会话）且出章；'eb2 chen' 一张都不留", async () => {
    const { container } = render(<BoardLanes />);
    await search("推荐信 chen");
    expect(titles(lane(container, 0))).toEqual(["写推荐信"]);
    expect(badgesOf(lane(container, 0))).toBe(1);
    expect(titles(lane(container, 1))).toEqual([]);
    await search("eb2 chen");
    expect(titles(lane(container, 0))).toEqual([]);
    expect(screen.queryByText(BADGE)).toBeNull();
  });

  it("清掉搜索词 → 章全部退场、列恢复全量", async () => {
    const { container } = render(<BoardLanes />);
    await search("chen");
    expect(badgesOf(container)).toBeGreaterThan(0);
    act(() => setFilters({ search: "" }));
    expect(badgesOf(container)).toBe(0);
    expect(titles(lane(container, 0))).toEqual(["EB-1A petition", "写推荐信"]);
  });

  it("层缺席（文件不在 → 空快照）：字段搜索照常、零章、只有会话提过的卡藏起", async () => {
    vi.mocked(fetchSearchIndex).mockResolvedValue({ etag: null, snapshot: { entries: {}, truncated: false } });
    const { container } = render(<BoardLanes />);
    await search("chen");
    expect(titles(lane(container, 0))).toEqual([]);
    expect(lane(container, 0).querySelector(".column-empty")?.textContent).toBe("No matching cards");
    expect(badgesOf(container)).toBe(0);
    await search("eb1");
    expect(titles(lane(container, 0))).toEqual(["EB-1A petition"]);
  });

  it("索引换版（新 ETag）后命中跟着变：新版里 P-202 的会话没有 chen 了 → 它离开列", async () => {
    const { container } = render(<BoardLanes />);
    await search("chen");
    expect(titles(lane(container, 0))).toEqual(["EB-1A petition", "写推荐信"]);
    vi.mocked(fetchSearchIndex).mockResolvedValue({
      etag: '"8-80"',
      snapshot: { entries: { ...INDEX.snapshot.entries, "P-202": "nothing here" }, truncated: false },
    });
    await act(async () => {
      await refreshBoard(); // 搜索开着 → 每版看板落地重验索引（带旧 ETag）
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(vi.mocked(fetchSearchIndex)).toHaveBeenLastCalledWith('"7-70"');
    expect(titles(lane(container, 0))).toEqual(["写推荐信"]);
  });
});
