// 机器卡标题 = §37 摘要优先链（原生 ApprovalCardView Cards.swift:1073 card.displaySummary；占位 :945；
// T2 确认 :984；拒绝分叉 :1001；DebtRow :2028——同一个字串）。真实投影里 display_title 恒非空（dashboard.py
// _display_title），所以「display_title 优先」会让大白话摘要在真数据上永远消失——本判例钉：
//   · summary ≠ display_title → 卡面 / aria-label / T2 弹窗 / 拒绝弹窗 都是 summary，display_title 不上卡面；
//   · user_titled=true → 钦定名压过 summary；
//   · AI 研究中占位同链（不是冻结 title）；
//   · summary 缺席 → display_title 顶上（裸 URL title 不上面）；
//   · 字级：摘要标题走审批卡的 15 semibold（.is-lg），raising 占位仍是 placeholder 档。
// §78（D80，issue #447）提案卡墓碑后，这条链唯一的机器卡面是潜在任务卡（DebtCardItem）——
// 「促成运行」那颗键旁边读的就是这一行，判例原样搬过来（标题一项一文件，face 判例不再重复钉）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests } from "../../store";
import type { DebtCard } from "../../types";
import { DebtCardItem } from "./DebtCardItem";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  fetchCard: vi.fn(async (id: string) => ({ id })),
}));

beforeEach(() => {
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
  }
  resetStoreForTests();
});

afterEach(cleanup);

function card(over: Partial<DebtCard> = {}): DebtCard {
  return {
    id: "P-001",
    title: "example-bench: leaderboard 一键导出评测报告",
    summary: "大白话摘要一句",
    display_title: "短名字",
    tier: "T1",
    show_cost: false,
    processing: false,
    sources: [],
    plan: ["step1"],
    dod: ["done"],
    ...over,
  };
}

describe("DebtCardItem headline (§37 summary-first face)", () => {
  it("summary ≠ display_title：卡面与 aria-label 都是 summary，display_title 不出现在卡面", () => {
    render(<DebtCardItem item={card()} />);
    expect(screen.getByRole("article", { name: "Backlog · 大白话摘要一句" })).toBeTruthy();
    expect(screen.getByText("大白话摘要一句").className).toContain("card-title");
    expect(screen.queryByText("短名字")).toBeNull();
    expect(screen.queryByText("example-bench: leaderboard 一键导出评测报告")).toBeNull();
  });

  it("user_titled=true：钦定名压过 summary", () => {
    render(<DebtCardItem item={card({ user_titled: true })} />);
    expect(screen.getByRole("article", { name: "Backlog · 短名字" })).toBeTruthy();
    expect(screen.queryByText("大白话摘要一句")).toBeNull();
  });

  it("summary 缺席 → display_title 顶上，冻结 title（裸 URL）不上卡面", () => {
    render(<DebtCardItem item={card({ title: "https://youtu.be/abc123", summary: undefined, display_title: "youtu.be ▸ abc123" })} />);
    expect(screen.getByRole("article", { name: "Backlog · youtu.be ▸ abc123" })).toBeTruthy();
    expect(screen.queryByText("https://youtu.be/abc123")).toBeNull();
  });

  it("AI 研究中占位同一条链（原生 :945 displaySummary），不是冻结 title", () => {
    render(<DebtCardItem item={card({ processing: true, plan: [], dod: [] })} />);
    expect(screen.getByRole("article", { name: "AI researching · 大白话摘要一句" })).toBeTruthy();
    expect(screen.queryByText("短名字")).toBeNull();
    expect(screen.queryByText("example-bench: leaderboard 一键导出评测报告")).toBeNull();
  });

  it("拒绝分叉弹窗正文 = headline（原生 :1001 informativeText = displaySummary）", () => {
    render(<DebtCardItem item={card()} />);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.querySelector(".dialog-body")?.textContent).toBe("大白话摘要一句");
  });

  it("T2 typed-confirm 弹窗点名的是 headline（原生 :984 confirmT2 summary = displaySummary）", async () => {
    render(<DebtCardItem item={card({ tier: "T2", user_titled: true })} />);
    fireEvent.click(screen.getByRole("button", { name: "Details ▸" }));
    // §78：卡上那颗键叫「促成运行」；弹窗里的确认键仍是原生 confirmT2 的「批准」（wire 动词 approve 未改名）
    fireEvent.click(await screen.findByRole("button", { name: "Run it" }));
    expect(screen.getByText("Approve P-001: 短名字")).toBeTruthy();
    expect(screen.queryByText(/大白话摘要一句/)).toBeNull();
  });

  // §7 / §78：「促成运行」长在这张卡上，owner 按键前读的就是这一行——它得是审批卡的 15 semibold
  // （.card-title.is-lg，原生 ApprovalCardView 同款），不是债务行的 12 medium 行标题
  it("摘要标题走审批卡字级 is-lg；raising 灰占位仍是 placeholder 字级（还没有计划可批）", () => {
    const { unmount } = render(<DebtCardItem item={card()} />);
    expect(screen.getByText("大白话摘要一句").className).toBe("card-title is-lg");
    unmount();
    render(<DebtCardItem item={card({ processing: true })} />);
    expect(screen.getByText("大白话摘要一句").className).toBe("card-title is-placeholder");
  });
});
