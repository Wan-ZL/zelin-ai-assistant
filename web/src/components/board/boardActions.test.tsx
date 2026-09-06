// useSubmit 状态机测试：180s truth-timeout（镜像 Mac Store.swift 的 180s fallback）——
// 已提交但看板始终无回流 → 解锁 + 诚实报「backend 未确认」；回流按时到达则绝不误报。
// 这里的卡提交时不在看板上（无 sourceLane）→ 退回「新快照落地」判据；逐动词的真信号判例见 pendingSettle.test.ts /
// useSubmit.settle.test.tsx。
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, postAction } from "../../api";
import { refreshBoard, resetStoreForTests } from "../../store";
import type { Board } from "../../types";
import { CONFIRM_TIMEOUT_MS, useSubmit } from "./boardActions";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchBoard: vi.fn(), fetchCard: vi.fn(), postAction: vi.fn() };
});

const fetchBoardMock = vi.mocked(fetchBoard);
const postActionMock = vi.mocked(postAction);

function makeBoard(generatedAt: string): Board {
  return {
    generated_at: generatedAt,
    counts: {},
    needs_approval: [],
    running: [],
    needs_input: [],
    review: [],
    completed: [],
    debt: [],
    trash: [],
  };
}

describe("useSubmit", () => {
  beforeEach(() => {
    resetStoreForTests();
    fetchBoardMock.mockReset();
    postActionMock.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("180s 无回流：解锁并报 backend 未确认（不装成功）", async () => {
    postActionMock.mockResolvedValue({ ok: true });
    const { result } = renderHook(() => useSubmit());

    await act(async () => {
      await result.current.submit({ action: "approve", id: "R-101" });
    });
    expect(result.current.pending).toBe(true);
    expect(result.current.error).toBeNull();

    act(() => {
      vi.advanceTimersByTime(CONFIRM_TIMEOUT_MS - 1);
    });
    expect(result.current.pending).toBe(true); // 差 1ms 仍在等回流

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current.pending).toBe(false);
    // 卡不在看板上（提交时就没见过）→ 原生「已提交但后台超时未确认」那句（Store.swift:444）
    expect(result.current.error).toMatch(/"R-101" was submitted but the backend never confirmed.*actd/);
  });

  it("回流在 180s 内到达：正常解锁，超时绝不追加误报", async () => {
    postActionMock.mockResolvedValue({ ok: true });
    const { result } = renderHook(() => useSubmit());

    await act(async () => {
      await result.current.submit({ action: "approve", id: "R-101" });
    });
    expect(result.current.pending).toBe(true);

    // SSE 回流落地：generated_at 变化 → 解锁（回流是唯一成功回执）
    fetchBoardMock.mockResolvedValue(makeBoard("2030-01-01T00:00:00Z"));
    await act(async () => {
      await refreshBoard();
    });
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toBeNull();

    act(() => {
      vi.advanceTimersByTime(CONFIRM_TIMEOUT_MS + 1);
    });
    expect(result.current.error).toBeNull(); // 定时器已随解锁清除
  });

  it("提交失败：立即解锁并给出可读错误（超时定时器不再触发）", async () => {
    postActionMock.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useSubmit());

    await act(async () => {
      await result.current.submit({ action: "approve", id: "R-101" });
    });
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toBe("boom");

    act(() => {
      vi.advanceTimersByTime(CONFIRM_TIMEOUT_MS + 1);
    });
    expect(result.current.error).toBe("boom"); // 不被超时文案覆盖
  });
});
