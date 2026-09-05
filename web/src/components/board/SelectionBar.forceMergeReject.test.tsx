// §21bis 强制合并的「合并中…」章只在 POST 成功后才挂（原生 AppDelegate.submitMergeForce：`guard writeInboxFile` 才
// `store.beginMergeForce`）：server 拒绝 / 网络失败 → 不挂章、180 s 后也不出「强制合并未确认…检查 actd」——那句点名
// actd，而请求根本没进 inbox，挂上就是两句谎话（§0 第 3 条）。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, postAction } from "../../api";
import { LanguageContext } from "../../i18n";
import { FORCE_MERGE_TIMEOUT_MS, getState, refreshBoard, resetStoreForTests, setSelectionMode, toggleSelected } from "../../store";
import type { Board } from "../../types";
import { SelectionBar } from "./SelectionBar";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

function board(generated_at = "2026-09-05T10:00:00Z"): Board {
  const proposal = (id: string) => ({ id, title: id, tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] });
  return {
    generated_at, counts: {}, needs_approval: [proposal("P-1"), proposal("P-2")],
    running: [], needs_input: [], review: [], completed: [], debt: [], trash: [],
  } as unknown as Board;
}

async function load(b: Board) {
  vi.mocked(fetchBoard).mockResolvedValue(b);
  await act(async () => {
    await refreshBoard();
  });
}

async function confirmForceMerge() {
  render(<LanguageContext.Provider value="zh"><SelectionBar /></LanguageContext.Provider>);
  fireEvent.click(screen.getByRole("button", { name: "强制合并 (2)" }));
  await act(async () => {
    fireEvent.click(screen.getAllByRole("button", { name: "强制合并" }).at(-1)!);
  });
}

beforeEach(async () => {
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
  }
  resetStoreForTests();
  vi.mocked(postAction).mockReset();
  await load(board());
  setSelectionMode(true);
  toggleSelected("P-1");
  toggleSelected("P-2");
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("SelectionBar 强制合并：章跟着 POST 的结果走", () => {
  it("POST 被拒 → 不挂「合并中…」章、错误句露出、180 s 后也没有超时条", async () => {
    vi.mocked(postAction).mockRejectedValue(new Error("inbox write failed"));
    await confirmForceMerge();
    await waitFor(() => expect(postAction).toHaveBeenCalledTimes(1));
    expect(screen.getByText("inbox write failed")).toBeTruthy();
    expect(getState().forceMergingIds.size).toBe(0);

    await load(board("2026-09-05T10:00:10Z")); // actd 例行重写，两张卡都还在
    expect(getState().forceMergingIds.size).toBe(0);

    vi.useFakeTimers();
    act(() => {
      vi.advanceTimersByTime(FORCE_MERGE_TIMEOUT_MS + 1);
    });
    expect(getState().forceMergeTimedOutAt).toBeNull();
  });

  it("POST 成功 → 章才挂上（主卡 + 副卡），且是 POST 落定之后", async () => {
    let resolve: (v: unknown) => void = () => {};
    vi.mocked(postAction).mockImplementation(() => new Promise((r) => { resolve = r; }));
    await confirmForceMerge();
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({ action: "merge_force", ids: ["P-1", "P-2"], primary: "P-1" }));
    expect(getState().forceMergingIds.size).toBe(0); // 在途：还没落定

    await act(async () => {
      resolve({});
    });
    await waitFor(() => expect([...getState().forceMergingIds].sort()).toEqual(["P-1", "P-2"]));
    expect(screen.getByText("已提交强制合并")).toBeTruthy();
  });
});
