// §21bis 「合并中…」章的清除判据 = 一批的每张副卡都离开所有列（原生 PendingForceMerge），不是 generated_at 一变就清；
// 180 s 没落地 → 章退场 + 提案列顶的原生超时句（ForceMergeTimeoutNotice），点 × / 120 s 后消失。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "./api";
import { ForceMergeTimeoutNotice, NOTICE_FADE_MS } from "./components/board/ForceMergeTimeoutNotice";
import { LanguageContext } from "./i18n";
import { FORCE_MERGE_TIMEOUT_MS, getState, markForceMerging, refreshBoard, resetStoreForTests, settleForceMerging } from "./store";
import type { Board } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchBoard: vi.fn() };
});

function board(over: Partial<Board> = {}, generated_at = "2026-09-05T10:00:00Z"): Board {
  return {
    generated_at, counts: {}, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [],
    ...over,
  } as unknown as Board;
}
const proposal = (id: string) => ({ id, title: id, tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] }) as never;

async function load(b: Board) {
  vi.mocked(fetchBoard).mockResolvedValue(b);
  await refreshBoard();
}

beforeEach(async () => {
  resetStoreForTests();
  vi.useFakeTimers();
  await load(board({ needs_approval: [proposal("P-1"), proposal("P-2"), proposal("P-3")] }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("forceMergingIds 的结算（settleForceMerging）", () => {
  it("新一版快照、副卡还在 → 章不清；一张副卡消失 → 不清；副卡全消失（主卡留着）→ 清", async () => {
    markForceMerging(["P-1", "P-2", "P-3"], "P-1");
    expect([...getState().forceMergingIds].sort()).toEqual(["P-1", "P-2", "P-3"]);

    await load(board({ needs_approval: [proposal("P-1"), proposal("P-2"), proposal("P-3")] }, "2026-09-05T10:00:10Z"));
    expect(getState().forceMergingIds.size).toBe(3);

    await load(board({ needs_approval: [proposal("P-1"), proposal("P-3")] }, "2026-09-05T10:00:20Z"));
    expect(getState().forceMergingIds.size).toBe(3);

    await load(board({ needs_approval: [proposal("P-1")] }, "2026-09-05T10:00:30Z"));
    expect(getState().forceMergingIds.size).toBe(0);
  });

  it("两批各自结算：一批落地不带走另一批", async () => {
    markForceMerging(["P-1", "P-2"], "P-1");
    markForceMerging(["P-3", "P-4"], "P-4");
    expect(getState().forceMergingIds.size).toBe(4);
    settleForceMerging(board({ needs_approval: [proposal("P-1"), proposal("P-3"), proposal("P-4")] }));
    expect([...getState().forceMergingIds].sort()).toEqual(["P-3", "P-4"]);
  });

  it("primary 不在 ids 里 / 缺席（旧调用方）→ 第一张当主卡；空 ids 无事发生", async () => {
    markForceMerging(["P-2", "P-3"], "P-9");
    settleForceMerging(board({ needs_approval: [proposal("P-2")] })); // P-3 消失 = 副卡全消失
    expect(getState().forceMergingIds.size).toBe(0);
    markForceMerging([]);
    expect(getState().forceMergingIds.size).toBe(0);
  });

  it("同版快照重拉（同 generated_at）也跑谓词：副卡在这一版里已经不见就清", async () => {
    markForceMerging(["P-1", "P-2"], "P-1");
    await load(board({ needs_approval: [proposal("P-1"), proposal("P-3")] })); // generated_at 未变
    expect(getState().forceMergingIds.size).toBe(0);
  });
});

describe("180 s 兜底", () => {
  const wrap = (language: "zh" | "en" = "zh") => render(<LanguageContext.Provider value={language}><ForceMergeTimeoutNotice /></LanguageContext.Provider>);

  it("副卡 180 s 没离开 → 章退场、超时条出现（原生 notice-merge-force 句，zh / en）；落地了的批次不会再触发", () => {
    markForceMerging(["P-1", "P-2"], "P-1");
    wrap();
    expect(screen.queryByRole("status")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(FORCE_MERGE_TIMEOUT_MS - 1);
    });
    expect(getState().forceMergingIds.size).toBe(2);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(getState().forceMergingIds.size).toBe(0);
    expect(screen.getByRole("status").textContent).toContain("强制合并未确认，卡片未变化，请重试（检查 actd 是否在运行）");
    cleanup();
    wrap("en");
    expect(screen.getByRole("status").textContent).toContain("Force-merge never confirmed — nothing changed, try again (check that actd is running)");
  });

  it("落地后定时器作废：到点不弹超时条", () => {
    markForceMerging(["P-1", "P-2"], "P-1");
    settleForceMerging(board({ needs_approval: [proposal("P-1")] }));
    act(() => {
      vi.advanceTimersByTime(FORCE_MERGE_TIMEOUT_MS + 1);
    });
    expect(getState().forceMergeTimedOutAt).toBeNull();
  });

  it("× 关掉 / 120 s 自动褪去", () => {
    markForceMerging(["P-1", "P-2"], "P-1");
    wrap();
    act(() => {
      vi.advanceTimersByTime(FORCE_MERGE_TIMEOUT_MS);
    });
    fireEvent.click(screen.getByRole("button", { name: "知道了" }));
    expect(screen.queryByRole("status")).toBeNull();
    expect(getState().forceMergeTimedOutAt).toBeNull();

    markForceMerging(["P-2", "P-3"], "P-2");
    act(() => {
      vi.advanceTimersByTime(FORCE_MERGE_TIMEOUT_MS);
    });
    expect(screen.getByRole("status")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(NOTICE_FADE_MS);
    });
    expect(screen.queryByRole("status")).toBeNull();
  });
});
