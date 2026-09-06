// 潜在任务书立条的展开态挂 store、搜索命中强制展开（§54.1 追记 `strips-force-open`；原生 Kanban.swift:316-328 +
// Store.swift:127-128）：收起 + 搜索命中 → 列表可见（旗不动，清掉查询即收回）；无命中不强开；强制展开期间列头是 no-op；
// 旗在会话内跨卸载 / 重挂（换页）留存；store setter 直接打开（useSubmit 的强制展开走它）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { getState, refreshBoard, resetStoreForTests, setBacklogStripExpanded, setFilters } from "../../store";
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

beforeEach(async () => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
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

  it("store setter 打开（useSubmit 暂缓落地 / debt 超时走这条）→ 条随之展开；每次启动（resetStoreForTests）都收起", () => {
    const { container } = render(<BacklogStrip />);
    expect(strip(container).classList.contains("is-collapsed")).toBe(true);
    act(() => setBacklogStripExpanded(true));
    expect(strip(container).classList.contains("is-collapsed")).toBe(false);
    expect(screen.getByText(/周会纪要/)).toBeTruthy();

    resetStoreForTests();
    expect(getState().backlogStripExpanded).toBe(false);
  });
});
