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
    // M8：卡面点明升档，别让用户见 "T1" 却弹 T2 确认框
    expect(screen.getByText("External → T2")).toBeTruthy();
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

describe("ProposalCard §60 two-stage ids (D21)", () => {
  it("卡面显示 display_id，动作 payload 仍送主键 id", () => {
    // 已批准过又退回提案的卡：主键 P-012、工作编号 R-280——看到的是 R-280，发出去的是 P-012
    const card = { ...makeCard("T1"), id: "P-012", work_id: "R-280", display_id: "R-280", id_kind: "work" };
    render(<ProposalCard card={card} />);
    expect(screen.getByText("R-280")).toBeTruthy();
    expect(screen.queryByText("P-012")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({
      action: "approve",
      comment: null,
      id: "P-012",
    });
  });

  it("提案卡（未批准）显示 P- 主键；legacy R 主键按 server 的 id_kind 灰显", () => {
    const { unmount } = render(<ProposalCard card={{ ...makeCard("T1"), id: "P-007", display_id: "P-007", id_kind: "proposal" }} />);
    const shown = screen.getByText("P-007");
    expect(shown.className).toBe("card-id");
    unmount();
    render(<ProposalCard card={{ ...makeCard("T1"), id: "R-050", display_id: "R-050", id_kind: "legacy" }} />);
    expect(screen.getByText("R-050").className).toContain("card-id-legacy");
  });

  it("T2 typed-confirm 弹窗点名的是展示编号，wire 仍是主键", () => {
    const card = { ...makeCard("T2"), id: "P-012", work_id: "R-280", display_id: "R-280", id_kind: "work" };
    render(<ProposalCard card={card} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(screen.getByText(/Approve R-280:/)).toBeTruthy();
    const input = screen.getByPlaceholderText("Type 确认 or go");
    const dialogApprove = screen
      .getAllByRole("button", { name: "Approve" })
      .find((b) => b.closest("dialog"))!;
    fireEvent.change(input, { target: { value: "go" } });
    fireEvent.click(dialogApprove);
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "approve", comment: null, id: "P-012" });
  });
});

describe("ProposalCard §7 egress[] disclosure (issue #11)", () => {
  it("github_repo_create 行以后果语气渲染，带 target；空/缺席不渲染", () => {
    const card = {
      ...makeCard("T1"),
      target_repo: "~/Projects/brand-new-repo",
      target_name: "brand-new-repo",
      target_kind: "new",
      egress: [{ kind: "github_repo_create", target: "brand-new-repo", visibility: "private" }],
    };
    const { unmount } = render(<ProposalCard card={card} />);
    const list = screen.getByRole("list", { name: "What leaves this Mac if you approve" });
    expect(list.textContent).toContain("Approving creates the private GitHub repo “brand-new-repo” and pushes content");
    unmount();

    // flag off (today's default) → egress: [] → no disclosure list at all
    render(<ProposalCard card={{ ...card, egress: [] }} />);
    expect(screen.queryByRole("list", { name: "What leaves this Mac if you approve" })).toBeNull();
    cleanup();
    // old server (no key) → same
    render(<ProposalCard card={makeCard("T1")} />);
    expect(screen.queryByRole("list", { name: "What leaves this Mac if you approve" })).toBeNull();
  });

  it("未知 kind 不吞——按 kind 原文降级显示（披露宁多勿少）", () => {
    const card = { ...makeCard("T1"), egress: [{ kind: "slack_draft", target: "#team" }] };
    render(<ProposalCard card={card} />);
    expect(screen.getByText(/Approving sends data out: slack_draft → #team/)).toBeTruthy();
  });
});
