// 「录制数据与磁盘」状态行（CONTRACT §71.1；issue #28）：GET /api/screenpipe/disk 的 computing → ready 轮询、数字格式化、
// 增长估算的依据句、上次清理回执的一句话、备份文件告示（只报不删）、刷新按钮 = ?refresh=1、拉取失败只显示一句不炸。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchScreenpipeDisk } from "../../api";
import { LanguageContext } from "../../i18n";
import type { ScreenpipeDisk } from "../../types";
import { dayOf, formatBytes, growthText, pruneText, StorageStatus } from "./StorageStatus";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchScreenpipeDisk: vi.fn() };
});

const text = (zh: string, en: string) => en;

const ready: ScreenpipeDisk = {
  state: "ready", computed_at: "2026-09-07T10:00:00Z", refreshing: false, root: "/Users/demo/.screenpipe", root_exists: true,
  total_bytes: 43_000_000_000, db_bytes: 10_700_000_000, backup_bytes: 32_000_000_000, log_bytes: 30_000_000, media_bytes: 0,
  other_bytes: 600_000, file_count: 120, backups: [{ name: "db.sqlite.bak-20260604", bytes: 32_000_000_000 }],
  db_reclaimable_bytes: 1_200_000_000, oldest_frame_ts: "2026-04-16T11:54:01.723008+00:00",
  newest_frame_ts: "2026-09-05T03:04:12.820268+00:00", db_error: null,
  growth: { bytes_per_month: 2_300_000_000, basis: "lifetime", span_days: 141.6, samples: 1 },
  retention_days: 0, last_prune: null,
};
const computing: ScreenpipeDisk = {
  ...ready, state: "computing", computed_at: null, refreshing: true, total_bytes: null, db_bytes: null, backup_bytes: null,
  log_bytes: null, media_bytes: null, other_bytes: null, file_count: null, backups: [], db_reclaimable_bytes: null,
  oldest_frame_ts: null, newest_frame_ts: null, growth: { bytes_per_month: null, basis: null, span_days: null, samples: 0 },
};

function renderEn() {
  return render(<LanguageContext.Provider value="en"><StorageStatus /></LanguageContext.Provider>);
}

beforeEach(() => {
  vi.mocked(fetchScreenpipeDisk).mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("formatting helpers", () => {
  it("formatBytes uses decimal units and — for unknown", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(43_000_000_000)).toBe("43 GB");
    expect(formatBytes(1_250_000_000)).toBe("1.3 GB");
    expect(formatBytes(30_000_000)).toBe("30 MB");
    expect(formatBytes(4_200)).toBe("4 KB");
    expect(formatBytes(0)).toBe("0 KB");
  });

  it("growthText names its basis and says so when there is nothing to go on", () => {
    expect(growthText({ bytes_per_month: 2_300_000_000, basis: "lifetime", span_days: 141.6, samples: 1 }, text))
      .toBe("≈ 2.3 GB / month (average over all 141.6 recorded days)");
    expect(growthText({ bytes_per_month: 900_000_000, basis: "samples", span_days: 3.5, samples: 9 }, text))
      .toBe("≈ 900 MB / month (from the last 3.5 days of samples)");
    expect(growthText({ bytes_per_month: -500_000_000, basis: "samples", span_days: 2, samples: 4 }, text))
      .toBe("≈ −500 MB / month (from the last 2 days of samples)");
    expect(growthText({ bytes_per_month: null, basis: null, span_days: null, samples: 1 }, text))
      .toBe("Not enough samples yet (needs two measurements ≥ 1 day apart)");
  });

  it("dayOf and pruneText", () => {
    expect(dayOf("2026-04-16T11:54:01.723008+00:00")).toBe("2026-04-16");
    expect(dayOf(null)).toBe("—");
    expect(pruneText(null, text)).toBe("Not run yet");
    expect(pruneText({ ran_at: "2026-09-07T04:00:12Z", skipped: "retention_off" }, text))
      .toBe("2026-09-07 04:00 UTC retention off (0 = keep forever), nothing deleted");
    expect(pruneText({ ran_at: "2026-09-07T04:00:12Z", deleted_frames: 120, deleted_audio: 7, budget_exhausted: true }, text))
      .toBe("2026-09-07 04:00 UTC deleted 120 frames / 7 transcripts; time budget used up, continues next round");
    expect(pruneText({ ran_at: "2026-09-07T04:00:12Z", error: "DatabaseError: locked" }, text))
      .toBe("2026-09-07 04:00 UTC failed: DatabaseError: locked");
    expect(pruneText({ ran_at: "2026-09-07T04:00:12Z", skipped: "no_db" }, text)).toBe("2026-09-07 04:00 UTC skipped (no_db)");
  });
});

describe("StorageStatus", () => {
  it("polls while the server is still measuring, then shows the numbers, the backup notice and the prune line", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(fetchScreenpipeDisk).mockResolvedValueOnce(computing).mockResolvedValueOnce(ready);
    renderEn();
    await waitFor(() => expect(screen.getByTestId("storage-total").textContent).toBe("Measuring…"));
    expect((screen.getByRole("button", { name: "Refresh" }) as HTMLButtonElement).disabled).toBe(true);
    await vi.advanceTimersByTimeAsync(1600);
    await waitFor(() => expect(screen.getByTestId("storage-total").textContent).toBe("43 GB"));
    expect(vi.mocked(fetchScreenpipeDisk).mock.calls).toEqual([[false], [false]]);
    expect(screen.getByText("~/.screenpipe")).toBeTruthy();
    expect(screen.getByText("database 11 GB · backups 32 GB · logs 30 MB · media 0 KB")).toBeTruthy();
    expect(screen.getByText("of which 1.2 GB inside the database is reusable (pruned, waiting for new data)")).toBeTruthy();
    expect(screen.getByTestId("storage-growth").textContent).toBe("≈ 2.3 GB / month (average over all 141.6 recorded days)");
    expect(screen.getByText("oldest 2026-04-16 · newest 2026-09-05")).toBeTruthy();
    expect(screen.getByTestId("storage-prune").textContent).toBe("Not run yet");
    const notice = screen.getByRole("status");
    expect(notice.textContent).toContain("Found 1 database backup file(s), 32 GB in total:");
    expect(notice.textContent).toContain("db.sqlite.bak-20260604（32 GB）");
    expect(notice.textContent).toContain("nothing here deletes them");
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();
    expect((screen.getByRole("button", { name: "Refresh" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("refresh asks the server to recompute (?refresh=1) and shows the re-measuring hint while it runs", async () => {
    vi.mocked(fetchScreenpipeDisk).mockResolvedValueOnce(ready).mockResolvedValueOnce({ ...ready, refreshing: true });
    renderEn();
    await waitFor(() => expect(screen.getByTestId("storage-total").textContent).toBe("43 GB"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.getByText("Re-measuring…")).toBeTruthy());
    expect(vi.mocked(fetchScreenpipeDisk).mock.calls).toEqual([[false], [true]]);
    expect(screen.getByTestId("storage-total").textContent).toBe("43 GB"); // 旧数字照旧可读
  });

  it("a failed fetch shows one plain error line instead of crashing", async () => {
    vi.mocked(fetchScreenpipeDisk).mockRejectedValue(new Error("fetchScreenpipeDisk: not stubbed here"));
    renderEn();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("not stubbed here");
  });

  it("state=error from the background job and a broken db are both reported honestly", async () => {
    vi.mocked(fetchScreenpipeDisk).mockResolvedValue({ ...ready, state: "error", error: "PermissionError: [Errno 13]", db_error: "database is locked",
      backups: [], growth: { bytes_per_month: null, basis: null, span_days: null, samples: 0 } });
    renderEn();
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBe(2));
    const alerts = screen.getAllByRole("alert").map((el) => el.textContent);
    expect(alerts).toContain("Measurement failed: PermissionError: [Errno 13]");
    expect(alerts).toContain("Database could not be read: database is locked");
    expect(screen.getByTestId("storage-growth").textContent).toBe("Not enough samples yet (needs two measurements ≥ 1 day apart)");
    expect(screen.queryByRole("status")).toBeNull();
  });
});
