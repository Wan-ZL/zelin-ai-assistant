// 永久性完成书立条的展开态挂 store（§54.1 追记 `strips-force-open`；原生 Store.swift:128 archiveStripExpanded）：
// 点列头写旗、卸载再挂（换页）仍展开；store setter 打开（useSubmit 放回看板成功 / 超时走它）。
// 右条没有搜索强开（原生 Kanban.swift:516 直接绑 $store.archiveStripExpanded）——全局 ⌘F 不碰它。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { fetchBoard, postAction } from "../../api";
import { getState, refreshBoard, resetStoreForTests, setArchiveStripExpanded, setFilters } from "../../store";
import type { Board } from "../../types";
import { ArchiveStrip } from "./ArchiveStrip";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchCard: vi.fn(), postAction: vi.fn().mockResolvedValue({ ok: true }) };
});

const board = {
  generated_at: "2026-09-05T12:00:00Z",
  counts: { archived: 1 },
  needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [],
  archived: [
    { id: "R-701", title: "旧的 onboarding 文档", summary: "已彻底结束", kind: "suggestion",
      archived_at: "2026-09-01T00:00:00Z", archive_reason: "user", prev_status: "delivered" },
  ],
} as unknown as Board;

beforeEach(async () => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.mocked(fetchBoard).mockResolvedValue(board);
  vi.mocked(postAction).mockClear();
  await refreshBoard();
});

afterEach(cleanup);

const strip = (c: HTMLElement) => c.querySelector(".backlog-strip.is-archive")!;
const toggle = () => screen.getByRole("button", { name: /Done for good/ });

describe("ArchiveStrip：展开态挂 store", () => {
  it("点列头展开写旗；卸载再挂仍展开；再点收起旗翻回 false", () => {
    const first = render(<ArchiveStrip />);
    expect(strip(first.container).classList.contains("is-collapsed")).toBe(true);
    fireEvent.click(toggle());
    expect(getState().archiveStripExpanded).toBe(true);
    expect(screen.getByText("已彻底结束")).toBeTruthy();
    first.unmount();

    const second = render(<ArchiveStrip />);
    expect(strip(second.container).classList.contains("is-collapsed")).toBe(false);
    expect(screen.getByText("已彻底结束")).toBeTruthy();

    fireEvent.click(toggle());
    expect(getState().archiveStripExpanded).toBe(false);
    expect(strip(second.container).classList.contains("is-collapsed")).toBe(true);
  });

  it("store setter 打开 → 条随之展开、行与「放回看板」可见", () => {
    const { container } = render(<ArchiveStrip />);
    act(() => setArchiveStripExpanded(true));
    expect(strip(container).classList.contains("is-collapsed")).toBe(false);
    expect(screen.getByRole("button", { name: "Put back" })).toBeTruthy();
  });

  it("全局 ⌘F 不强开右条（归档不是看板列，不吃全局过滤）", () => {
    const { container } = render(<ArchiveStrip />);
    act(() => setFilters({ search: "onboarding" }));
    expect(strip(container).classList.contains("is-collapsed")).toBe(true);
  });
});
