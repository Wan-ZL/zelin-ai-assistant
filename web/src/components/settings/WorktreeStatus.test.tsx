// 开发者区「隔离工作树（worktree）」行（CONTRACT §75.4；issue #315）：GET /api/worktrees 的 computing → ready、
// 条数与占用的显示、没有可清理的就禁用按钮、「清理」= POST /api/worktrees/cleanup 并复述回执、拉取失败只显示一句不炸。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchWorktrees, postWorktreesCleanup } from "../../api";
import { LanguageContext } from "../../i18n";
import type { WorktreeInventory } from "../../types";
import { cleanupText, WorktreeStatus } from "./WorktreeStatus";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchWorktrees: vi.fn(), postWorktreesCleanup: vi.fn() };
});

const text = (zh: string, en: string) => en;

const ready: WorktreeInventory = {
  state: "ready", ok: true, scanned_at: "2026-09-15T04:00:00Z", refreshing: false,
  worktrees: 30, removable: 12, bytes: 12_400_000_000, bytes_partial: false, truncated: false,
  stale_days: 14, repos: [],
};
const computing: WorktreeInventory = {
  ...ready, state: "computing", scanned_at: null, refreshing: true, worktrees: null,
  removable: null, bytes: null,
};

function renderEn() {
  return render(<LanguageContext.Provider value="en"><WorktreeStatus /></LanguageContext.Provider>);
}

beforeEach(() => {
  vi.mocked(fetchWorktrees).mockReset();
  vi.mocked(postWorktreesCleanup).mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("cleanupText", () => {
  it("says what went and what stayed", () => {
    expect(cleanupText({ ok: true, removed: [
      { path: "/a", branch: "feat/a", reason: "merged", branch_deleted: true, error: null },
      { path: "/b", branch: "", reason: "stale", branch_deleted: false, error: null },
    ], failed: [], skipped: { dirty: 2, live: 1 } }, text))
      .toBe("Removed 2 worktrees and 1 local branches; kept 3 (dirty / locked / in flight / unpushed)");
  });

  it("counts the ones git refused", () => {
    expect(cleanupText({ ok: true, removed: [], failed: [
      { path: "/a", branch: "feat/a", reason: "merged", branch_deleted: false, error: "locked" },
    ], skipped: {} }, text))
      .toContain("1 could not be removed");
  });

  it("repeats the server's failure sentence verbatim", () => {
    expect(cleanupText({ ok: false, removed: [], message: "no python" }, text))
      .toBe("Cleanup failed: no python");
  });
});

describe("WorktreeStatus", () => {
  it("shows the count and the size once the server is done measuring", async () => {
    vi.mocked(fetchWorktrees).mockResolvedValue(ready);
    renderEn();
    await waitFor(() => expect(screen.getByTestId("worktree-count").textContent).toBe("30 · 12 GB"));
    expect(screen.getByText(/12 can be reclaimed now/)).toBeTruthy();
  });

  it("says it is still measuring while the server computes and disables the button", async () => {
    vi.mocked(fetchWorktrees).mockResolvedValue(computing);
    renderEn();
    await waitFor(() => expect(screen.getByTestId("worktree-count").textContent).toBe("Measuring…"));
    expect(screen.getByRole("button", { name: "Clean up" }).hasAttribute("disabled")).toBe(true);
  });

  it("disables the button when there is nothing to reclaim", async () => {
    vi.mocked(fetchWorktrees).mockResolvedValue({ ...ready, removable: 0 });
    renderEn();
    await waitFor(() => expect(screen.getByTestId("worktree-count").textContent).toBe("30 · 12 GB"));
    expect(screen.getByRole("button", { name: "Clean up" }).hasAttribute("disabled")).toBe(true);
  });

  it("cleans up on click and repeats the receipt", async () => {
    vi.mocked(fetchWorktrees).mockResolvedValue(ready);
    vi.mocked(postWorktreesCleanup).mockResolvedValue({
      ok: true, removed: [{ path: "/a", branch: "feat/a", reason: "merged", branch_deleted: true, error: null }],
      failed: [], skipped: { dirty: 1 },
    });
    renderEn();
    await waitFor(() => expect(screen.getByRole("button", { name: "Clean up" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Clean up" }));
    await waitFor(() => expect(screen.getByText(/Removed 1 worktrees and 1 local branches/)).toBeTruthy());
    expect(vi.mocked(postWorktreesCleanup)).toHaveBeenCalled();
  });

  it("asks the server to re-measure when refreshed", async () => {
    vi.mocked(fetchWorktrees).mockResolvedValue(ready);
    renderEn();
    await waitFor(() => expect(screen.getByTestId("worktree-count").textContent).toBe("30 · 12 GB"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(vi.mocked(fetchWorktrees)).toHaveBeenCalledWith(true));
  });

  it("shows one sentence and no crash when the fetch fails", async () => {
    vi.mocked(fetchWorktrees).mockRejectedValue(new Error("offline"));
    renderEn();
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("offline"));
  });

  it("admits when the size could not be measured in full", async () => {
    vi.mocked(fetchWorktrees).mockResolvedValue({ ...ready, bytes: null, bytes_partial: true });
    renderEn();
    await waitFor(() => expect(screen.getByTestId("worktree-count").textContent).toContain("size partly unmeasured"));
  });
});
