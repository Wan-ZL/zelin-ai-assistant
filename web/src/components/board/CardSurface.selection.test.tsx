// §21 多选态的整卡 tap catcher（原生 Kanban.swift:671-705 selectableCard；CONTRACT §54.1 第 11 项追记）：
//   1) selectionMode 下卡的动作行是死的——点「批准」/「删除」/「永久完成」不发任何动作、只切换选中
//      （原生注释「a mis-click must not approve/trash anything」），键盘 Enter 合成的 click 同样拦下；
//   2) 点卡身 = 切换选中（is-selectable 手形、is-selected accent 淡底）；勾选框自己切一次、不叠加；
//      仍活着的控件（单击复制指令行）点了不算点卡身；
//   3) 全 lane 可选（v0.21，Kanban.swift:575-591 selectableIDs）：潜在任务 / 阶段性完成 / 排队中 也长勾选框，
//      提案列 AI 研究中占位不长；
//   4) 不在多选态：动作照常、点卡身不选中、没有 is-selectable；
//   5) axe：多选态的卡零 violation。
import axe from "axe-core";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { postAction } from "../../api";
import { getState, resetStoreForTests, setSelectionMode, toggleSelected } from "../../store";
import { DEBT_FIXTURE, PROPOSAL_PROCESSING, PROPOSAL_T1, TASK_DONE, TASK_QUEUED, TASK_WORKING } from "../styleguide/fixtures";
import { DebtCardItem } from "./DebtCardItem";
import { DoneCard } from "./DoneCard";
import { ProposalCard } from "./ProposalCard";
import { RunningCard } from "./RunningCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  fetchCard: vi.fn(async (id: string) => ({ id })),
}));

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.mocked(postAction).mockClear();
});
afterEach(cleanup);

const selected = () => [...getState().selectedIds];

describe("selection mode blocks the card's own actions (native tap catcher)", () => {
  it("提案卡：批准 / 拒绝 / 暂缓 在多选态点了不发动作、不开弹窗，这一下算点卡身 → 选中", () => {
    setSelectionMode(true);
    render(<ProposalCard card={PROPOSAL_T1} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(postAction).not.toHaveBeenCalled();
    expect(selected()).toEqual([PROPOSAL_T1.id]);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(document.querySelector("dialog")).toBeNull();
    expect(selected()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Later" }));
    expect(postAction).not.toHaveBeenCalled();
    expect(selected()).toEqual([PROPOSAL_T1.id]);
  });

  it("潜在任务卡：删除 / 永久完成 在多选态不发 trash / archive", () => {
    setSelectionMode(true);
    render(<DebtCardItem item={DEBT_FIXTURE} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: /Done for good/ }));
    expect(postAction).not.toHaveBeenCalled();
  });

  it("「展开详情 ▸」在动作行里，多选态也是死的（原生 overlay 盖住整卡）", () => {
    setSelectionMode(true);
    render(<ProposalCard card={PROPOSAL_T1} />);
    fireEvent.click(screen.getByRole("button", { name: "Details ▸" }));
    expect(getState().selectedCardId).toBeNull();
    expect(selected()).toEqual([PROPOSAL_T1.id]);
  });
});

describe("card body toggles selection; the checkbox stays the a11y path", () => {
  it("点卡身切换选中，再点取消；类名 is-selectable / is-selected 跟着走", () => {
    setSelectionMode(true);
    render(<ProposalCard card={PROPOSAL_T1} />);
    const article = screen.getByRole("article");
    expect(article.className).toContain("is-selectable");
    expect(article.className).not.toContain("is-selected");
    fireEvent.click(article);
    expect(selected()).toEqual([PROPOSAL_T1.id]);
    expect(article.className).toContain("is-selected");
    fireEvent.click(screen.getByText(String(PROPOSAL_T1.summary)));   // 标题文字也是卡身
    expect(selected()).toEqual([]);
    expect(article.className).not.toContain("is-selected");
  });

  it("勾选框只切一次（不叠加卡身的切换），checked 跟 store", () => {
    setSelectionMode(true);
    render(<ProposalCard card={PROPOSAL_T1} />);
    const box = screen.getByRole("checkbox", { name: `Select ${PROPOSAL_T1.id}` }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    expect(selected()).toEqual([PROPOSAL_T1.id]);
    expect(box.checked).toBe(true);
    fireEvent.click(box);
    expect(selected()).toEqual([]);
    expect(box.checked).toBe(false);
  });

  it("仍活着的控件（单击复制指令行）点了不算点卡身", () => {
    setSelectionMode(true);
    render(<RunningCard row={TASK_WORKING} />);
    fireEvent.click(screen.getByRole("button", { name: /Click to copy the command/ }));
    expect(selected()).toEqual([]);
  });

  it("store 里已选的卡挂载即带 is-selected", () => {
    setSelectionMode(true);
    toggleSelected(PROPOSAL_T1.id);
    render(<ProposalCard card={PROPOSAL_T1} />);
    expect(screen.getByRole("article").className).toContain("is-selected");
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
  });
});

describe("every board lane is selectable (v0.21 selectableIDs)", () => {
  it("潜在任务 / 阶段性完成 / 排队中 / 执行中 都长勾选框；AI 研究中占位不长", () => {
    setSelectionMode(true);
    const faces: Array<[string, JSX.Element, boolean]> = [
      [DEBT_FIXTURE.id, <DebtCardItem item={DEBT_FIXTURE} />, true],
      [TASK_DONE.id, <DoneCard row={TASK_DONE} />, true],
      [TASK_QUEUED.id, <RunningCard row={TASK_QUEUED} />, true],
      [TASK_WORKING.id, <RunningCard row={TASK_WORKING} />, true],
      [PROPOSAL_PROCESSING.id, <ProposalCard card={PROPOSAL_PROCESSING} />, false],
    ];
    for (const [id, node, expected] of faces) {
      const { unmount } = render(node);
      expect(screen.queryByRole("checkbox", { name: `Select ${id}` }) !== null).toBe(expected);
      expect(screen.getByRole("article").className.includes("is-selectable")).toBe(expected);
      unmount();
    }
  });

  it("AI 研究中占位点卡身不选中（原生 selectable: !card.processing）", () => {
    setSelectionMode(true);
    render(<ProposalCard card={PROPOSAL_PROCESSING} />);
    fireEvent.click(screen.getByRole("article"));
    expect(selected()).toEqual([]);
  });
});

describe("outside selection mode nothing changes", () => {
  it("动作照常发出；点卡身不选中；没有 is-selectable / 勾选框", () => {
    render(<DoneCard row={TASK_DONE} />);
    const article = screen.getByRole("article");
    expect(article.className).not.toContain("is-selectable");
    expect(screen.queryByRole("checkbox")).toBeNull();
    fireEvent.click(article);
    expect(selected()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Back to review" }));
    expect(postAction).toHaveBeenCalledWith({ action: "revert_review", comment: null, id: TASK_DONE.id });
  });

  it("退出多选态即刻恢复：同一张卡的 批准 又能点", () => {
    setSelectionMode(true);
    render(<ProposalCard card={PROPOSAL_T1} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(postAction).not.toHaveBeenCalled();
    act(() => setSelectionMode(false));
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(postAction).toHaveBeenCalledWith({ action: "approve", comment: null, id: PROPOSAL_T1.id });
  });
});

describe("axe in selection mode", () => {
  it("可选卡（未选 + 已选）零 violation", async () => {
    setSelectionMode(true);
    toggleSelected(TASK_DONE.id);
    const { container } = render(<><ProposalCard card={PROPOSAL_T1} /><DoneCard row={TASK_DONE} /></>);
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
  });
});
