// §44.6 并入回执 + 合并态角标（原生 Store.swift LocalNotice / Kanban.swift cardOverlay 的 web 版，§54.4 追记）：
// 回执三节点、可关（sessionStorage `seenFoldReceipts`）、坏形跳过；「合并分析中…」跟 backend 的 analyzing 建议，
// 「合并中…」跟强制合并的会话内瞬态（真批次随副卡全部离开所有列退场——§21bis，判例在 store.forceMergeSettle.test.tsx）。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "../../api";
import { LanguageContext } from "../../i18n";
import { markForceMerging, refreshBoard, resetStoreForTests } from "../../store";
import type { Board } from "../../types";
import { MergeStateChip } from "./cardChrome";
import { FoldReceiptNotices, receiptTitle, SEEN_FOLD_RECEIPTS_KEY } from "./FoldReceiptNotices";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn() };
});

function board(over: Partial<Board> = {}, generated_at = "2026-09-02T12:00:00Z"): Board {
  return {
    generated_at, counts: {}, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [],
    ...over,
  } as unknown as Board;
}

async function load(b: Board) {
  vi.mocked(fetchBoard).mockResolvedValue(b);
  await refreshBoard();
}

const wrap = (node: JSX.Element, language: "zh" | "en" = "zh") => render(<LanguageContext.Provider value={language}>{node}</LanguageContext.Provider>);

beforeEach(() => {
  resetStoreForTests();
  window.sessionStorage.clear();
});
afterEach(cleanup);

describe("FoldReceiptNotices（§44.6）", () => {
  const receipt = { id: "abc", req: "R-105", title: "example-bench: 修 flaky 的 e2e 测试（retry 逻辑）", channel: "quick_capture", at: 1_788_350_370 };

  it("前缀 / 「展示名前 20 字」/ 后缀 三节点；en 同形", async () => {
    await load(board({ fold_receipts: [receipt] }));
    wrap(<FoldReceiptNotices />);
    expect(screen.getByText("刚才的输入已并入 R-105")).toBeTruthy();
    expect(screen.getByText("「example-bench: 修 fla」")).toBeTruthy();
    expect(screen.getByText("（没有建新卡）")).toBeTruthy();
    expect(receiptTitle({ ...receipt, title: "短" })).toBe("短");
    cleanup();
    wrap(<FoldReceiptNotices />, "en");
    expect(screen.getByText("Your input was merged into R-105")).toBeTruthy();
    expect(screen.getByText('"example-bench: 修 fla"')).toBeTruthy();
  });

  it("目标卡已消失（title 空）只报 R-xxx；坏形 / 缺键跳过；旧 payload 无键 = 不渲染", async () => {
    await load(board({ fold_receipts: [{ ...receipt, title: "" }, { id: "bad" } as never, null as never] }));
    wrap(<FoldReceiptNotices />);
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.queryByText(/「/)).toBeNull();
    cleanup();
    await load(board());
    const { container } = wrap(<FoldReceiptNotices />);
    expect(container.innerHTML).toBe("");
  });

  it("× 关掉 → sessionStorage 记 id，重挂不再弹", async () => {
    await load(board({ fold_receipts: [receipt] }));
    wrap(<FoldReceiptNotices />);
    fireEvent.click(screen.getByRole("button", { name: "知道了" }));
    expect(screen.queryAllByRole("status")).toHaveLength(0);
    expect(JSON.parse(window.sessionStorage.getItem(SEEN_FOLD_RECEIPTS_KEY) ?? "[]")).toEqual(["abc"]);
    cleanup();
    const { container } = wrap(<FoldReceiptNotices />);
    expect(container.innerHTML).toBe("");
  });
});

describe("MergeStateChip（契约七 / §21bis）", () => {
  it("在 analyzing 建议里 → 合并分析中…；done 建议不算；不在任何建议里 → 无", async () => {
    await load(board({ merge_suggestions: [
      { id: "MS-1", ids: ["P-1", "P-2"], status: "analyzing", requested_at: 1 },
      { id: "MS-2", ids: ["P-3"], status: "done", verdict: "merge", requested_at: 1 },
    ] as never }));
    const { container } = wrap(<><MergeStateChip cardId="P-1" /><MergeStateChip cardId="P-3" /><MergeStateChip cardId="P-9" /></>, "en");
    expect(container.querySelectorAll(".chip")).toHaveLength(1);
    expect(screen.getByText("Analyzing…")).toBeTruthy();
  });

  // 真批次（≥2 张）的清除判据 = 副卡全部离开所有列，见 store.forceMergeSettle.test.tsx；这里是单卡退化批次（无副卡）→ 退回看新快照
  it("强制合并已提交 → 合并中…（压过 analyzing）；单卡退化批次：下一版看板落地即清，同一版重拉不清", async () => {
    await load(board({ merge_suggestions: [{ id: "MS-1", ids: ["P-1"], status: "analyzing", requested_at: 1 }] as never }));
    wrap(<MergeStateChip cardId="P-1" />);
    act(() => markForceMerging(["P-1"]));
    expect(screen.getByText("合并中…")).toBeTruthy();
    await load(board({ merge_suggestions: [{ id: "MS-1", ids: ["P-1"], status: "analyzing", requested_at: 1 }] as never }));
    expect(screen.getByText("合并中…")).toBeTruthy();          // 同一 generated_at：回流还没来
    await load(board({}, "2026-09-02T12:00:05Z"));
    expect(screen.queryByText("合并中…")).toBeNull();          // 新一版 → 章退场；这一版里也没有 analyzing 建议了
    expect(screen.queryByText("合并分析中…")).toBeNull();
  });
});
