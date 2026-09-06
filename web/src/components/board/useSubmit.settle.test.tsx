// useSubmit 的解锁判据是这条动作的真信号，不是 generated_at（§39.3「generated_at bump 不清（§21bis 先例）」）：
// actd 每个 pass 结尾都重写看板；一条 approve 若落在本 pass drain 之后，「新一版快照」照样来——卡没动、按钮却
// 解锁了（可双击重复提交）。原生 PendingSweep.cleared(by:) 逐动词判；这里钉 hook 级行为 + 180 s 逐动词文案。
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

function board(over: Partial<Board> = {}, generated_at = "2026-09-05T10:00:00Z"): Board {
  return {
    generated_at, counts: {}, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [],
    ...over,
  } as unknown as Board;
}
const proposal = (id: string, over: Record<string, unknown> = {}) =>
  ({ id, title: `${id} title`, summary: "摘要", tier: "T1", show_cost: false, processing: false, sources: [], plan: ["step 1"], dod: [], ...over }) as never;

async function load(b: Board) {
  vi.mocked(fetchBoard).mockResolvedValue(b);
  await act(async () => {
    await refreshBoard();
  });
}

describe("useSubmit：解锁看真信号", () => {
  beforeEach(async () => {
    resetStoreForTests();
    vi.mocked(fetchBoard).mockReset();
    vi.mocked(postAction).mockReset().mockResolvedValue({ ok: true });
    vi.useFakeTimers();
    await load(board({ needs_approval: [proposal("P-1"), proposal("P-2")] }));
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("approve：generated_at 变了但卡仍在提案列 → 仍 pending；卡离开提案列 → 解锁", async () => {
    const { result } = renderHook(() => useSubmit());
    await act(async () => {
      await result.current.submit({ action: "approve", id: "P-1", comment: null });
    });
    expect(result.current.pending).toBe(true);

    // actd 例行重写：只换 generated_at，P-1 还在提案列
    await load(board({ needs_approval: [proposal("P-1"), proposal("P-2")] }, "2026-09-05T10:00:10Z"));
    expect(result.current.pending).toBe(true);
    expect(result.current.error).toBeNull();

    // 真信号：P-1 进了运行中（排队）
    await load(board({ needs_approval: [proposal("P-2")], running: [{ id: "P-1", name: "n", state: "queued" } as never] }, "2026-09-05T10:00:20Z"));
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toBeNull();

    act(() => {
      vi.advanceTimersByTime(CONFIRM_TIMEOUT_MS + 1);
    });
    expect(result.current.error).toBeNull(); // 解锁即撤定时器，不追加误报
  });

  it("comment：同版快照重拉不解锁；plan 追加 tag 才解锁", async () => {
    const { result } = renderHook(() => useSubmit());
    await act(async () => {
      await result.current.submit({ action: "comment", id: "P-1", comment: "改一下" });
    });
    await load(board({ needs_approval: [proposal("P-1"), proposal("P-2")] }, "2026-09-05T10:00:10Z"));
    expect(result.current.pending).toBe(true);
    await load(board({ needs_approval: [proposal("P-1", { plan: ["step 1", "[2026-09-05 修改方向] 改一下"] }), proposal("P-2")] }, "2026-09-05T10:00:20Z"));
    expect(result.current.pending).toBe(false);
  });

  it("180 s 真信号没来、卡还在 → 「后台响应超时，卡片已恢复可操作」；卡不见了 → 点名 actd", async () => {
    const { result } = renderHook(() => useSubmit());
    await act(async () => {
      await result.current.submit({ action: "approve", id: "P-1", comment: null });
    });
    await load(board({ needs_approval: [proposal("P-1"), proposal("P-2")] }, "2026-09-05T10:00:10Z"));
    act(() => {
      vi.advanceTimersByTime(CONFIRM_TIMEOUT_MS);
    });
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toBe("Backend timed out — the card is interactive again");

    const second = renderHook(() => useSubmit());
    await act(async () => {
      await second.result.current.submit({ action: "trash", id: "P-2", comment: null });
    });
    await load(board({ needs_approval: [proposal("P-1")] }, "2026-09-05T10:00:30Z")); // P-2 消失了——那就是落地，先解锁
    expect(second.result.current.pending).toBe(false);
  });

  it("restore 超时 → 原生「恢复超时」句；set_title 超时 → 「改名超时」句", async () => {
    await load(board({ trash: [{ id: "R-9", title: "t", permanent: false, trashed_at: "2026-09-01T00:00:00Z" }] }));
    const restore = renderHook(() => useSubmit());
    await act(async () => {
      await restore.result.current.submit({ action: "restore", id: "R-9" });
    });
    const rename = renderHook(() => useSubmit());
    await act(async () => {
      await rename.result.current.submit({ action: "set_title", id: "R-9", title: "新名" });
    });
    act(() => {
      vi.advanceTimersByTime(CONFIRM_TIMEOUT_MS);
    });
    expect(restore.result.current.error).toBe("Restore timed out — the card is back in the trash, try again (check that actd is running)");
    expect(rename.result.current.error).toBe("Rename timed out — the card name is unchanged, try again (check that actd is running)");
  });

  it("set_title：后台 display_title 等于新名（空白归一）才解锁", async () => {
    const { result } = renderHook(() => useSubmit());
    await act(async () => {
      await result.current.submit({ action: "set_title", id: "P-1", title: "整理  合同" });
    });
    await load(board({ needs_approval: [proposal("P-1", { display_title: "P-1 title" }), proposal("P-2")] }, "2026-09-05T10:00:10Z"));
    expect(result.current.pending).toBe(true);
    await load(board({ needs_approval: [proposal("P-1", { display_title: "整理 合同", user_titled: true }), proposal("P-2")] }, "2026-09-05T10:00:20Z"));
    expect(result.current.pending).toBe(false);
  });
});
