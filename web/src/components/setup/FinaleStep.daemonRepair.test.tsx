// 向导末步「后台服务」行的「启动后台服务」= 横幅一键修复的同一状态机（useRepairActd，§68.8 追记 / §68.5）：
//   POST kickstart → 每 1 s 问 health、最多 15 轮 → 心跳回来：行转绿（store 6 s 后才刷，行先按轮询结果说话）；
//   15 轮没转好 → 原生 fixNote 前缀「启动失败: 」+ 整句「后台服务已重启，但数据还没更新——…」；再点一次即清。
//   轮询的恢复判据 = 本行的 daemonRunning（心跳在且不 stale，§68.5）——`unknown`（无心跳、看板新鲜）对横幅算恢复、
//   对本行不算，行不得绿 6 s 再翻红（§68.8 追记二）。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchHealth, postRepairActd } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshHealth, resetStoreForTests } from "../../store";
import type { HealthSnapshot, SetupEngine } from "../../types";
import { REPAIR_POLL_MS, REPAIR_POLL_ROUNDS, REPAIR_SUCCESS_MS } from "../shell/repairActd";
import { FinaleStep } from "./FinaleStep";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchHealth: vi.fn(), postRepairActd: vi.fn(), postSeedDashboard: vi.fn() };
});

const fetchHealthMock = vi.mocked(fetchHealth);
const repairMock = vi.mocked(postRepairActd);

const DEAD: HealthSnapshot = {
  verdict: "stale", heartbeat: null, dashboard: null,
  loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "2026-09-01T08:00:05Z",
};
const OK: HealthSnapshot = {
  verdict: "ok",
  heartbeat: { age_s: 4, phase: "idle", pid: 4242, interval: 10, stale_after_s: 90, stale: false },
  dashboard: { generated_at: "2026-09-01T08:00:00Z", age_s: 5, stale: false },
  loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "2026-09-01T08:00:05Z",
};
/** 无心跳文件、看板新鲜（刚点过「立即生成一次」/ 心跳写不出来）：横幅的 isRecovered 算恢复，本行的 daemonRunning 不算 */
const UNKNOWN: HealthSnapshot = {
  verdict: "unknown", heartbeat: null,
  dashboard: { generated_at: "2026-09-01T08:00:00Z", age_s: 5, stale: false },
  loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "2026-09-01T08:00:05Z",
};
const ENGINE: SetupEngine = { cli_path: "/usr/local/bin/claude", version: "1.0.99", auth: "api_key", auth_sources: {}, ready: true };

function daemonRow() {
  return document.querySelector("[data-row='daemon']") as HTMLElement;
}

async function pollRounds(n: number) {
  for (let i = 0; i < n; i += 1) {
    await act(async () => { await vi.advanceTimersByTimeAsync(REPAIR_POLL_MS); });
  }
}

async function renderFinale() {
  fetchHealthMock.mockResolvedValue(DEAD);
  await refreshHealth();
  render(
    <LanguageContext.Provider value="en">
      <FinaleStep engine={ENGINE} engineChecking={false} goEngine={() => undefined} />
    </LanguageContext.Provider>,
  );
  expect(daemonRow().className).toContain("is-fail");
}

beforeEach(() => {
  resetStoreForTests();
  fetchHealthMock.mockReset();
  repairMock.mockReset();
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("FinaleStep — 后台服务 row uses the shared repair state machine", () => {
  it("Start it → Starting… while polling; heartbeat back on the 2nd poll → row green at once, store refreshed after 6 s", async () => {
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
    await renderFinale();
    fireEvent.click(screen.getByRole("button", { name: "Start it" }));
    await act(async () => { await Promise.resolve(); });
    expect(repairMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Starting…" })).toBeTruthy();
    await pollRounds(1);
    expect(daemonRow().className).toContain("is-fail");
    fetchHealthMock.mockResolvedValue(OK);
    await pollRounds(1);
    expect(daemonRow().className).toContain("is-ok");
    expect(screen.queryByRole("button", { name: "Starting…" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    // refreshHealth 只在 6 s 庆祝结束后：1 次初始 + 2 次轮询 + 1 次刷 store
    await act(async () => { await vi.advanceTimersByTimeAsync(REPAIR_SUCCESS_MS); });
    expect(fetchHealthMock).toHaveBeenCalledTimes(4);
    expect(daemonRow().className).toContain("is-ok");
  });

  it("15 polls without a heartbeat → 启动失败: + the honest sentence; Start it comes back and clears the note on retry", async () => {
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
    await renderFinale();
    fireEvent.click(screen.getByRole("button", { name: "Start it" }));
    await act(async () => { await Promise.resolve(); });
    await pollRounds(REPAIR_POLL_ROUNDS);
    const note = screen.getByRole("alert");
    expect(note.textContent).toBe("Start failed: Service restarted but data still isn't updating — try \"Fix with AI\" or view the log");
    expect(screen.getByText("Start failed:")).toBeTruthy();
    expect(daemonRow().className).toContain("is-fail");
    fireEvent.click(screen.getByRole("button", { name: "Start it" }));
    await act(async () => { await Promise.resolve(); });
    expect(repairMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("verdict=unknown on a poll is not 'running' for this row: it keeps polling, never flashes green, and reports the honest failure after 15 rounds", async () => {
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
    await renderFinale();
    fireEvent.click(screen.getByRole("button", { name: "Start it" }));
    await act(async () => { await Promise.resolve(); });
    fetchHealthMock.mockResolvedValue(UNKNOWN);
    await pollRounds(1);
    // 横幅宿主会在这里说「已恢复 ✓」；向导行按 §68.5 判据仍是红的、按钮仍是「启动中…」、继续轮询
    expect(daemonRow().className).toContain("is-fail");
    expect(screen.getByRole("button", { name: "Starting…" })).toBeTruthy();
    await pollRounds(REPAIR_POLL_ROUNDS - 2);
    expect(daemonRow().className).toContain("is-fail");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(fetchHealthMock).toHaveBeenCalledTimes(1 + REPAIR_POLL_ROUNDS - 1);
    await pollRounds(1);
    expect(fetchHealthMock).toHaveBeenCalledTimes(1 + REPAIR_POLL_ROUNDS);
    expect(screen.getByRole("alert").textContent).toBe("Start failed: Service restarted but data still isn't updating — try \"Fix with AI\" or view the log");
    expect(screen.getByRole("button", { name: "Start it" })).toBeTruthy();
    // 之后没有 6 s 庆祝、没有 store 刷新：再走 6 s 也不多一次 fetch，行也不翻面
    await act(async () => { await vi.advanceTimersByTimeAsync(REPAIR_SUCCESS_MS); });
    expect(fetchHealthMock).toHaveBeenCalledTimes(1 + REPAIR_POLL_ROUNDS);
    expect(daemonRow().className).toContain("is-fail");
  });

  it("a fresh heartbeat mid-poll turns the row green (the §68.5 criterion), even when the store still holds the dead snapshot", async () => {
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
    await renderFinale();
    fireEvent.click(screen.getByRole("button", { name: "Start it" }));
    await act(async () => { await Promise.resolve(); });
    fetchHealthMock.mockResolvedValue(UNKNOWN);
    await pollRounds(1);
    expect(daemonRow().className).toContain("is-fail");
    fetchHealthMock.mockResolvedValue(OK);
    await pollRounds(1);
    expect(daemonRow().className).toContain("is-ok");
    await act(async () => { await vi.advanceTimersByTimeAsync(REPAIR_SUCCESS_MS); });
    expect(daemonRow().className).toContain("is-ok"); // store 刷完仍绿：轮询判据与 steady-state 判据一致
  });

  it("a refused POST keeps the native prefix with the server's sentence and never polls", async () => {
    repairMock.mockRejectedValue(new Error("com.zelin.aiassistant.actd is not loaded in launchd"));
    await renderFinale();
    fireEvent.click(screen.getByRole("button", { name: "Start it" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("Start failed:")).toBeTruthy();
    expect(screen.getByText(/not loaded in launchd/)).toBeTruthy();
    await pollRounds(2);
    expect(fetchHealthMock).toHaveBeenCalledTimes(1); // 只有 renderFinale 的那一次
  });
});
