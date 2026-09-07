// 一键修复的下场 → analytics `pipeline_repair_result{ok}`（CONTRACT §16 追记；owner 决策 D48；原生 Doctor.swift:379 在最终
// phase 落定时发一条）：POST 被拒 → ok:false；15 轮没转好 → ok:false；恢复 → ok:true。每次修复恰好一条、在下场落定那一刻发；
// 点按钮 / 轮询中途不发；running 期间重复点不重复发。假定时器驱动轮询（同 PipelineBanner.repair.test.tsx）。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchHealth, postRepairActd } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import { trackEvent } from "../../telemetry";
import type { HealthSnapshot } from "../../types";
import { RepairButton } from "./PipelineBanner";
import { REPAIR_POLL_MS, REPAIR_POLL_ROUNDS } from "./repairActd";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchHealth: vi.fn(), postRepairActd: vi.fn(), postAiFixDoctor: vi.fn() };
});

vi.mock("../../telemetry", () => ({
  markTelemetryConsentShown: vi.fn(),
  trackEvent: vi.fn(),
}));

const fetchHealthMock = vi.mocked(fetchHealth);
const repairMock = vi.mocked(postRepairActd);
const trackMock = vi.mocked(trackEvent);

function snap(overrides: Partial<HealthSnapshot> = {}): HealthSnapshot {
  return {
    verdict: "ok",
    heartbeat: { age_s: 4, phase: "idle", pid: 4242, interval: 10, stale_after_s: 90, stale: false },
    dashboard: { generated_at: "2026-09-01T08:00:00Z", age_s: 5, stale: false },
    loop_health: { consecutive_failures: 0, last_error: null },
    checked_at: "2026-09-01T08:00:05Z",
    ...overrides,
  };
}

const stalled = snap({ verdict: "stalled", heartbeat: { age_s: 9000, phase: "dispatch", pid: 1, interval: 10, stale_after_s: 90, stale: true } });

function renderEn() {
  return render(<LanguageContext.Provider value="en"><RepairButton verdict="stalled" /></LanguageContext.Provider>);
}

async function pollRounds(n: number) {
  for (let i = 0; i < n; i += 1) {
    await act(async () => { await vi.advanceTimersByTimeAsync(REPAIR_POLL_MS); });
  }
}

beforeEach(() => {
  resetStoreForTests();
  fetchHealthMock.mockReset();
  repairMock.mockReset();
  trackMock.mockReset().mockResolvedValue(undefined);
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("pipeline_repair_result{ok} (D48)", () => {
  it("recovered on the 2nd poll → exactly one event with ok:true, sent when the verdict lands", async () => {
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
    fetchHealthMock.mockResolvedValue(stalled);
    renderEn();
    fireEvent.click(screen.getByRole("button", { name: "Fix now" }));
    await act(async () => { await Promise.resolve(); });
    expect(trackMock).not.toHaveBeenCalled(); // 点了、POST 回了——还没有下场
    await pollRounds(1);
    expect(trackMock).not.toHaveBeenCalled();
    fetchHealthMock.mockResolvedValue(snap());
    await pollRounds(1);
    expect(screen.getByText("Recovered ✓ data is updating again")).toBeTruthy();
    expect(trackMock).toHaveBeenCalledTimes(1);
    expect(trackMock).toHaveBeenCalledWith("pipeline_repair_result", { ok: true });
  });

  it("15 rounds without recovery → one event with ok:false", async () => {
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
    fetchHealthMock.mockResolvedValue(stalled);
    renderEn();
    fireEvent.click(screen.getByRole("button", { name: "Fix now" }));
    await act(async () => { await Promise.resolve(); });
    // running 期间再点：不重复 POST、也不会多发事件
    fireEvent.click(screen.getByRole("button", { name: "Repairing…" }));
    await pollRounds(REPAIR_POLL_ROUNDS - 1);
    expect(trackMock).not.toHaveBeenCalled();
    await pollRounds(1);
    expect(screen.getByText(/Auto-repair didn't work/)).toBeTruthy();
    expect(repairMock).toHaveBeenCalledTimes(1);
    expect(trackMock).toHaveBeenCalledTimes(1);
    expect(trackMock).toHaveBeenCalledWith("pipeline_repair_result", { ok: false });
  });

  it("POST rejected (actd not loaded → 409) → one event with ok:false, no polling", async () => {
    repairMock.mockRejectedValue(new Error("com.zelin.aiassistant.actd is not loaded — run bash install.sh"));
    renderEn();
    fireEvent.click(screen.getByRole("button", { name: "Fix now" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText(/is not loaded/)).toBeTruthy();
    expect(fetchHealthMock).not.toHaveBeenCalled();
    expect(trackMock).toHaveBeenCalledTimes(1);
    expect(trackMock).toHaveBeenCalledWith("pipeline_repair_result", { ok: false });
  });
});
