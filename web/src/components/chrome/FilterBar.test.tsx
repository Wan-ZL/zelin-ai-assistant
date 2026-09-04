// FilterBar 行为：chip 只剩 Tier / 期限 / 回锅 + 搜索（D28：类型 / 渠道退役）、chip 多选写进 store + URL（?tier=）、
// ⌘F 聚焦搜索、清除按钮复位、⎋ 两段（清词 → 退出多选，原生 契约七）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { getState, refreshBoard, resetStoreForTests, setFilters, setSelectionMode } from "../../store";
import { fetchBoard } from "../../api";
import type { Board } from "../../types";
import { FilterBar } from "./FilterBar";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn() };
});

// 带 type 与 sources[].channel 的看板——退役前会让「类型」「渠道」两颗 chip 长出来
const boardWithTypesAndChannels = {
  generated_at: "2026-09-04T00:00:00Z",
  counts: {},
  needs_approval: [{ id: "P-1", title: "t", tier: "T1", processing: false, show_cost: false,
    sources: [{ who: "a", channel: "slack", date: "d", quote: "q" }], plan: [], dod: [] }],
  running: [], needs_input: [], review: [], completed: [],
  debt: [{ id: "P-3", title: "t", type: "process",
    sources: [{ who: "c", channel: "meeting", date: "d", quote: "q" }] }],
  trash: [],
} as unknown as Board;

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
});

afterEach(cleanup);

describe("FilterBar", () => {
  it("D28：chip 恰好三颗 Tier / 期限 / 回锅 + 搜索框——看板带 type / channel 也不长出类型、渠道 chip", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(boardWithTypesAndChannels);
    await act(async () => { await refreshBoard(); });
    render(<FilterBar />);

    expect(screen.getByRole("searchbox", { name: "Search cards" })).toBeTruthy();
    const chips = Array.from(document.querySelectorAll<HTMLElement>(".chrome-chip"));
    expect(chips.map((c) => c.textContent?.trim())).toEqual(["Tier", "Deadline", "↩︎ Re-raised"]);
    expect(screen.getByRole("button", { name: "Filter by tier" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Filter by deadline" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Filter by type/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Filter by channel/ })).toBeNull();
  });

  it("tier chip 多选：toggle 进 store 并序列化进 URL；再点取消", () => {
    render(<FilterBar />);

    fireEvent.click(screen.getByRole("button", { name: "Filter by tier" }));
    fireEvent.click(screen.getByRole("option", { name: /T2/ }));

    expect(getState().filters.tiers).toEqual(["T2"]);
    expect(new URLSearchParams(window.location.search).get("tier")).toBe("T2");
    // 多选弹层保持打开，二次点击取消勾选
    fireEvent.click(screen.getByRole("option", { name: /T2/ }));
    expect(getState().filters.tiers).toEqual([]);
    expect(new URLSearchParams(window.location.search).get("tier")).toBeNull();
  });

  it("挂载时从 URL 水合深链过滤器；旧书签的 type= / channel= 被忽略、首次写回即丢弃", () => {
    window.history.replaceState(null, "", "/?tier=T1&q=readme&reraised=1&type=engineering&channel=slack");
    render(<FilterBar />);
    const { filters } = getState();
    expect(filters).toEqual({ tiers: ["T1"], deadline: "all", reraisedOnly: true, search: "readme" });
    expect(screen.getByRole("button", { name: "Filter by tier" }).textContent).toContain("T1");

    fireEvent.click(screen.getByRole("button", { name: "Filter by tier" }));
    fireEvent.click(screen.getByRole("option", { name: /T2/ }));
    const params = new URLSearchParams(window.location.search);
    expect(params.get("tier")).toBe("T1,T2");
    expect(params.has("type")).toBe(false);
    expect(params.has("channel")).toBe(false);
  });

  it("⎋ 两段：有搜索词先清词、再按退出多选（原生 Kanban.swift:98 契约七）", () => {
    render(<FilterBar />);
    act(() => { setFilters({ search: "readme" }); setSelectionMode(true); });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(getState().filters.search).toBe("");
    expect(getState().selectionMode).toBe(true);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(getState().selectionMode).toBe(false);
  });

  it("⌘F 聚焦搜索框；输入写 store + URL q=", () => {
    render(<FilterBar />);
    const input = screen.getByRole("searchbox", { name: "Search cards" });

    fireEvent.keyDown(window, { key: "f", metaKey: true });
    expect(document.activeElement).toBe(input);

    fireEvent.change(input, { target: { value: "lint" } });
    expect(getState().filters.search).toBe("lint");
    expect(new URLSearchParams(window.location.search).get("q")).toBe("lint");
  });

  it("清除按钮复位全部维度并清 URL", () => {
    window.history.replaceState(null, "", "/?tier=T1&deadline=soon");
    render(<FilterBar />);
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /Clear \(2\)/ }));
    });
    expect(getState().filters.tiers).toEqual([]);
    expect(getState().filters.deadline).toBe("all");
    expect(window.location.search).toBe("");
  });
});
