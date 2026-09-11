// 设置 · 录制区里的磁盘块（CONTRACT §71，issue #28）：占用分项 / 增长估计 / prune 回执 / 保留期旋钮。
// 钉住的行为：两类占用分开显示；growth 为 null 时那一行不渲染（没观察到就不说）；prune 的
// never / stale / unreadable 三种都进 alert（停掉的清理与「没东西可删」不许长一个样）；
// 保存只发一个键、按钮在没改动时是灰的；scanning 态显示进度句并轮询。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchStorageSettings, putStorageSettings } from "../../api";
import { LanguageContext } from "../../i18n";
import type { StorageSettings } from "../../types";
import { formatBytes, StorageSection } from "./StorageSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchStorageSettings: vi.fn(), putStorageSettings: vi.fn() };
});

const GB = 1024 ** 3;

function snap(over: Partial<StorageSettings> = {}): StorageSettings {
  return {
    media_retention_minutes: 60,
    source: "default",
    bounds: { min: 5, max: 525600, default: 60 },
    usage: {
      state: "ready", dir: "/Users/z/.screenpipe",
      bytes: { media: 12 * GB, index: 40 * 1024 * 1024, other: 0, total: 12 * GB + 40 * 1024 * 1024 },
      files: { media: 90000, index: 3, other: 0 },
      truncated: false, scanned_at: "2026-09-11T04:00:00Z", scan_seconds: 1.2,
      stale: false, scanning: false,
    },
    growth: { basis: "samples", days: 12, bytes_per_month: 30 * GB },
    prune: {
      ran_at: "2026-09-11T04:00:00Z", state: "ok", deleted_files: 42,
      deleted_bytes: 500 * 1024 * 1024, retention_minutes: 60, age_seconds: 120, stale: false,
    },
    ...over,
  };
}

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

beforeEach(() => {
  vi.mocked(fetchStorageSettings).mockReset();
  vi.mocked(putStorageSettings).mockReset();
});
afterEach(cleanup);

describe("formatBytes", () => {
  it("reads like a human at every magnitude", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(999)).toBe("999 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(23 * 1024 ** 3)).toBe("23 GB");
    expect(formatBytes(-5)).toBe("0 B");
  });
});

describe("StorageSection", () => {
  it("splits media from the text index and shows the monthly estimate", async () => {
    vi.mocked(fetchStorageSettings).mockResolvedValue(snap());
    renderEn(<StorageSection />);
    const total = await screen.findByText(/total/);
    expect(total.textContent).toContain("12 GB");
    expect(screen.getByText(/Raw media/).textContent).toContain("12 GB");
    expect(screen.getByText(/Text index/).textContent).toContain("40 MB");
    expect(screen.getByText(/Text index/).textContent).toContain("never touches it");
    expect(screen.getByText("About 30 GB/month (measured over the last 12 days)")).toBeTruthy();
  });

  it("says nothing about growth when the server cannot estimate it", async () => {
    vi.mocked(fetchStorageSettings).mockResolvedValue(snap({ growth: null }));
    renderEn(<StorageSection />);
    await screen.findByText(/total/);
    expect(screen.queryByText(/per month/i)).toBeNull();
    expect(screen.queryByText(/About .*\/month/)).toBeNull();
  });

  it("a prune that never ran is an alert, not a quiet line", async () => {
    vi.mocked(fetchStorageSettings).mockResolvedValue(snap({
      prune: { ran_at: null, state: "never", deleted_files: null, deleted_bytes: null, retention_minutes: null, age_seconds: null, stale: true },
    }));
    renderEn(<StorageSection />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("The prune job has never left a receipt — it may never have run.");
  });

  it("a stale prune names the gap", async () => {
    vi.mocked(fetchStorageSettings).mockResolvedValue(snap({
      prune: { ran_at: "2026-09-10T04:00:00Z", state: "ok", deleted_files: 0, deleted_bytes: 0, retention_minutes: 60, age_seconds: 90000, stale: true },
    }));
    renderEn(<StorageSection />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("over 3 hours ago");
  });

  it("an unreadable data folder does not read as a clean run", async () => {
    vi.mocked(fetchStorageSettings).mockResolvedValue(snap({
      prune: { ran_at: "2026-09-11T04:00:00Z", state: "unreadable", deleted_files: 0, deleted_bytes: 0, retention_minutes: 60, age_seconds: 60, stale: false },
    }));
    renderEn(<StorageSection />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("could not read the recording folder");
  });

  it("a healthy prune that deleted nothing is a quiet line", async () => {
    vi.mocked(fetchStorageSettings).mockResolvedValue(snap({
      prune: { ran_at: "2026-09-11T04:00:00Z", state: "ok", deleted_files: 0, deleted_bytes: 0, retention_minutes: 60, age_seconds: 60, stale: false },
    }));
    renderEn(<StorageSection />);
    await screen.findByText("Last prune 2026-09-11T04:00:00Z: nothing was past the retention window.");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("saves exactly one key and only when the value changed", async () => {
    vi.mocked(fetchStorageSettings).mockResolvedValue(snap());
    vi.mocked(putStorageSettings).mockResolvedValue(snap({ media_retention_minutes: 180, source: "override" }));
    renderEn(<StorageSection />);
    const input = await screen.findByLabelText("Disk usage and retention");
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(input, { target: { value: "180" } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(putStorageSettings).toHaveBeenCalledWith({ media_retention_minutes: 180 }));
    await screen.findByText("Saved: raw media kept for 180 minutes; effective on the next prune round (every 30 min).");
  });

  it("shows the server's own 400 sentence verbatim", async () => {
    vi.mocked(fetchStorageSettings).mockResolvedValue(snap());
    vi.mocked(putStorageSettings).mockRejectedValue(new Error("media_retention_minutes must be an integer"));
    renderEn(<StorageSection />);
    const input = await screen.findByLabelText("Disk usage and retention");
    fireEvent.change(input, { target: { value: "soon" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("media_retention_minutes must be an integer");
  });

  it("while the server is still measuring, it says so and polls", async () => {
    const scanning = snap({
      usage: { state: "scanning", dir: "/Users/z/.screenpipe", bytes: null, files: null, truncated: false, scanned_at: null, scan_seconds: null, stale: false, scanning: true },
      growth: null,
    });
    vi.mocked(fetchStorageSettings).mockResolvedValueOnce(scanning).mockResolvedValue(snap());
    renderEn(<StorageSection />);
    await screen.findByText("Measuring disk usage…");
    await screen.findByText(/total/, undefined, { timeout: 5000 });
    expect(vi.mocked(fetchStorageSettings).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("an empty machine says there is no data yet instead of 0 B", async () => {
    vi.mocked(fetchStorageSettings).mockResolvedValue(snap({
      usage: { state: "missing", dir: "/Users/z/.screenpipe", bytes: null, files: null, truncated: false, scanned_at: null, scan_seconds: null, stale: false, scanning: false },
      growth: null,
    }));
    renderEn(<StorageSection />);
    await screen.findByText("No recording data yet (/Users/z/.screenpipe does not exist).");
  });
});
