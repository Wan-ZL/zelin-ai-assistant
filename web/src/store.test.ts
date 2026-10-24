// store 行为测试：订阅/更新通知、refreshRecaps 并发合并 + 形状门 + 错误面、recap 乐观本地态。
// 经 vi.mock 替换 fetchRecaps / postRecapMark（保留真 ApiError 供 instanceof 分类），零真实网络。
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchRecaps, postRecapMark } from "./api";
import {
  clearRecapPending,
  getState,
  markRecap,
  markRecapPending,
  refreshRecaps,
  resetStoreForTests,
  setConnection,
  subscribe,
  useAppState,
} from "./store";
import type { RecapRow, RecapsSnapshot } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchRecaps: vi.fn(), postRecapMark: vi.fn() };
});

const ROW: RecapRow = {
  key: "meeting:2026-10-20T1400-zoom",
  app: "zoom",
  start: "2026-10-20T14:00:00Z",
  end: "2026-10-20T14:42:00Z",
  duration_min: 42,
  status: "closed",
  version: 2,
  generate_request: { requested_at: "2026-10-20T15:00:00Z", state: "done", note: null },
};

const SNAPSHOT: RecapsSnapshot = { recaps: [ROW], recap_counts: { active: 1, archived: 0, dismissed: 0 } };

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

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchRecaps).mockReset();
  vi.mocked(postRecapMark).mockReset();
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

describe("refreshRecaps", () => {
  it("stores the rows and lane totals, clears the error, drops the loading flag", async () => {
    vi.mocked(fetchRecaps).mockResolvedValue(SNAPSHOT);
    expect(getState().recapsLoading).toBe(true);
    await refreshRecaps();
    const state = getState();
    expect(state.recaps).toEqual([ROW]);
    expect(state.recapCounts).toEqual({ active: 1, archived: 0, dismissed: 0 });
    expect(state.recapsError).toBeNull();
    expect(state.recapsLoading).toBe(false);
  });

  it("surfaces the ApiError message on failure and keeps the last good rows, then clears it on recovery", async () => {
    vi.mocked(fetchRecaps).mockResolvedValueOnce(SNAPSHOT);
    await refreshRecaps();
    vi.mocked(fetchRecaps).mockRejectedValueOnce(
      new ApiError(0, { error: { code: "READ_FAILED", message: "recaps unreachable" } }),
    );
    await refreshRecaps();
    expect(getState().recapsError).toBe("recaps unreachable");
    expect(getState().recaps).toEqual([ROW]); // 旧行留着（它可能仍在说真话）

    vi.mocked(fetchRecaps).mockResolvedValueOnce({ recaps: [] });
    await refreshRecaps();
    expect(getState().recapsError).toBeNull();
    expect(getState().recaps).toEqual([]);
    expect(getState().recapCounts).toBeNull(); // 老 server 没有 recap_counts = null，页面不说「另有 N 条」
  });

  it("a 2xx body without a recaps list is a read failure, not an empty page", async () => {
    vi.mocked(fetchRecaps).mockResolvedValueOnce({} as RecapsSnapshot);
    await refreshRecaps();
    expect(getState().recaps).toBeNull();
    expect(getState().recapsError).toMatch(/recaps is not a list/);
  });

  it("collapses concurrent calls into a single in-flight request", async () => {
    const gate = deferred<RecapsSnapshot>();
    vi.mocked(fetchRecaps).mockReturnValue(gate.promise);

    const first = refreshRecaps();
    const second = refreshRecaps();
    expect(second).toBe(first); // 同一个在途 promise
    expect(fetchRecaps).toHaveBeenCalledTimes(1);

    gate.resolve(SNAPSHOT);
    await first;

    // 在途台账清空后，下一次调用重新发请求
    vi.mocked(fetchRecaps).mockResolvedValue(SNAPSHOT);
    await refreshRecaps();
    expect(fetchRecaps).toHaveBeenCalledTimes(2);
  });
});

describe("recap local state (§63.5 marks / §63.8 pending)", () => {
  it("markRecap keeps the server receipt as the optimistic local mark", async () => {
    vi.mocked(postRecapMark).mockResolvedValue({ ok: true, key: ROW.key, copied_at: null, sent_at: "2026-10-20T16:00:00Z", dismissed_at: null });
    await markRecap(ROW.key, "sent", true);
    expect(postRecapMark).toHaveBeenCalledWith(ROW.key, "sent", true);
    expect(getState().recapMarks[ROW.key]).toEqual({ copied_at: null, sent_at: "2026-10-20T16:00:00Z", dismissed_at: null });
  });

  it("markRecapPending records the version and receipt seen at the press; clearRecapPending drops only that key", () => {
    markRecapPending(ROW, 1_000);
    markRecapPending({ ...ROW, key: "meeting:2026-10-21T0900-meet", version: 0, generate_request: null }, 2_000);
    expect(getState().recapPending[ROW.key]).toEqual({ version: 2, requested_at: "2026-10-20T15:00:00Z", at: 1_000 });
    clearRecapPending(ROW.key);
    expect(Object.keys(getState().recapPending)).toEqual(["meeting:2026-10-21T0900-meet"]);
    const before = getState();
    clearRecapPending("meeting:2000-01-01T0000-none"); // 不在表里 = 零动作（不 emit 新对象）
    expect(getState()).toBe(before);
  });
});
