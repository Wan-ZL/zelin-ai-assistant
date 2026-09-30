// §7 `egress[]` 出机披露（issue #11）：促成运行这张卡会把什么送出这台 Mac，逐条以「后果」语气
// 上卡面，住在动词那一排之前——「促成运行」永不许坐在一张藏了出机后果的卡上。
//   · github_repo_create → 点名将建的私有仓库与推送；
//   · 未知 kind 不吞，按 kind 原文降级显示（披露宁多勿少）；
//   · 空表 / 老 server 缺键 → 整段不渲染（flag 关 = 今日默认）。
// §78（D80，issue #447）提案列退役后这段判例的被测面是潜在任务卡（DebtCardItem）——`EgressLines`
// 随卡搬进那个文件，披露句本身一个字没改（wire 动词仍叫 approve，§41 弹窗那颗键仍叫「批准」）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests } from "../../store";
import type { DebtCard } from "../../types";
import { DebtCardItem } from "./DebtCardItem";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  fetchCard: vi.fn(async (id: string) => ({ id })),
}));

beforeEach(() => resetStoreForTests());
afterEach(cleanup);

function card(over: Partial<DebtCard> = {}): DebtCard {
  return {
    id: "R-001",
    title: "leaderboard 一键导出评测报告",
    summary: "在 dashboard 加导出按钮",
    tier: "T1",
    show_cost: false,
    processing: false,
    sources: [],
    plan: ["step1"],
    dod: ["done"],
    cost_usd: 85,
    ...over,
  };
}

const LIST_LABEL = "What leaves this Mac if you approve";

describe("DebtCardItem §7 egress[] disclosure (issue #11)", () => {
  it("github_repo_create 行以后果语气渲染，带 target；空/缺席不渲染", () => {
    const withEgress = card({
      target_repo: "~/Projects/brand-new-repo",
      target_name: "brand-new-repo",
      target_kind: "new",
      egress: [{ kind: "github_repo_create", target: "brand-new-repo", visibility: "private" }],
    });
    const { unmount } = render(<DebtCardItem item={withEgress} />);
    const list = screen.getByRole("list", { name: LIST_LABEL });
    expect(list.textContent).toContain("Approving creates the private GitHub repo “brand-new-repo” and pushes content");
    unmount();

    // flag off (today's default) → egress: [] → no disclosure list at all
    render(<DebtCardItem item={{ ...withEgress, egress: [] }} />);
    expect(screen.queryByRole("list", { name: LIST_LABEL })).toBeNull();
    cleanup();
    // old server (no key) → same
    render(<DebtCardItem item={card()} />);
    expect(screen.queryByRole("list", { name: LIST_LABEL })).toBeNull();
  });

  it("未知 kind 不吞——按 kind 原文降级显示（披露宁多勿少）", () => {
    render(<DebtCardItem item={card({ egress: [{ kind: "slack_draft", target: "#team" }] })} />);
    expect(screen.getByText(/Approving sends data out: slack_draft → #team/)).toBeTruthy();
  });

  it("披露段在动作行之前：读到后果才轮到「促成运行」那颗键", () => {
    const { container } = render(<DebtCardItem item={card({ egress: [{ kind: "github_repo_create", target: "acme" }] })} />);
    const list = container.querySelector(".card-egress")!;
    const actions = container.querySelector(".card-actions")!;
    expect(list.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("button", { name: "Run it" })).toBeTruthy();
  });
});
