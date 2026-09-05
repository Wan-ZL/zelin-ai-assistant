// store 行为测试：订阅/更新通知、refreshBoard 并发合并、selectCard 陈旧守卫、错误面。
// 经 vi.mock 替换 fetchBoard/fetchCard（保留真 ApiError 供 instanceof 分类），零真实网络。
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard, fetchCard } from "./api";
import {
  getState,
  refreshBoard,
  resetStoreForTests,
  selectCard,
  setConnection,
  subscribe,
  useAppState,
} from "./store";
import type { Board, CardDetail } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchCard: vi.fn() };
});

const BOARD: Board = {
  generated_at: "2026-08-30T12:00:00Z",
  counts: { needs_approval: 0 },
  needs_approval: [],
  running: [],
  needs_input: [],
  review: [],
  completed: [],
  debt: [],
  trash: [],
};

/** 手动控制 resolve/reject 的挂起 promise（模拟在途请求） */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** 冲掉一轮微任务（等 store 内部 .then 落地） */
const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchBoard).mockReset();
  vi.mocked(fetchCard).mockReset();
});

describe("subscribe / update", () => {
  it("notifies subscribers on each state change and stops after unsubscribe", async () => {
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);

    setConnection("live");
    expect(listener).toHaveBeenCalledTimes(1);
    expect(getState().connection).toBe("live");

    unsubscribe();
    setConnection("reconnecting");
    expect(listener).toHaveBeenCalledTimes(1); // 退订后不再收到
    expect(getState().connection).toBe("reconnecting"); // 但 state 照常更新
  });

  it("setConnection is a no-op (no emit) when the state is unchanged", () => {
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);
    setConnection("connecting"); // 初始值即 connecting
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("useAppState re-renders components with the fresh snapshot", () => {
    const { result, unmount } = renderHook(() => useAppState());
    expect(result.current.connection).toBe("connecting");
    act(() => setConnection("live"));
    expect(result.current.connection).toBe("live");
    unmount();
  });
});

describe("refreshBoard", () => {
  it("stores the board, clears error, and drops the loading flag", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    expect(getState().boardLoading).toBe(true);
    await refreshBoard();
    const state = getState();
    expect(state.board).toEqual(BOARD);
    expect(state.boardError).toBeNull();
    expect(state.boardLoading).toBe(false);
  });

  it("surfaces the ApiError message on failure, then clears it on recovery", async () => {
    vi.mocked(fetchBoard).mockRejectedValueOnce(
      new ApiError(0, { error: { code: "READ_FAILED", message: "board unreachable" } }),
    );
    await refreshBoard();
    expect(getState().boardError).toBe("board unreachable");
    expect(getState().boardLoading).toBe(false);

    vi.mocked(fetchBoard).mockResolvedValueOnce(BOARD);
    await refreshBoard();
    expect(getState().boardError).toBeNull();
    expect(getState().board).toEqual(BOARD);
  });

  it("collapses concurrent calls into a single in-flight request", async () => {
    const gate = deferred<Board>();
    vi.mocked(fetchBoard).mockReturnValue(gate.promise);

    const first = refreshBoard();
    const second = refreshBoard();
    expect(second).toBe(first); // 同一个在途 promise
    expect(fetchBoard).toHaveBeenCalledTimes(1);

    gate.resolve(BOARD);
    await first;

    // 在途台账清空后，下一次调用重新发请求
    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    await refreshBoard();
    expect(fetchBoard).toHaveBeenCalledTimes(2);
  });
});

describe("selectCard", () => {
  it("sets the selection immediately and fills cardDetail when the fetch lands", async () => {
    const detail: CardDetail = { id: "R-101", plan: ["step"] };
    vi.mocked(fetchCard).mockResolvedValue(detail);

    selectCard("R-101");
    expect(getState().selectedCardId).toBe("R-101");
    expect(getState().cardDetail).toBeNull(); // 详情未落地前为空

    await flush();
    expect(getState().cardDetail).toEqual(detail);
  });

  it("drops a stale response when the selection has moved on", async () => {
    const slow = deferred<CardDetail>();
    const fast = deferred<CardDetail>();
    vi.mocked(fetchCard)
      .mockReturnValueOnce(slow.promise)
      .mockReturnValueOnce(fast.promise);

    selectCard("R-101");
    selectCard("R-202"); // 换卡：R-101 的响应已过期

    fast.resolve({ id: "R-202" });
    await flush();
    slow.resolve({ id: "R-101" }); // 迟到的旧卡详情
    await flush();

    expect(getState().selectedCardId).toBe("R-202");
    expect(getState().cardDetail).toEqual({ id: "R-202" });
  });

  it("remembers every card whose sidebar was opened this session (detailViewedIds — the T2 gate reads it)", async () => {
    vi.mocked(fetchCard).mockResolvedValue({ id: "R-101" });
    expect(getState().detailViewedIds.has("R-101")).toBe(false);
    selectCard("R-101");
    expect(getState().detailViewedIds.has("R-101")).toBe(true);
    selectCard(null); // 关侧栏不忘记：看过明细就是看过
    expect(getState().detailViewedIds.has("R-101")).toBe(true);
    selectCard("R-202");
    expect([...getState().detailViewedIds].sort()).toEqual(["R-101", "R-202"]);
    await Promise.resolve();
  });

  it("selectCard(null) closes the drawer without fetching", () => {
    selectCard(null);
    expect(getState().selectedCardId).toBeNull();
    expect(fetchCard).not.toHaveBeenCalled();
  });

  it("keeps the error scoped to the still-selected card only", async () => {
    const failing = deferred<CardDetail>();
    vi.mocked(fetchCard).mockReturnValueOnce(failing.promise);
    selectCard("R-101");
    failing.reject(new ApiError(404, { error: { code: "NOT_FOUND", message: "gone" } }));
    await flush();
    expect(getState().cardDetailError).toBe("gone");

    // 换卡后，旧卡的失败不得污染新选择
    const late = deferred<CardDetail>();
    vi.mocked(fetchCard)
      .mockReturnValueOnce(late.promise)
      .mockResolvedValueOnce({ id: "R-303" });
    selectCard("R-202");
    expect(getState().cardDetailError).toBeNull(); // 选择时清空旧错误
    selectCard("R-303");
    late.reject(new ApiError(404, { error: { code: "NOT_FOUND", message: "stale" } }));
    await flush();
    expect(getState().cardDetailError).toBeNull();
  });
});
