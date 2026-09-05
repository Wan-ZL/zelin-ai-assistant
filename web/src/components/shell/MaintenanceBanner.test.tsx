// MaintenanceBanner（§70，D10）：board.maintenance → 「正在整理」/「今日整理：合并 N、清理 M（可撤销）」。
// 不弹系统通知；缺键 / 昨天的运行 / 全零 / 半路崩掉的陈旧 running 都闭嘴；server 连不上时闭嘴。
// D33：last_result.advisories → 「系统自检 N 条」按钮 + 可展开的列表；自检类信号不铸卡，只在这里可见。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, resetStoreForTests, setConnection } from "../../store";
import type { Board, Maintenance, MaintenanceAdvisory } from "../../types";
import { advisoriesOf, describeMaintenance, MaintenanceBanner } from "./MaintenanceBanner";

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

const ADVISORIES: MaintenanceAdvisory[] = [
  { kind: "stuck_dispatch", text: "派发卡死：3 张已批卡发不出去（claude_blind） — 根因 claude_blind", ref: "",
    fingerprint: "stuck_dispatch:claude_blind", first_seen: "2026-08-30" },
  { kind: "doctor_fail", text: "doctor 红灯：launchd claude — TCC", ref: "claude_blind",
    fingerprint: "doctor_fail:launchd claude", first_seen: "2026-09-02" },
];

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
    expect(d?.trashed).toBe(true);
    const zh = describeMaintenance(maint(), (z) => z, NOW);
    expect(zh?.message).toBe("今日整理：合并 1、清理 9（可撤销）、提案 3");
    // merges also send the old cards to the trash → still restorable; proposals alone do not
    expect(describeMaintenance(maint({ last_result: { merged: 2, trashed: 0, proposals: 0 } }), en, NOW)?.trashed).toBe(true);
    expect(describeMaintenance(maint({ last_result: { merged: 0, trashed: 0, proposals: 1 } }), en, NOW)?.trashed).toBe(false);
  });

  it("stays quiet for yesterday's run or an all-zero run", () => {
    expect(describeMaintenance(maint({ last_run_at: nowS - 86400 * 1.5 }), en, NOW)).toBeNull();
    expect(describeMaintenance(maint({ last_result: { merged: 0, trashed: 0, proposals: 0 } }), en, NOW)).toBeNull();
    expect(describeMaintenance(maint({ last_run_at: null }), en, NOW)).toBeNull();
  });

  it("speaks for advisories alone and mirrors the wire rows verbatim (D33)", () => {
    const d = describeMaintenance(maint({ last_result: { merged: 0, trashed: 0, proposals: 0, advisories: ADVISORIES } }), en, NOW);
    expect(d?.kind).toBe("summary");
    expect(d?.advisories).toEqual(ADVISORIES);
    // nothing merged / cleaned / proposed → say so instead of three zeros, and promise no undo
    expect(d?.message).toBe("Today's tidy-up: nothing to change");
    expect(d?.trashed).toBe(false);
    const zh = describeMaintenance(maint({ last_result: { merged: 0, trashed: 0, proposals: 0, advisories: ADVISORIES } }), (z) => z, NOW);
    expect(zh?.message).toBe("今日整理：看板无变动");
    expect(describeMaintenance(maint(), en, NOW)?.advisories).toEqual([]);
    // running says nothing about advisories; garbage rows are dropped, not rendered
    expect(describeMaintenance(maint({ phase: "dedup", started_at: nowS - 30 }), en, NOW)?.advisories).toEqual([]);
    const junk = { merged: 0, trashed: 0, proposals: 0, advisories: [null, "x", { kind: "k" }, { kind: "k", text: "" }, ADVISORIES[1]] };
    expect(advisoriesOf(maint({ last_result: junk as unknown as Maintenance["last_result"] }))).toEqual([ADVISORIES[1]]);
    expect(advisoriesOf(maint({ last_result: { merged: 1, trashed: 0, proposals: 0, advisories: "no" as unknown as [] } }))).toEqual([]);
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

  it("collapses advisories into a toggle and lists them on demand, without a notification (D33)", async () => {
    fetchBoardMock.mockResolvedValue(board(maint({ last_run_at: Date.now() / 1000 - 60,
      last_result: { merged: 0, trashed: 0, proposals: 0, advisories: ADVISORIES } })));
    await refreshBoard();
    renderBanner();
    const status = screen.getByRole("status");
    expect(status.textContent).toContain("nothing to change");
    expect(status.textContent).not.toContain("0 proposed");
    expect(screen.queryByRole("link")).toBeNull();                 // nothing went to the trash → no undo link
    const toggle = screen.getByRole("button", { name: /2 self-check notes/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("list")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(status.className).toContain("is-open");
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("派发卡死");
    expect(items[0].textContent).toContain("since 2026-08-30");
    expect(items[1].textContent).toContain("doctor_fail");
    fireEvent.click(toggle);
    expect(screen.queryByRole("list")).toBeNull();
    expect(status.className).not.toContain("is-open");
  });

  it("keeps the trash link next to the toggle when something was actually trashed", async () => {
    fetchBoardMock.mockResolvedValue(board(maint({ last_run_at: Date.now() / 1000 - 60,
      last_result: { merged: 0, trashed: 2, proposals: 0, advisories: ADVISORIES } })));
    await refreshBoard();
    renderBanner();
    expect(screen.getByRole("status").textContent).toContain("2 cleaned up (undoable)");
    expect(screen.getByRole("button", { name: /2 self-check notes/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Restore from the trash" })).toBeTruthy();
  });

  it("shows no toggle when there are no advisories", async () => {
    fetchBoardMock.mockResolvedValue(board(maint({ last_run_at: Date.now() / 1000 - 60 })));
    await refreshBoard();
    renderBanner();
    expect(screen.queryByRole("button")).toBeNull();
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
