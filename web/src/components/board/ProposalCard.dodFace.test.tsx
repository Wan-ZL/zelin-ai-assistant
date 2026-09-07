// 提案卡面的「怎样算办完」（D43；CONTRACT §54.1 第 2 项 2026-09-06 追记；gap board-cards-proposal-dod-on-face）：
//   原生 Cards.swift:1085-1099 把 DoD 放在 ApprovalCardView 收起态正文里（「§11 验收标准 — visible by default: approving
//   the card approves this」），D34 后 web 卡面一条不剩。自此：
//   1) 卡面在章行之后、分歧之前给「怎样算办完：」+ 前 3 条 + 「+N」；空 DoD 不渲染；AI 研究中占位不渲染；
//   2) D34 单一详情面不动：plan / 来源 / 技术标题仍不在卡面，「展开详情 ▸」仍是唯一详情入口，点了卡面一个字不多；
//   3) 详情侧栏（DetailFields）的「怎样算办完：」全文清单不变（5 条全在，编号 <ol>）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getState, resetStoreForTests } from "../../store";
import type { ApprovalCard, CardDetail } from "../../types";
import { DetailFields } from "../detail/DetailFields";
import { ProposalCard } from "./ProposalCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  fetchCard: vi.fn(async (id: string) => ({ id })),
}));

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
});
afterEach(cleanup);

const FIVE = ["dashboard 页出现「导出报告」按钮", "点击后生成 markdown + png 到 exports/", "draft PR 通过 CI", "文档更新", "截图进 README"];

function proposal(extra: Partial<ApprovalCard> = {}): ApprovalCard {
  return {
    id: "P-101",
    title: "leaderboard 一键导出评测报告（技术标题）",
    summary: "在 dashboard 加导出按钮",
    tier: "T1",
    show_cost: false,
    processing: false,
    sources: [{ who: "sam", channel: "slack", date: "2026-08-30", quote: "能不能一键导出" }],
    plan: ["加按钮", "接后端"],
    dod: FIVE,
    disagreement: "口径不一致",
    ...extra,
  };
}

describe("proposal face: 怎样算办完 (D43)", () => {
  it("章行之后、分歧之前：标题 + 前 3 条编号 + 「+2」；plan / 来源 / 技术标题仍不在卡面", () => {
    const { container } = render(<ProposalCard card={proposal()} />);
    const block = container.querySelector(".card-dod.is-dod")!;
    expect(block).not.toBeNull();
    expect(block.querySelector(".card-dod-heading")!.textContent).toBe("Definition of done:");
    const items = Array.from(block.querySelectorAll(".card-dod-item")).map((li) => li.textContent?.replace(/\s+/g, " ").trim());
    expect(items).toEqual([`1. ${FIVE[0]}`, `2. ${FIVE[1]}`, `3. ${FIVE[2]}`]);
    expect(block.querySelector(".card-dod-more")!.textContent).toContain("+2");
    expect(screen.queryByText(/文档更新/)).toBeNull();
    // 位置：章行 → DoD → 分歧
    const badges = container.querySelector(".card-badges")!;
    expect(badges.nextElementSibling).toBe(block);
    expect(block.nextElementSibling?.textContent).toContain("Disagreement");
    // D34：其余积木仍只在侧栏
    expect(screen.queryByText(/接后端/)).toBeNull();
    expect(screen.queryByText(/能不能一键导出/)).toBeNull();
    expect(screen.queryByText(/技术标题/)).toBeNull();
  });

  it("空 DoD 不渲染块；AI 研究中占位不渲染", () => {
    const { container, rerender } = render(<ProposalCard card={proposal({ dod: [] })} />);
    expect(container.querySelector(".card-dod")).toBeNull();
    rerender(<ProposalCard card={proposal({ processing: true })} />);
    expect(container.querySelector(".card-dod")).toBeNull();
  });

  it("点「展开详情 ▸」= 开侧栏；卡面 DoD 仍是那 3 条 + +2，不就地撑开（D34 不动）", () => {
    const { container } = render(<ProposalCard card={proposal()} />);
    fireEvent.click(screen.getByRole("button", { name: "Details ▸" }));
    expect(getState().selectedCardId).toBe("P-101");
    expect(container.querySelectorAll(".card-dod-item")).toHaveLength(3);
    expect(screen.queryByText(/文档更新/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Collapse ▾" })).toBeNull();
  });

  it("侧栏 DetailFields 的「怎样算办完：」仍是全文清单（5 条，编号 <ol>）", () => {
    const detail = { ...proposal(), lane: "needs_approval" } as unknown as CardDetail;
    const { container } = render(<DetailFields detail={detail} />);
    expect(screen.getByText("Definition of done:").tagName).toBe("H3");
    const section = screen.getByText("Definition of done:").closest(".zai-detail-section")!;
    expect(Array.from(section.querySelectorAll("ol li")).map((li) => li.textContent)).toEqual(FIVE);
    expect(container.querySelector(".card-dod")).toBeNull();
  });
});
