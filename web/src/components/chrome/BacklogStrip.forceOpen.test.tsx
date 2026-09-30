// 潜在任务书立条的展开态挂 store、搜索命中强制展开（§54.1 追记 `strips-force-open`；原生 Kanban.swift:316-328）：
// 收起 + 搜索命中 → 列表可见（旗不动，清掉查询即收回）；无命中不强开；强制展开期间列头是 no-op；
// 旗在会话内跨卸载 / 重挂（换页）留存；store setter 直接打开（useSubmit 的强制展开走它）。
// §78 / D80.3：出厂**展开**（机器卡的收件箱不许默认藏起来），所以每条先手动收起再测强制展开这一跳。
// 末段（§44.6 / §21bis / §21）：三种通知落地都得把收起的条打开——**逐条通知各算一次**，
// 且它们钉在条顶（列表的兄弟节点），不许落进滚动的 `.backlog-strip-list` 里被滚出视口。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  dismissForceMergeTimeout,
  FORCE_MERGE_TIMEOUT_MS,
  getState,
  markForceMerging,
  refreshBoard,
  resetStoreForTests,
  setBacklogStripExpanded,
  setFilters,
} from "../../store";
import { fetchBoard, fetchCard } from "../../api";
import type { Board } from "../../types";
import { BacklogStrip } from "./BacklogStrip";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchCard: vi.fn() };
});

const board = {
  generated_at: "2026-09-05T12:00:00Z",
  counts: { debt: 2 },
  needs_approval: [], running: [], needs_input: [], review: [], completed: [],
  debt: [
    { id: "R-301", title: "README 安装一节过时", type: "engineering", tier: "T1",
      sources: [{ who: "sam", channel: "slack", date: "d", quote: "q" }] },
    { id: "R-302", title: "周会纪要没人整理", type: "process", tier: "T2",
      sources: [{ who: "manager", channel: "meeting", date: "d", quote: "q" }] },
  ],
  trash: [],
} as unknown as Board;

/** 同一版看板 + 额外的顶层键（fold_receipts / merge_suggestions）；每次换 generated_at 免得被当成同一版 */
async function load(over: Record<string, unknown>, generated_at = "2026-09-05T12:00:01Z") {
  vi.mocked(fetchBoard).mockResolvedValue({ ...board, generated_at, ...over } as unknown as Board);
  await act(async () => {
    await refreshBoard();
  });
}

const receipt = (id: string, req: string) => ({ id, req, title: "周会纪要没人整理", channel: "quick_capture", at: 1 });
const collapse = () => fireEvent.click(toggle());

beforeEach(async () => {
  window.history.replaceState(null, "", "/");
  window.sessionStorage.clear();   // §44.6 「看过没」的真源；上一条 case 关掉的回执不许漏进下一条
  resetStoreForTests();
  setBacklogStripExpanded(false);   // §78 出厂展开 → 先收起，被测的是「关着的条会不会被打开」
  vi.mocked(fetchBoard).mockResolvedValue(board);
  vi.mocked(fetchCard).mockResolvedValue({ id: "R-302" });
  await refreshBoard();
});

afterEach(cleanup);

function strip(container: HTMLElement) {
  return container.querySelector(".backlog-strip")!;
}
const toggle = () => screen.getByRole("button", { name: /Backlog/ });

describe("BacklogStrip：搜索命中强制展开", () => {
  it("收起 + ⌘F 命中 → 列表可见、aria-expanded=true；清掉查询 → 回到收起（旗没被搜索改动）", () => {
    const { container } = render(<BacklogStrip />);
    expect(strip(container).classList.contains("is-collapsed")).toBe(true);
    expect(screen.queryByText(/周会纪要/)).toBeNull();

    act(() => setFilters({ search: "周会" }));
    expect(strip(container).classList.contains("is-collapsed")).toBe(false);
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(/周会纪要/)).toBeTruthy();
    expect(screen.queryByText(/README/)).toBeNull();
    expect(screen.getByText("1/2")).toBeTruthy();
    expect(getState().backlogStripExpanded).toBe(false); // 强制展开是视图态，不写旗

    act(() => setFilters({ search: "" }));
    expect(strip(container).classList.contains("is-collapsed")).toBe(true);
    expect(screen.queryByText(/周会纪要/)).toBeNull();
  });

  it("过滤 chips 命中（tier）同样强制展开——条吃的是同一套全局过滤器", () => {
    const { container } = render(<BacklogStrip />);
    act(() => setFilters({ tiers: ["T2"] }));
    expect(strip(container).classList.contains("is-collapsed")).toBe(false);
    expect(screen.getByText(/周会纪要/)).toBeTruthy();
    expect(screen.queryByText(/README/)).toBeNull();
  });

  it("搜索无命中 → 不强开（原生 `!debt.isEmpty` 半边）：仍收起，计数 0/2", () => {
    const { container } = render(<BacklogStrip />);
    act(() => setFilters({ search: "不存在的词" }));
    expect(strip(container).classList.contains("is-collapsed")).toBe(true);
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByText("0/2")).toBeTruthy();
  });

  it("强制展开期间列头开合是 no-op（原生 `.constant(true)`）：点了不收、旗也不翻；清掉查询后回到旗的状态", () => {
    const { container } = render(<BacklogStrip />);
    act(() => setFilters({ search: "周会" }));
    fireEvent.click(toggle());
    expect(strip(container).classList.contains("is-collapsed")).toBe(false);
    expect(getState().backlogStripExpanded).toBe(false);

    act(() => setFilters({ search: "" }));
    expect(strip(container).classList.contains("is-collapsed")).toBe(true);
  });

  it("用户展开过再搜索：清掉查询后仍是展开（旗为 true）", () => {
    const { container } = render(<BacklogStrip />);
    fireEvent.click(toggle());
    expect(getState().backlogStripExpanded).toBe(true);
    act(() => setFilters({ search: "周会" }));
    act(() => setFilters({ search: "" }));
    expect(strip(container).classList.contains("is-collapsed")).toBe(false);
    expect(screen.getByText(/README/)).toBeTruthy();
  });
});

describe("BacklogStrip：展开态挂 store", () => {
  it("点列头展开写旗；卸载再挂（换页）仍展开——原生「survives page switches within a session」", () => {
    const first = render(<BacklogStrip />);
    fireEvent.click(toggle());
    expect(getState().backlogStripExpanded).toBe(true);
    expect(strip(first.container).classList.contains("is-collapsed")).toBe(false);
    first.unmount();

    const second = render(<BacklogStrip />);
    expect(strip(second.container).classList.contains("is-collapsed")).toBe(false);
    expect(screen.getByText(/README/)).toBeTruthy();

    fireEvent.click(toggle()); // 再点收起 → 旗翻回 false
    expect(getState().backlogStripExpanded).toBe(false);
    expect(strip(second.container).classList.contains("is-collapsed")).toBe(true);
  });

  it("store setter 打开（useSubmit 暂缓落地 / debt 超时走这条）→ 条随之展开；每次启动（resetStoreForTests）出厂展开", () => {
    const { container } = render(<BacklogStrip />);
    expect(strip(container).classList.contains("is-collapsed")).toBe(true);
    act(() => setBacklogStripExpanded(true));
    expect(strip(container).classList.contains("is-collapsed")).toBe(false);
    expect(screen.getByText(/周会纪要/)).toBeTruthy();

    resetStoreForTests();
    expect(getState().backlogStripExpanded).toBe(true);   // §78 / D80.3：每次启动出厂是展开
  });
});

// §78 把三件原本挂在提案列的通知搬了过来。它们在那一列住的是**列顶 composer 槽**（列表的兄弟节点，D42），
// 不是列表内部——这条条现在是全板最长的一列，进了滚动容器的回执 owner 滚到下面就再也看不见。
describe("BacklogStrip：通知钉在条顶（§44.6 / §21bis）", () => {
  it("并入回执与强制合并超时条都是 .backlog-strip-list 的兄弟、排在它之前（不随行滚走）", async () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<BacklogStrip />);
      await load({ fold_receipts: [receipt("f1", "R-302")] });
      act(() => markForceMerging(["R-301", "R-302"], "R-301"));
      act(() => vi.advanceTimersByTime(FORCE_MERGE_TIMEOUT_MS));

      const list = strip(container).querySelector(".backlog-strip-list")!;
      for (const selector of [".fold-receipts", "[data-notice='merge-force-timeout']"]) {
        const notice = strip(container).querySelector(selector)!;
        expect(notice).toBeTruthy();
        expect(notice.closest(".backlog-strip-list")).toBeNull();
        expect(notice.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      }
    } finally {
      act(() => dismissForceMergeTimeout());
      vi.useRealTimers();
    }
  });
});

// 「开完旗归用户」不等于「此后永不再开」：一条通知还活着时第二条到货，得能把 owner 中途收起的条再打开一次。
describe("BacklogStrip：每条通知各开一次（§44.6 / §21bis / §21）", () => {
  it("回执落地 → 开条；owner 收起后同一条不重开；第二条回执到货 → 再开一次", async () => {
    const { container } = render(<BacklogStrip />);
    expect(strip(container).classList.contains("is-collapsed")).toBe(true);

    await load({ fold_receipts: [receipt("f1", "R-302")] });
    expect(getState().backlogStripExpanded).toBe(true);
    expect(screen.getByText("Your input was merged into R-302")).toBeTruthy();

    collapse();
    expect(getState().backlogStripExpanded).toBe(false);
    await load({ fold_receipts: [receipt("f1", "R-302")] }, "2026-09-05T12:00:02Z");
    expect(getState().backlogStripExpanded).toBe(false);   // 同一条回执：开过就算数，旗归用户

    await load({ fold_receipts: [receipt("f1", "R-302"), receipt("f2", "R-301")] }, "2026-09-05T12:00:03Z");
    expect(getState().backlogStripExpanded).toBe(true);    // 新的一条 → 再开一次

    collapse();
    await load({ fold_receipts: [receipt("f1", "R-302"), receipt("f2", "R-301")] }, "2026-09-05T12:00:04Z");
    expect(getState().backlogStripExpanded).toBe(false);   // 两条都开过了，不再打扰
  });

  it("§21 合并建议卡落地 → 开条（SelectionBar 承诺的「潜在任务条顶会出现建议卡」）；判决落地再开一次", async () => {
    const analyzing = { id: "MS-1", ids: ["R-301", "R-302"], status: "analyzing", requested_at: 1 };
    const { container } = render(<BacklogStrip />);
    expect(strip(container).classList.contains("is-collapsed")).toBe(true);

    await load({ merge_suggestions: [analyzing] });
    expect(getState().backlogStripExpanded).toBe(true);

    collapse();
    await load({ merge_suggestions: [analyzing] }, "2026-09-05T12:00:02Z");
    expect(getState().backlogStripExpanded).toBe(false);   // 还是那张分析中的卡，不重复打扰

    // analyzing → done：那一刻卡上才长出「接受 / 取消」两颗键，是新的一件事
    await load({ merge_suggestions: [{ ...analyzing, status: "done", verdict: "merge", primary: "R-301" }] }, "2026-09-05T12:00:03Z");
    expect(getState().backlogStripExpanded).toBe(true);
  });

  it("§21bis 超时条落地 → 开条——即使此刻已经有一条没看过的回执活着（两条通知各算各的）", async () => {
    const { container } = render(<BacklogStrip />);
    await load({ fold_receipts: [receipt("f1", "R-302")] });   // 第一条通知：回执
    expect(getState().backlogStripExpanded).toBe(true);
    collapse();
    expect(getState().backlogStripExpanded).toBe(false);

    vi.useFakeTimers();
    try {
      // 回执还活着（一个合并出来的 bool 会一直是 true，永远不再触发），超时条是**第二条**通知
      act(() => markForceMerging(["R-301", "R-302"], "R-301"));
      act(() => vi.advanceTimersByTime(FORCE_MERGE_TIMEOUT_MS));
      expect(getState().backlogStripExpanded).toBe(true);
      expect(strip(container).querySelector("[data-notice='merge-force-timeout']")).toBeTruthy();
    } finally {
      act(() => dismissForceMergeTimeout());
      vi.useRealTimers();
    }
  });
});
