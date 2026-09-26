// §78（issue #447）潜在任务卡 = 唯一的机器卡面，一次点击「促成运行」才开跑：
//   1) 促成运行发的是**既有** approve 动词的四键形 {action,comment,id}——没有新 wire 动词；
//   2) §50 打字确认闸门照搬：T2 / W17 生效 T2 不许单击直批，且没看过明细连按钮都不给；
//   3) raising 灰占位（processing: true）只有 sheen，不给任何决策按钮——还没有计划与验收标准的卡
//      不该能被促成运行（§0.4）；
//   4) 卡面不许藏判据：§7 egress、§50 生效档位、§40 费用、§11 DoD 都在促成运行那颗键旁边；
//   5) 修改 = comment、拒绝 = fork（reject / done_external）、研究并提议 = raise、删除 = trash。
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
import { postAction } from "../../api";

beforeEach(() => {
  // jsdom <dialog> 兜底：老版本没有 showModal/close
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

describe("DebtCardItem 促成运行（§78）", () => {
  it("T1：单击直发既有的 approve 四键形——无 ts、无多余字段、无新动词", () => {
    render(<DebtCardItem item={card()} />);
    fireEvent.click(screen.getByRole("button", { name: "Run it" }));
    expect(postAction).toHaveBeenCalledTimes(1);
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "approve", comment: null, id: "R-001" });
  });

  it("T2：没看过明细不给键；看过后开 typed-confirm，错词不发、正词发的仍是同一条 approve", async () => {
    render(<DebtCardItem item={card({ tier: "T2" })} />);
    expect(screen.queryByRole("button", { name: "Run it" })).toBeNull();
    expect(screen.getByText("T2: expand details first")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Details ▸" }));
    fireEvent.click(await screen.findByRole("button", { name: "Run it" }));
    expect(postAction).not.toHaveBeenCalled();
    const input = screen.getByPlaceholderText("Type 确认 or go");
    const confirm = screen.getAllByRole("button", { name: "Approve" }).find((b) => b.closest("dialog"))!;
    fireEvent.change(input, { target: { value: "yes" } });
    fireEvent.click(confirm);
    expect(postAction).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "  GO " } });
    fireEvent.click(confirm);
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "approve", comment: null, id: "R-001" });
  });

  it("W17 外部升档（tier=T1、effective_tier=T2）：卡面点明升档，且照样过打字确认", async () => {
    render(<DebtCardItem item={card({ effective_tier: "T2", origin_trust: "external" })} />);
    expect(screen.getByText("External → T2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Details ▸" }));
    fireEvent.click(await screen.findByRole("button", { name: "Run it" }));
    expect(postAction).not.toHaveBeenCalled();
    fireEvent.change(screen.getByPlaceholderText("Type 确认 or go"), { target: { value: "确认" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Approve" }).find((b) => b.closest("dialog"))!);
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "approve", comment: null, id: "R-001" });
  });

  it("raising 灰占位（processing）：只有 sheen 一句，没有促成运行 / 拒绝 / 修改 / 删除", () => {
    render(<DebtCardItem item={card({ processing: true })} />);
    expect(screen.getByText("AI is researching; the plan and DoD land on this card when it finishes")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Run it" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("促成运行不许坐在一张藏了判据的卡上：egress（§7）/ 费用（§40）/ 档位 / DoD 都在同一屏", () => {
    render(<DebtCardItem item={card({
      show_cost: true,
      cost_state: "estimated",
      cost_usd: 12,
      egress: [{ kind: "github_repo_create", target: "acme-bench" }],
      dod: ["导出按钮可点", "CSV 能下载"],
      target_kind: "new",
      target_name: "acme-bench",
    })} />);
    expect(screen.getByText(/Approving creates the private GitHub repo/)).toBeTruthy();
    expect(screen.getByText("$12")).toBeTruthy();
    expect(screen.getByText("one-click approve")).toBeTruthy();
    expect(screen.getByText("Definition of done:")).toBeTruthy();
    expect(screen.getByText("🟢 New repo: acme-bench")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Run it" })).toBeTruthy();
  });

  it("没分级的行（老 server 的债务行 / server 发了空 tier）：章说「未分级」而不是假装有档位，促成运行仍是单击", () => {
    render(<DebtCardItem item={{ id: "R-777", title: "旧债务行" } as DebtCard} />);
    expect(screen.getByText("Untiered")).toBeTruthy();
    expect(screen.queryByText("Definition of done:")).toBeNull();   // 空 DoD 不渲染（D43）
    fireEvent.click(screen.getByRole("button", { name: "Run it" }));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "approve", comment: null, id: "R-777" });
  });

  it("其余动词照旧是既有 inbox 动词：修改 = comment、拒绝 fork = reject / done_external、研究并提议 = raise、删除 = trash", () => {
    const { unmount } = render(<DebtCardItem item={card()} />);
    fireEvent.click(screen.getByRole("button", { name: "Comment" }));
    fireEvent.change(screen.getByPlaceholderText("What to change…"), { target: { value: "换个方向" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "comment", comment: "换个方向", id: "R-001" });
    unmount();

    vi.mocked(postAction).mockClear();
    const second = render(<DebtCardItem item={card()} />);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    fireEvent.click(screen.getByRole("button", { name: "Already done (mark delivered)" }));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "done_external", comment: null, id: "R-001" });
    second.unmount();

    vi.mocked(postAction).mockClear();
    render(<DebtCardItem item={card()} />);
    fireEvent.click(screen.getByRole("button", { name: "Research & propose" }));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "raise", comment: null, id: "R-001" });
  });
});
