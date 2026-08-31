// 行为测试（BUILD-CONTRACT §2.3 点名的两条）：
//   1) 批准按钮发出正确 payload——四键形 {action,comment,id}，无 ts（server 端盖章）、无多余字段；
//   2) T2 typed-confirm 闸门——确认词不对绝不发 approve；「go」/「确认」放行且 wire 与 T0/T1 相同。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests } from "../../store";
import type { ApprovalCard } from "../../types";
import { ProposalCard } from "./ProposalCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
}));
import { postAction } from "../../api";

// jsdom <dialog> 兜底：老版本没有 showModal/close（jsdom≥24 已内建，这里防御性 polyfill）
beforeEach(() => {
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.open = true;
    };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.open = false;
    };
  }
  resetStoreForTests();
  vi.mocked(postAction).mockClear();
});

afterEach(cleanup);

function makeCard(tier: string): ApprovalCard {
  return {
    id: "R-001",
    title: "leaderboard 一键导出评测报告",
    summary: "在 dashboard 加导出按钮",
    tier,
    show_cost: false,
    processing: false,
    sources: [],
    plan: ["step1"],
    dod: ["done"],
    cost_usd: 85,
  };
}

describe("ProposalCard approve", () => {
  it("T1 批准 = 单击直发 {action, comment:null, id}——无 ts、无多余字段", () => {
    render(<ProposalCard card={makeCard("T1")} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(postAction).toHaveBeenCalledTimes(1);
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({
      action: "approve",
      comment: null,
      id: "R-001",
    });
  });

  it("T2 批准先过 typed-confirm：错词不发、正词(go/确认)放行且 wire 同 T1", () => {
    render(<ProposalCard card={makeCard("T2")} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    // 弹窗出现，approve 尚未发出
    expect(postAction).not.toHaveBeenCalled();
    const input = screen.getByPlaceholderText("Type 确认 or go");
    const dialogApprove = screen
      .getAllByRole("button", { name: "Approve" })
      .find((b) => b.closest("dialog"))!;

    // 错词：不发出，提示不匹配并留在弹窗
    fireEvent.change(input, { target: { value: "yes" } });
    fireEvent.click(dialogApprove);
    expect(postAction).not.toHaveBeenCalled();
    expect(screen.getByText("Previous input didn't match.")).toBeTruthy();

    // 正词（大小写/首尾空白宽容）：发出与 T1 完全相同的 wire
    fireEvent.change(input, { target: { value: "  GO " } });
    fireEvent.click(dialogApprove);
    expect(postAction).toHaveBeenCalledTimes(1);
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({
      action: "approve",
      comment: null,
      id: "R-001",
    });
  });

  it("W17 外部升档：tier=T1 但 effective_tier=T2 → 批准必须过 typed-confirm", () => {
    // F1/L3 修复判例（§50）：外部出身卡投影带 effective_tier="T2"，
    // 声明档 T1 也不许单击直批——弹窗拦住，确认词放行后 wire 与 T1 相同
    const card = { ...makeCard("T1"), effective_tier: "T2", origin_trust: "external" };
    render(<ProposalCard card={card} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(postAction).not.toHaveBeenCalled();
    const input = screen.getByPlaceholderText("Type 确认 or go");
    const dialogApprove = screen
      .getAllByRole("button", { name: "Approve" })
      .find((b) => b.closest("dialog"))!;
    fireEvent.change(input, { target: { value: "确认" } });
    fireEvent.click(dialogApprove);
    expect(postAction).toHaveBeenCalledTimes(1);
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({
      action: "approve",
      comment: null,
      id: "R-001",
    });
  });

  it("拒绝 fork：已办完分支发 done_external（同四键形）", () => {
    render(<ProposalCard card={makeCard("T1")} />);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    fireEvent.click(screen.getByRole("button", { name: "Already done (mark delivered)" }));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({
      action: "done_external",
      comment: null,
      id: "R-001",
    });
  });
});
