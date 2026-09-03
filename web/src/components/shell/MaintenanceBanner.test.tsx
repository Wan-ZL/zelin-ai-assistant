// MaintenanceBanner（§65，D10）：board.maintenance → 「正在整理」/「今日整理：合并 N、清理 M（可撤销）」。
// 不弹系统通知；缺键 / 昨天的运行 / 全零 / 半路崩掉的陈旧 running 都闭嘴；server 连不上时闭嘴。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, resetStoreForTests, setConnection } from "../../store";
import type { Board, Maintenance } from "../../types";
import { describeMaintenance, MaintenanceBanner } from "./MaintenanceBanner";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchBoard: vi.fn(), fetchCard: vi.fn() };
});

const fetchBoardMock = vi.mocked(fetchBoard);
const en = (_zh: string, english: string) => english;
const NOW = new Date(2026, 8, 2, 10, 0, 0);
const nowS = NOW.getTime() / 1000;

function maint(over: Partial<Maintenance> = {}): Maintenance {
  return {
    phase: "idle",
    started_at: nowS - 600,
    last_run_at: nowS - 500,
    next_run_at: nowS + 80000,
    last_result: { merged: 1, trashed: 9, proposals: 3, summaries: 2, errors: 0 },
    ...over,
  };
}

function board(maintenance?: Maintenance): Board {
  return {
    generated_at: NOW.toISOString(),
    counts: {},
    needs_approval: [],
    running: [],
    needs_input: [],
    review: [],
    completed: [],
    debt: [],
    trash: [],
    ...(maintenance ? { maintenance } : {}),
  };
}

function renderBanner() {
  return render(
    <LanguageContext.Provider value="en">
      <MaintenanceBanner />
    </LanguageContext.Provider>,
  );
}

beforeEach(() => {
  resetStoreForTests();
  fetchBoardMock.mockReset();
});
afterEach(cleanup);

describe("describeMaintenance", () => {
  it("says nothing without the key", () => {
    expect(describeMaintenance(undefined, en, NOW)).toBeNull();
  });

  it("summarises today's run with the undo hint", () => {
    const d = describeMaintenance(maint(), en, NOW);
    expect(d?.kind).toBe("summary");
    expect(d?.message).toBe("Today's tidy-up: 1 merged · 9 cleaned up (undoable) · 3 proposed");
    const zh = describeMaintenance(maint(), (z) => z, NOW);
    expect(zh?.message).toBe("今日整理：合并 1、清理 9（可撤销）、提案 3");
  });

  it("stays quiet for yesterday's run or an all-zero run", () => {
    expect(describeMaintenance(maint({ last_run_at: nowS - 86400 * 1.5 }), en, NOW)).toBeNull();
    expect(describeMaintenance(maint({ last_result: { merged: 0, trashed: 0, proposals: 0 } }), en, NOW)).toBeNull();
    expect(describeMaintenance(maint({ last_run_at: null }), en, NOW)).toBeNull();
  });

  it("shows the running phase while fresh, not when it is a stale leftover", () => {
    const fresh = describeMaintenance(maint({ phase: "stale_sweep", started_at: nowS - 30 }), en, NOW);
    expect(fresh?.kind).toBe("running");
    expect(fresh?.message).toContain("sweeping stale cards");
    const unknown = describeMaintenance(maint({ phase: "whatever", started_at: nowS - 30 }), en, NOW);
    expect(unknown?.message).toContain("working");
    // started 3 h ago and never reached idle → the daemon died mid-run; fall through to the summary rule
    const stale = describeMaintenance(maint({ phase: "dedup", started_at: nowS - 3 * 3600 }), en, NOW);
    expect(stale?.kind).toBe("summary");
  });
});

describe("<MaintenanceBanner />", () => {
  it("renders today's summary as a status line with a trash link", async () => {
    fetchBoardMock.mockResolvedValue(board(maint({ last_run_at: Date.now() / 1000 - 60 })));
    await refreshBoard();
    renderBanner();
    const status = screen.getByRole("status");
    expect(status.getAttribute("data-kind")).toBe("summary");
    expect(status.textContent).toContain("1 merged");
    expect(screen.getByRole("link", { name: "Restore from the trash" }).getAttribute("href")).toContain("page=trash");
  });

  it("renders nothing without the key and nothing while reconnecting", async () => {
    fetchBoardMock.mockResolvedValue(board());
    await refreshBoard();
    const { unmount } = renderBanner();
    expect(screen.queryByRole("status")).toBeNull();
    unmount();
    fetchBoardMock.mockResolvedValue(board(maint({ last_run_at: Date.now() / 1000 - 60 })));
    await refreshBoard();
    setConnection("reconnecting");
    renderBanner();
    expect(screen.queryByRole("status")).toBeNull();
  });
});
