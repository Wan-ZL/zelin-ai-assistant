// 提案卡面两处文案 / 格式（原生 Cards.swift）：
//   · 费用章 = Self.money（:1374-1377）：整数不带小数「$12」，否则两位「$0.50」（此前 `${cost_usd}` 会渲染 $0.5）；
//     show_cost=false 不出章；
//   · 回锅章旁的大白话小字「你之前验收过这件事，来了新信息」（:1191-1193），与章并排、同色 warning；
//     没回锅时既没章也没小字。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { ApprovalCard } from "../../types";
import { ProposalCard } from "./ProposalCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  fetchCard: vi.fn(async (id: string) => ({ id })),
}));

beforeEach(() => resetStoreForTests());
afterEach(cleanup);

function card(over: Partial<ApprovalCard> = {}): ApprovalCard {
  return {
    id: "P-002",
    title: "t",
    summary: "摘要",
    tier: "T1",
    show_cost: false,
    processing: false,
    sources: [],
    plan: [],
    dod: [],
    ...over,
  };
}

function chips(): string[] {
  return Array.from(document.querySelectorAll(".card-badges .chip")).map((el) => el.textContent ?? "");
}

describe("ProposalCard cost chip (native money format)", () => {
  it("整数 → $12；小数 → 两位 $0.50 / $3.50", () => {
    const { unmount } = render(<ProposalCard card={card({ show_cost: true, cost_usd: 12, cost_state: "estimated" })} />);
    expect(chips()).toContain("$12");
    unmount();
    render(<ProposalCard card={card({ show_cost: true, cost_usd: 0.5, cost_state: "estimated" })} />);
    expect(chips()).toContain("$0.50");
    expect(chips()).not.toContain("$0.5");
    cleanup();
    render(<ProposalCard card={card({ show_cost: true, cost_usd: 3.5 })} />);
    expect(chips()).toContain("$3.50");
  });

  it("show_cost=false 不出费用章（阈值以下 / 成本未知）", () => {
    render(<ProposalCard card={card({ show_cost: false, cost_usd: 85 })} />);
    expect(chips().some((c) => c.startsWith("$"))).toBe(false);
  });
});

describe("ProposalCard reraised subtext (native reraisedBadge)", () => {
  it("回锅：章「↩︎ 回锅 · Returned」旁并排一句「你之前验收过这件事，来了新信息」（zh）", () => {
    render(<LanguageContext.Provider value="zh"><ProposalCard card={card({ reraised: true, reraised_note: "又有人问" })} /></LanguageContext.Provider>);
    const chip = screen.getByText("↩︎ 回锅 · Returned");
    const note = screen.getByText("你之前验收过这件事，来了新信息");
    expect(chip.className).toContain("chip-warning");
    expect(note.className).toContain("card-meta-text");
    expect(note.className).toContain("is-warning");
    expect(chip.parentElement).toBe(note.parentElement); // 同一章行，并排
    expect(screen.getByText("又有人问")).toBeTruthy();   // 原生 returnedNote「新增：」仍在
  });

  it("en 文案「You accepted this before — new info arrived」；没回锅时章与小字都不出", () => {
    const { unmount } = render(<ProposalCard card={card({ reraised: true })} />);
    expect(screen.getByText("↩︎ Returned")).toBeTruthy();
    expect(screen.getByText("You accepted this before — new info arrived")).toBeTruthy();
    unmount();
    render(<ProposalCard card={card()} />);
    expect(screen.queryByText("↩︎ Returned")).toBeNull();
    expect(screen.queryByText("You accepted this before — new info arrived")).toBeNull();
  });
});
