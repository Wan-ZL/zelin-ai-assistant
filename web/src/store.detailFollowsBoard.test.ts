// 详情侧栏跟随看板换版（CONTRACT §49 追记 `store-resilience-drawer`；原生 Store.swift:56-57 @Published dashboard——每次
// reload 一发布，展开区就从新快照重渲染）：此前 selectCard 一次性拉 /api/cards/{id}，refreshBoard 从不碰 cardDetail，侧栏
// 开着时抬头 / 列积木 / 改名框预填全部冻结在打开那一刻。自此 refreshBoard 在 **generated_at 变了 + 有选中卡** 时静默重拉，
// 成功且用户还停在这张卡才整份替换 cardDetail（不清旧详情、不闪「加载详情…」）；同版重拉不拉、首版落地不拉、
// 换卡后迟到的响应丢、失败留旧详情。经 vi.mock 替换 fetchBoard / fetchCard，零真实网络。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard, fetchCard } from "./api";
import { getState, refreshBoard, resetStoreForTests, selectCard } from "./store";
import type { Board, CardDetail } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchCard: vi.fn() };
});

function board(generated_at: string, over: Partial<Board> = {}): Board {
  return {
    generated_at, counts: {}, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [],
    ...over,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchBoard).mockReset();
  vi.mocked(fetchCard).mockReset();
});

async function openWithBoard(cardId = "R-101", detail: CardDetail = { id: cardId, title: "old", lane: "needs_approval" }) {
  vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:00:00Z"));
  await refreshBoard();
  vi.mocked(fetchCard).mockResolvedValueOnce(detail);
  selectCard(cardId);
  await flush();
  expect(getState().cardDetail).toEqual(detail);
  expect(fetchCard).toHaveBeenCalledTimes(1);
}

describe("refreshBoard · detail follows the board", () => {
  it("generated_at changed with a card selected → fetchCard once more, cardDetail replaced on success", async () => {
    await openWithBoard();
    const fresh: CardDetail = { id: "R-101", title: "old", display_title: "renamed", lane: "running" };
    vi.mocked(fetchCard).mockResolvedValueOnce(fresh);
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:01:00Z"));
    await refreshBoard();
    expect(fetchCard).toHaveBeenCalledTimes(2);
    expect(fetchCard).toHaveBeenLastCalledWith("R-101");
    await flush();
    expect(getState().cardDetail).toEqual(fresh);
    expect(getState().cardDetailError).toBeNull();
  });

  it("exactly once per generated_at change: a same-version refetch (reconnect) does not re-fetch the card", async () => {
    await openWithBoard();
    await refreshBoard(); // 同版重拉
    await refreshBoard();
    expect(fetchCard).toHaveBeenCalledTimes(1);
    vi.mocked(fetchCard).mockResolvedValueOnce({ id: "R-101" });
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:02:00Z"));
    await refreshBoard();
    await refreshBoard(); // 新版之后再同版重拉
    expect(fetchCard).toHaveBeenCalledTimes(2);
  });

  it("the old detail stays on screen while the follow-up is in flight (no null flicker, no spinner)", async () => {
    await openWithBoard();
    const slow = deferred<CardDetail>();
    vi.mocked(fetchCard).mockReturnValueOnce(slow.promise);
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:01:00Z"));
    await refreshBoard();
    expect(getState().cardDetail).toEqual({ id: "R-101", title: "old", lane: "needs_approval" }); // 旧详情仍在
    slow.resolve({ id: "R-101", title: "old", lane: "review" });
    await flush();
    expect(getState().cardDetail?.lane).toBe("review");
  });

  it("the response is ignored when the selection changed meanwhile (closed or moved to another card)", async () => {
    await openWithBoard();
    const slow = deferred<CardDetail>();
    vi.mocked(fetchCard).mockReturnValueOnce(slow.promise);
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:01:00Z"));
    await refreshBoard();

    vi.mocked(fetchCard).mockResolvedValueOnce({ id: "R-202", title: "other" });
    selectCard("R-202");
    await flush();
    slow.resolve({ id: "R-101", title: "late" }); // 迟到的旧卡跟随响应
    await flush();
    expect(getState().selectedCardId).toBe("R-202");
    expect(getState().cardDetail).toEqual({ id: "R-202", title: "other" });

    // 关侧栏后到达的响应也丢
    const slow2 = deferred<CardDetail>();
    vi.mocked(fetchCard).mockReturnValueOnce(slow2.promise);
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:02:00Z"));
    await refreshBoard();
    selectCard(null);
    slow2.resolve({ id: "R-202", title: "late again" });
    await flush();
    expect(getState().selectedCardId).toBeNull();
    expect(getState().cardDetail).toBeNull();
  });

  it("two versions landing back-to-back: only the latest follow-up response is kept even if it arrives first", async () => {
    await openWithBoard();
    const first = deferred<CardDetail>();
    const second = deferred<CardDetail>();
    vi.mocked(fetchCard).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:01:00Z"));
    await refreshBoard();
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:02:00Z"));
    await refreshBoard();
    expect(fetchCard).toHaveBeenCalledTimes(3);
    second.resolve({ id: "R-101", title: "v2" });
    await flush();
    first.resolve({ id: "R-101", title: "v1" }); // 旧版乱序迟到
    await flush();
    expect(getState().cardDetail?.title).toBe("v2");
  });

  it("a failed follow-up keeps the old detail and does not raise cardDetailError", async () => {
    await openWithBoard();
    vi.mocked(fetchCard).mockRejectedValueOnce(new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "boom" } }));
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:01:00Z"));
    await refreshBoard();
    await flush();
    expect(getState().cardDetail).toEqual({ id: "R-101", title: "old", lane: "needs_approval" });
    expect(getState().cardDetailError).toBeNull();
  });

  it("no selection → board versions never touch fetchCard", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:00:00Z"));
    await refreshBoard();
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:01:00Z"));
    await refreshBoard();
    expect(fetchCard).not.toHaveBeenCalled();
  });

  it("the first board landing does not double the selectCard fetch (deep link opened before the board arrived)", async () => {
    vi.mocked(fetchCard).mockResolvedValueOnce({ id: "R-101" });
    selectCard("R-101"); // ?card= 深链：侧栏先开
    await flush();
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:00:00Z"));
    await refreshBoard();
    expect(fetchCard).toHaveBeenCalledTimes(1);
  });

  it("a first fetch that failed is healed by a successful follow-up (error cleared, card counts as viewed)", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:00:00Z"));
    await refreshBoard();
    vi.mocked(fetchCard).mockRejectedValueOnce(new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "boom" } }));
    selectCard("R-101");
    await flush();
    expect(getState().cardDetailError).toBe("boom");
    expect(getState().detailViewedIds.has("R-101")).toBe(false);

    vi.mocked(fetchCard).mockResolvedValueOnce({ id: "R-101", title: "now" });
    vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-05T10:01:00Z"));
    await refreshBoard();
    await flush();
    expect(getState().cardDetail).toEqual({ id: "R-101", title: "now" });
    expect(getState().cardDetailError).toBeNull();
    expect(getState().detailViewedIds.has("R-101")).toBe(true);
  });
});
