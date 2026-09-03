// 待验收卡的 §65.3 交付核验章：verified → PR 章链接（含 draft）；未通过 → 红章带原因 token；
// 非 self_improve 卡（无 delivery）不渲染任何章。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests } from "../../store";
import type { ReviewCard as ReviewRow } from "../../types";
import { ReviewCard } from "./ReviewCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
}));

beforeEach(() => resetStoreForTests());
afterEach(cleanup);

function card(extra: Partial<ReviewRow> = {}): ReviewRow {
  return { id: "P-7", name: "lane 卡", dod: [], delivery_mode: "repo", ...extra } as ReviewRow;
}

describe("review card delivery chip", () => {
  it("verified draft PR renders a linked success chip", () => {
    render(<ReviewCard card={card({ delivery: { verified: true, pr_number: 123, pr_draft: true,
      pr_url: "https://github.com/o/r/pull/123" } })} />);
    const chip = screen.getByText("PR #123 · draft");
    expect(chip.className).toContain("chip-success");
    expect(chip.getAttribute("href")).toBe("https://github.com/o/r/pull/123");
    expect(chip.getAttribute("data-delivery")).toBe("verified");
  });

  it("unverified delivery renders the reason token in red", () => {
    render(<ReviewCard card={card({ interrupted: true,
      delivery: { verified: false, reason: "pr_not_draft", pr_number: 124 } })} />);
    const chip = screen.getByText("PR unverified: pr_not_draft");
    expect(chip.className).toContain("chip-danger");
    expect(chip.getAttribute("data-delivery")).toBe("unverified");
    expect(screen.getByText("Interrupted")).toBeTruthy();
  });

  it("cards without delivery render no chip", () => {
    render(<ReviewCard card={card()} />);
    expect(document.querySelector("[data-delivery]")).toBeNull();
  });
});
