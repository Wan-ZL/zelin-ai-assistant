// §21 追记 / D74（issue #312）：多选条的待验收两颗键——批量验收 / 批量丢弃。
// 钉三件事：(1) 资格只认待验收列（同一份选中集里的提案卡不会被顺手验收）；
// (2) 确认弹窗逐条列出 id + 标题——「一键」给的是一次点击，不是一次盲签（§64.6 追记）；
// (3) 提交 = 逐卡一条 §3 四键形 inbox 动作（accept / trash），不发任何批量形。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, postAction } from "../../api";
import { LanguageContext } from "../../i18n";
import { getState, refreshBoard, resetStoreForTests, setSelectionMode, toggleSelected } from "../../store";
import type { Board } from "../../types";
import { SelectionBar, reviewBatchable } from "./SelectionBar";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

const proposal = (id: string) => ({ id, title: id, tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] });
const reviewRow = (id: string, name: string) => ({ id, name, dod: [], delivery_mode: "chat" });

function board(): Board {
  return {
    generated_at: "2026-09-15T10:00:00Z", counts: {},
    needs_approval: [proposal("P-9")],
    running: [], needs_input: [],
    review: [reviewRow("R-245", "整理推荐信"), reviewRow("R-246", "改简历")],
    completed: [], debt: [], trash: [],
  } as unknown as Board;
}

async function load() {
  vi.mocked(fetchBoard).mockResolvedValue(board());
  await act(async () => {
    await refreshBoard();
  });
}

function renderBar() {
  return render(<LanguageContext.Provider value="zh"><SelectionBar /></LanguageContext.Provider>);
}

beforeEach(async () => {
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
  }
  resetStoreForTests();
  vi.mocked(postAction).mockReset();
  vi.mocked(postAction).mockResolvedValue({} as never);
  await load();
  setSelectionMode(true);
});

afterEach(cleanup);

describe("reviewBatchable", () => {
  it("只认待验收列的卡：选中集里的提案卡一概不算", async () => {
    const rows = board().review;
    expect(reviewBatchable(new Set(["R-245", "P-9", "R-999"]), rows)).toEqual(["R-245"]);
    expect(reviewBatchable(new Set(), rows)).toEqual([]);
  });
});

describe("SelectionBar 的待验收批量键", () => {
  it("计数只数待验收卡；确认弹窗逐条列 id + 标题；提交是逐卡一条 accept", async () => {
    toggleSelected("R-245");
    toggleSelected("R-246");
    toggleSelected("P-9");            // 提案卡也在选中集里，但不该被验收
    renderBar();

    fireEvent.click(screen.getByRole("button", { name: "批量验收 (2)" }));
    const body = screen.getByText(/R-245 整理推荐信/);
    expect(body.textContent).toContain("R-246 改简历");
    expect(body.textContent).not.toContain("P-9");
    expect(body.textContent).toContain("只是建议");

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "验收" }).at(-1)!);
    });
    await waitFor(() => expect(postAction).toHaveBeenCalledTimes(2));
    expect(vi.mocked(postAction).mock.calls.map((c) => c[0])).toEqual([
      { action: "accept", comment: null, id: "R-245" },
      { action: "accept", comment: null, id: "R-246" },
    ]);
    expect(getState().selectedIds.size).toBe(0);   // 全部成功才清选择
  });

  it("批量丢弃发的是 trash（回收站可恢复），文案说明白它可恢复", async () => {
    toggleSelected("R-246");
    renderBar();

    fireEvent.click(screen.getByRole("button", { name: "批量丢弃 (1)" }));
    expect(screen.getByText(/R-246 改简历/).textContent).toContain("可恢复");
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "丢弃" }).at(-1)!);
    });
    await waitFor(() => expect(postAction).toHaveBeenCalledTimes(1));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "trash", comment: null, id: "R-246" });
  });

  it("选中集里没有待验收卡时两颗键都是死的", () => {
    toggleSelected("P-9");
    renderBar();
    expect((screen.getByRole("button", { name: "批量验收 (0)" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "批量丢弃 (0)" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
