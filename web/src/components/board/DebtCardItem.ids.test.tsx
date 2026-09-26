// §60 两段式编号在卡面上的规矩（D21）：卡面 / 弹窗显示 `display_id`（= work_id ?? id），
// 动作回传永远送主键 `id`；`id_kind` 由 server 给，客户端不按前缀猜（legacy 主键灰显）。
// §78（D80，issue #447）提案列退役后，机器卡的唯一卡面是潜在任务卡——这条判例随卡搬过来：
// 「促成运行」发出去的仍是主键，owner 看到的仍是工作编号。
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
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
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
    ...over,
  };
}

describe("DebtCardItem §60 two-stage ids (D21)", () => {
  it("卡面显示 display_id，动作 payload 仍送主键 id", () => {
    // 曾被促成运行又回到潜在任务的卡：主键 P-012、工作编号 R-280——看到的是 R-280，发出去的是 P-012
    render(<DebtCardItem item={card({ id: "P-012", work_id: "R-280", display_id: "R-280", id_kind: "work" })} />);
    expect(screen.getByText("R-280")).toBeTruthy();
    expect(screen.queryByText("P-012")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Run it" }));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "approve", comment: null, id: "P-012" });
  });

  it("没跑过的卡显示 P- 主键；legacy R 主键按 server 的 id_kind 灰显", () => {
    const { unmount } = render(<DebtCardItem item={card({ id: "P-007", display_id: "P-007", id_kind: "proposal" })} />);
    expect(screen.getByText("P-007").className).toBe("card-id");
    unmount();
    render(<DebtCardItem item={card({ id: "R-050", display_id: "R-050", id_kind: "legacy" })} />);
    expect(screen.getByText("R-050").className).toContain("card-id-legacy");
  });

  it("T2 typed-confirm 弹窗点名的是展示编号，wire 仍是主键", async () => {
    render(<DebtCardItem item={card({ tier: "T2", id: "P-012", work_id: "R-280", display_id: "R-280", id_kind: "work" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Details ▸" }));
    fireEvent.click(await screen.findByRole("button", { name: "Run it" }));
    expect(screen.getByText(/Approve R-280:/)).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("Type 确认 or go"), { target: { value: "go" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Approve" }).find((b) => b.closest("dialog"))!);
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "approve", comment: null, id: "P-012" });
  });
});
