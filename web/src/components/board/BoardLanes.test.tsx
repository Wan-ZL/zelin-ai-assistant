// 看板装配的 parity 判例：默认新的在上（P-/R- 混排按数字后缀）、潜在任务条里的 processing 占位钉顶、
// 排序偏好切换即重排并持久化 cardSortOrder、原生 composer 占位文案、列头「?」说明来自
// server 目录（目录未到不渲染）、右侧「永久性完成」书立条在场。
// §78：提案列退役——机器卡（含 raising 灰占位）都在左侧潜在任务条里，`.board-column` 只剩
// 运行中 / 待验收 / 阶段性完成三列。
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchLanes } from "../../api";
import { getState, refreshBoard, refreshLanes, resetStoreForTests, setSortOrder } from "../../store";
import type { Board } from "../../types";
import { BoardLanes } from "./BoardLanes";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchLanes: vi.fn(), fetchCard: vi.fn() };
});

const board = {
  generated_at: "2026-09-01T12:00:00Z",
  counts: { needs_approval: 0, running: 3, needs_input: 0, review: 0, completed: 0, debt: 4, trash: 0, archived: 7 },
  needs_approval: [],
  running: [
    { id: "R-100", name: "run old", state: "working" },
    { id: "P-300", name: "run new", state: "working" },
    { id: "R-250", name: "run mid", state: "queued" },
  ],
  needs_input: [],
  review: [],
  completed: [],
  debt: [
    { id: "R-005", title: "old proposal", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] },
    { id: "P-201", title: "newest proposal", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] },
    { id: "R-180", title: "mid proposal", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] },
    { id: "R-002", title: "placeholder", tier: "T1", show_cost: false, processing: true, sources: [], plan: [], dod: [] },
  ],
  trash: [],
  archived: [],
} as unknown as Board;

const lanes = {
  lanes: [
    { slug: "debt", help: { zh: "潜在任务说明", en: "Backlog help text" } },
    { slug: "running", help: { zh: "运行中说明", en: "Running help text" } },
  ],
};

function laneTitles(container: HTMLElement, laneIndex: number): string[] {
  const lane = container.querySelectorAll(".board-column")[laneIndex];
  return Array.from(lane.querySelectorAll(".card-head .card-title")).map((el) => el.textContent ?? "");
}

/** 左侧潜在任务条里的卡序（BoardLanes 经 renderCard 注入 DebtCardItem） */
function backlogTitles(container: HTMLElement): string[] {
  const strip = container.querySelector(".backlog-strip:not(.is-archive)")!;
  return Array.from(strip.querySelectorAll(".card-head .card-title")).map((el) => el.textContent ?? "");
}

beforeEach(async () => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.mocked(fetchBoard).mockResolvedValue(board);
  vi.mocked(fetchLanes).mockResolvedValue(lanes);
  await refreshBoard();
});

afterEach(cleanup);

describe("lane sort", () => {
  it("默认 newest：数字后缀降序不看前缀；潜在任务条的 processing 占位钉在顶", () => {
    const { container } = render(<BoardLanes />);
    expect(backlogTitles(container)).toEqual(["placeholder", "newest proposal", "mid proposal", "old proposal"]);
    expect(laneTitles(container, 0)).toEqual(["run new", "run mid", "run old"]);
  });

  it("切到 oldest 即重排，并持久化到 localStorage cardSortOrder", () => {
    const { container } = render(<BoardLanes />);
    act(() => setSortOrder("oldest"));
    expect(backlogTitles(container)).toEqual(["placeholder", "old proposal", "mid proposal", "newest proposal"]);
    expect(laneTitles(container, 0)).toEqual(["run old", "run mid", "run new"]);
    expect(window.localStorage.getItem("cardSortOrder")).toBe("oldest");
    expect(getState().sortOrder).toBe("oldest");
  });

  it("deadline 模式：潜在任务条里有期限的先按日期升序，无期限的按 newest；运行中列退化为 newest", () => {
    const dated = {
      ...board,
      debt: board.debt.map((c) =>
        c.id === "R-005" ? { ...c, deadline: "2026-09-20" } : c.id === "R-180" ? { ...c, deadline: "2026-09-02" } : c),
    } as Board;
    vi.mocked(fetchBoard).mockResolvedValue(dated);
    return refreshBoard().then(() => {
      const { container } = render(<BoardLanes />);
      act(() => setSortOrder("deadline"));
      expect(backlogTitles(container)).toEqual(["placeholder", "mid proposal", "old proposal", "newest proposal"]);
      expect(laneTitles(container, 0)).toEqual(["run new", "run mid", "run old"]);
    });
  });
});

describe("composer placeholders (native wording)", () => {
  // §78：捕获框搬到潜在任务条头（意图 = 记一件事，落 detected），直跑框留在运行中列头（意图 = 现在
  // 就做，落 approved）；两句占位文案里点名退役列的字样（「（跳过提案）」/「AI 来研究并提案」）随列一起走。
  it("捕获框在左条、直跑框在运行中列；两句占位文案都不再点名退役的提案列", () => {
    const { container } = render(<BoardLanes />);
    const capture = screen.getByPlaceholderText("One line — jot it down, the AI fills in the plan…");
    expect(capture.closest(".backlog-strip")).toBeTruthy();
    expect(capture.closest(".board-column")).toBeNull();

    const run = screen.getByPlaceholderText("One line — run it now…");
    expect(run.closest(".board-column")).toBeTruthy();

    expect(screen.queryByPlaceholderText("One sentence — AI researches and proposes…")).toBeNull();
    expect(screen.queryByPlaceholderText("One line — run it now (skips proposal)…")).toBeNull();
    // 看板上只有这两只框：捕获 1 + 直跑 1（列里再没有第二只捕获框抢 ⌘L）
    expect(container.querySelectorAll(".lane-composer textarea")).toHaveLength(2);
  });
});

describe("lane help ? (server-owned catalog)", () => {
  it("目录未到：列头没有「?」也没有长段说明", () => {
    const { container } = render(<BoardLanes />);
    expect(container.querySelector(".lane-help-button")).toBeNull();
    expect(container.querySelector(".column-help")).toBeNull();
  });

  it("目录到位：有说明的列头出「?」（title = 文案），点击开气泡；无说明的列没有", async () => {
    await act(() => refreshLanes());
    const { container } = render(<BoardLanes />);
    const buttons = container.querySelectorAll(".lane-help-button");
    expect(buttons).toHaveLength(2);   // 潜在任务条 + 运行中列
    expect(buttons[0].getAttribute("title")).toBe("Backlog help text");
    act(() => {
      (buttons[0] as HTMLButtonElement).click();
    });
    expect(screen.getByRole("tooltip").textContent).toBe("Backlog help text");
  });
});

describe("done-for-good rail", () => {
  it("右侧书立条在场、默认收起、计数读 counts.archived 真实总数", () => {
    const { container } = render(<BoardLanes />);
    const rail = container.querySelector(".backlog-strip.is-archive")!;
    expect(rail).toBeTruthy();
    expect(rail.classList.contains("is-collapsed")).toBe(true);
    expect(rail.querySelector(".backlog-strip-count")?.textContent).toBe("7");
    // 左条在最前、右条在最后（原生两根书立条的位置；§66 探针 lanes:rail-left / rail-right 按这个判）
    const main = container.querySelector(".board-main")!;
    expect(main.firstElementChild?.classList.contains("backlog-strip")).toBe(true);
    expect(main.lastElementChild?.classList.contains("is-archive")).toBe(true);
  });

  it("§78：看板只剩运行中 / 待验收 / 阶段性完成三列，提案列不在", () => {
    const { container } = render(<BoardLanes />);
    expect(container.querySelectorAll(".board-column")).toHaveLength(3);
    expect(screen.queryByText("Proposals")).toBeNull();
  });
});
