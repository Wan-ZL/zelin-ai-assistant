// 可访问性判例（issue #8；CONTRACT §54.1 第 11 项）：
//   1) 键盘路径——每种卡的 <article> 可聚焦，Enter / Space 打开详情抽屉（双击的键盘等价物）；
//      焦点在卡内按钮上按 Enter 不会顺带开抽屉（按钮自己的 Enter 归按钮）；
//   2) 状态不靠颜色——每张卡的 aria-label 以状态词开头（色点 aria-hidden）；
//   3) 复制反馈可听——单击复制后有 role=status 的「已复制」播报；
//   4) axe-core 全卡面扫描零 violation（color-contrast 规则在 jsdom 无布局不可判，关掉；
//      其余 WCAG 2.x A/AA 规则全开）。
import axe from "axe-core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests, useAppState } from "../../store";
import {
  DEBT_FIXTURE,
  PROPOSAL_PROCESSING,
  PROPOSAL_T1,
  REVIEW_FIXTURE,
  TASK_BLOCKED,
  TASK_DONE,
  TASK_QUEUED,
  TASK_WORKING,
} from "../styleguide/fixtures";
import { DebtCardItem } from "./DebtCardItem";
import { DoneCard } from "./DoneCard";
import { ProposalCard } from "./ProposalCard";
import { ReviewCard } from "./ReviewCard";
import { RunningCard } from "./RunningCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
}));

beforeEach(() => {
  resetStoreForTests();
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
  }
});
afterEach(cleanup);

function SelectedProbe() {
  const { selectedCardId } = useAppState();
  return <output data-testid="selected">{selectedCardId ?? ""}</output>;
}

const CARDS: Array<{ name: string; id: string; stateWord: string; node: () => JSX.Element }> = [
  { name: "proposal", id: PROPOSAL_T1.id, stateWord: "Proposal", node: () => <ProposalCard card={PROPOSAL_T1} /> },
  { name: "raising placeholder", id: PROPOSAL_PROCESSING.id, stateWord: "AI researching", node: () => <ProposalCard card={PROPOSAL_PROCESSING} /> },
  { name: "queued", id: TASK_QUEUED.id, stateWord: "Queued", node: () => <RunningCard row={TASK_QUEUED} /> },
  { name: "working", id: TASK_WORKING.id, stateWord: "Working", node: () => <RunningCard row={TASK_WORKING} /> },
  { name: "blocked", id: TASK_BLOCKED.id, stateWord: "Needs input", node: () => <RunningCard row={TASK_BLOCKED} isBlocked /> },
  { name: "review", id: REVIEW_FIXTURE.id, stateWord: "In review", node: () => <ReviewCard card={REVIEW_FIXTURE} /> },
  { name: "done", id: TASK_DONE.id, stateWord: "Done", node: () => <DoneCard row={TASK_DONE} /> },
  { name: "backlog", id: DEBT_FIXTURE.id, stateWord: "Backlog", node: () => <DebtCardItem item={DEBT_FIXTURE} /> },
];

describe("board cards — keyboard path + state not by color (issue #8)", () => {
  for (const c of CARDS) {
    it(`${c.name}: focusable, labelled with its state word, Enter/Space opens the detail drawer`, () => {
      render(<>{c.node()}<SelectedProbe /></>);
      const surface = screen.getByRole("article", { name: new RegExp(`^${c.stateWord} · `) });
      expect(surface.tagName).toBe("ARTICLE");
      expect(surface.getAttribute("tabindex")).toBe("0");
      surface.focus();
      fireEvent.keyDown(surface, { key: "Enter" });
      expect(screen.getByTestId("selected").textContent).toBe(c.id);
      resetStoreForTests();
      fireEvent.keyDown(surface, { key: " " });
      expect(screen.getByTestId("selected").textContent).toBe(c.id);
    });
  }

  it("Enter on a button inside the card belongs to the button, not the card", () => {
    render(<><ProposalCard card={PROPOSAL_T1} /><SelectedProbe /></>);
    const details = screen.getByRole("button", { name: /Details/ });
    fireEvent.keyDown(details, { key: "Enter" });
    expect(screen.getByTestId("selected").textContent).toBe("");
  });

  it("click-to-copy announces success through a status region", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<RunningCard row={TASK_WORKING} />);
    const copy = screen.getByRole("button", { name: /Click to copy the command/ });   // visible text = accessible name (WCAG 2.5.3)
    fireEvent.click(copy);
    await screen.findByText("Copied to clipboard", { selector: "[role='status']" });
    expect(writeText).toHaveBeenCalledTimes(1);
  });
});

describe("board cards — axe-core scan", () => {
  for (const c of CARDS) {
    it(`${c.name}: no WCAG violations`, async () => {
      const { container } = render(c.node());
      const results = await axe.run(container, {
        rules: { "color-contrast": { enabled: false } },   // needs layout; jsdom has none
      });
      const summary = results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`);
      expect(summary).toEqual([]);
    });
  }
});
