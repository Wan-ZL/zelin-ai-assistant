// useSubmit 的书立条强制展开（§54.1 追记 `strips-force-open`；原生 Store.swift addEcho :861 / beginReturn :851 /
// sweepTimeouts :425 :450 :539）：暂缓提交成功 → 潜在任务条开；放回看板提交成功 → 永久性完成条开；POST 被拒不开；
// 从潜在任务条发出的动作 180 s 超时 → 潜在任务条开；放回看板超时 → 永久性完成条开；提案列的动作超时不碰任何条。
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, postAction } from "../../api";
import { getState, refreshBoard, resetStoreForTests, setArchiveStripExpanded } from "../../store";
import type { Board } from "../../types";
import { CONFIRM_TIMEOUT_MS, stripToForceOpen, useSubmit } from "./boardActions";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchBoard: vi.fn(), fetchCard: vi.fn(), postAction: vi.fn() };
});

const board = {
  generated_at: "2026-09-05T10:00:00Z",
  counts: {},
  needs_approval: [{ id: "P-1", title: "P-1 title", summary: "摘要", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] }],
  running: [], needs_input: [], review: [], completed: [],
  debt: [{ id: "R-301", title: "README 安装一节过时", type: "engineering", sources: [] }],
  trash: [],
  archived: [{ id: "R-701", title: "旧的 onboarding 文档", kind: "suggestion", archived_at: "2026-09-01T00:00:00Z", archive_reason: "user", prev_status: "delivered" }],
} as unknown as Board;

describe("useSubmit：书立条强制展开", () => {
  beforeEach(async () => {
    resetStoreForTests();
    vi.mocked(fetchBoard).mockReset().mockResolvedValue(board);
    vi.mocked(postAction).mockReset().mockResolvedValue({ ok: true });
    vi.useFakeTimers();
    await act(async () => {
      await refreshBoard();
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("暂缓（defer）POST 成功 → backlogStripExpanded=true（原生 addEcho target .debt）；旗在卡组件卸载后仍在", async () => {
    const { result, unmount } = renderHook(() => useSubmit());
    expect(getState().backlogStripExpanded).toBe(false);
    await act(async () => {
      await result.current.submit({ action: "defer", id: "P-1", comment: null });
    });
    expect(getState().backlogStripExpanded).toBe(true);
    expect(getState().archiveStripExpanded).toBe(false);
    unmount(); // 卡随落地离开提案列、组件卸载——旗是 store 的，不跟组件走
    expect(getState().backlogStripExpanded).toBe(true);
  });

  it("暂缓 POST 被拒 → 不开（没有回执要落进条里）", async () => {
    vi.mocked(postAction).mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useSubmit());
    await act(async () => {
      await result.current.submit({ action: "defer", id: "P-1", comment: null });
    });
    expect(result.current.error).toBe("boom");
    expect(getState().backlogStripExpanded).toBe(false);
  });

  it("批准 / 永久完成（archive）成功 → 不碰任何条（原生只对 target .debt 开左条，archive 的 echo 不开右条）", async () => {
    const { result } = renderHook(() => useSubmit());
    await act(async () => {
      await result.current.submit({ action: "approve", id: "P-1", comment: null });
    });
    await act(async () => {
      await result.current.submit({ action: "archive", id: "R-301", comment: null });
    });
    expect(getState().backlogStripExpanded).toBe(false);
    expect(getState().archiveStripExpanded).toBe(false);
  });

  it("放回看板（unarchive）POST 成功 → archiveStripExpanded=true（原生 beginReturn source .archived）", async () => {
    const { result } = renderHook(() => useSubmit());
    await act(async () => {
      await result.current.submit({ action: "unarchive", id: "R-701", comment: null });
    });
    expect(getState().archiveStripExpanded).toBe(true);
    expect(getState().backlogStripExpanded).toBe(false);
  });

  it("从潜在任务条发出的动作（研究并提议）180 s 超时 → 潜在任务条开（原生 :425 / :450 `e.source == .debt`）", async () => {
    const { result } = renderHook(() => useSubmit());
    await act(async () => {
      await result.current.submit({ action: "raise", id: "R-301", comment: null });
    });
    expect(getState().backlogStripExpanded).toBe(false); // 提交本身不开（raise 的 echo 落提案列）
    act(() => {
      vi.advanceTimersByTime(CONFIRM_TIMEOUT_MS);
    });
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toMatch(/README/); // 原生 raise 超时句点名卡
    expect(getState().backlogStripExpanded).toBe(true);
  });

  it("放回看板 180 s 超时 → 永久性完成条开（原生 :539 `entry.source == .archived`），即便用户中途收起了它", async () => {
    const { result } = renderHook(() => useSubmit());
    await act(async () => {
      await result.current.submit({ action: "unarchive", id: "R-701", comment: null });
    });
    act(() => setArchiveStripExpanded(false)); // 用户收起
    act(() => {
      vi.advanceTimersByTime(CONFIRM_TIMEOUT_MS);
    });
    expect(result.current.error).toMatch(/Put back timed out/);
    expect(getState().archiveStripExpanded).toBe(true);
  });

  it("提案列的动作（批准 / 暂缓）180 s 超时 → 不碰任何条（卡还在提案列，通知也落在那里）", async () => {
    const { result } = renderHook(() => useSubmit());
    await act(async () => {
      await result.current.submit({ action: "approve", id: "P-1", comment: null });
    });
    act(() => {
      vi.advanceTimersByTime(CONFIRM_TIMEOUT_MS);
    });
    expect(result.current.pending).toBe(false);
    expect(getState().backlogStripExpanded).toBe(false);
    expect(getState().archiveStripExpanded).toBe(false);
  });
});

describe("stripToForceOpen（纯函数）", () => {
  it("submitted：defer → backlog、unarchive → archive、其余 null", () => {
    expect(stripToForceOpen({ action: "defer", sourceLane: "needs_approval" }, "submitted")).toBe("backlog");
    expect(stripToForceOpen({ action: "unarchive", sourceLane: "archived" }, "submitted")).toBe("archive");
    expect(stripToForceOpen({ action: "archive", sourceLane: "completed" }, "submitted")).toBeNull();
    expect(stripToForceOpen({ action: "raise", sourceLane: "debt" }, "submitted")).toBeNull();
    expect(stripToForceOpen({ action: null, sourceLane: null }, "submitted")).toBeNull();
  });

  it("timeout：看提交时所在的列——debt → backlog、archived → archive、其余 null", () => {
    expect(stripToForceOpen({ action: "raise", sourceLane: "debt" }, "timeout")).toBe("backlog");
    expect(stripToForceOpen({ action: "trash", sourceLane: "debt" }, "timeout")).toBe("backlog");
    expect(stripToForceOpen({ action: "unarchive", sourceLane: "archived" }, "timeout")).toBe("archive");
    expect(stripToForceOpen({ action: "defer", sourceLane: "needs_approval" }, "timeout")).toBeNull();
    expect(stripToForceOpen({ action: "approve", sourceLane: null }, "timeout")).toBeNull();
  });
});
