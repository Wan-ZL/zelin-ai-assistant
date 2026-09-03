// SelfImproveBanner（§64.4）：board.self_improve.paused → 横幅点名 PR / 受保护路径 + 「恢复通道」
// （POST /api/self-improve/resume 后刷新看板）；未暂停 / 老 daemon 无键 / 离线时不渲染。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, postSelfImproveResume } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, resetStoreForTests, setConnection } from "../../store";
import type { Board } from "../../types";
import { describeSelfImprove, SelfImproveBanner } from "./SelfImproveBanner";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchBoard: vi.fn(), postSelfImproveResume: vi.fn() };
});

const fetchBoardMock = vi.mocked(fetchBoard);
const resumeMock = vi.mocked(postSelfImproveResume);
const en = (_zh: string, english: string) => english;

function board(selfImprove?: Board["self_improve"]): Board {
  return {
    generated_at: "2026-09-02T10:00:00Z",
    counts: {},
    needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [],
    ...(selfImprove ? { self_improve: selfImprove } : {}),
  } as unknown as Board;
}

const paused = {
  enabled: true, paused: true, paused_reason: "sensitive_paths", paused_pr: 123,
  paused_pr_url: "https://github.com/o/r/pull/123", paused_paths: ["act/lib/policy.py"],
  paused_at: "2026-09-02T09:00:00Z",
};

function renderBanner() {
  return render(
    <LanguageContext.Provider value="en">
      <SelfImproveBanner />
    </LanguageContext.Provider>,
  );
}

beforeEach(() => {
  resetStoreForTests();
  fetchBoardMock.mockReset();
  resumeMock.mockReset();
});
afterEach(cleanup);

describe("describeSelfImprove", () => {
  it("says nothing when absent, disabled or not paused", () => {
    expect(describeSelfImprove(undefined, en)).toBeNull();
    expect(describeSelfImprove({ enabled: true, paused: false }, en)).toBeNull();
    expect(describeSelfImprove({ enabled: false, paused: false }, en)).toBeNull();
  });

  it("names the PR, the protected paths and both exits when paused", () => {
    const d = describeSelfImprove(paused, en);
    expect(d?.title).toBe("Self-improve lane paused");
    expect(d?.detail).toContain("PR #123");
    expect(d?.detail).toContain("act/lib/policy.py");
    expect(d?.detail).toContain("needs-owner-eyes");
    expect(d?.detail).toContain("merge/close");
  });

  it("degrades honestly without pr / paths", () => {
    const d = describeSelfImprove({ enabled: true, paused: true }, en);
    expect(d?.detail).toContain("(unknown PR)");
    expect(d?.detail).toContain("protected paths");
  });
});

describe("<SelfImproveBanner>", () => {
  it("renders nothing without a board, on old daemons and when not paused", async () => {
    renderBanner();
    expect(screen.queryByRole("alert")).toBeNull();
    fetchBoardMock.mockResolvedValue(board());
    await refreshBoard();
    expect(screen.queryByRole("alert")).toBeNull();
    fetchBoardMock.mockResolvedValue(board({ enabled: true, paused: false }));
    await refreshBoard();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the paused banner with PR link and resumes on click", async () => {
    fetchBoardMock.mockResolvedValue(board(paused));
    await refreshBoard();
    renderBanner();
    const alert = screen.getByRole("alert");
    expect(alert.getAttribute("data-lane")).toBe("self_improve");
    expect(alert.className).toContain("is-warning");
    expect(screen.getByText("Self-improve lane paused")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open PR" }).getAttribute("href")).toBe(paused.paused_pr_url);

    resumeMock.mockResolvedValue({ ok: true, paused: false, was_paused: true });
    fetchBoardMock.mockResolvedValue(board({ enabled: true, paused: false }));
    fireEvent.click(screen.getByRole("button", { name: "Resume lane" }));
    await waitFor(() => expect(resumeMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("yields to the offline banner while reconnecting", async () => {
    fetchBoardMock.mockResolvedValue(board(paused));
    await refreshBoard();
    setConnection("reconnecting");
    renderBanner();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
