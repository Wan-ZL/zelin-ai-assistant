// 向导终章「录制引擎」行的按日程暂停态（CONTRACT §61.7）：暂停 = 中性行（—），说「到设定时间自动开始录制」，
// 不算失败、不出「启动引擎」修复按钮，且压过 engine_npm_download 的「下载中」——壳已把 diagnosis 置 null，页面即便收到
// 旧值也以暂停为准；同一状态不暂停时回到原来的 checking / fail 分支。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { applyShellState, resetShellBridgeForTests, type ShellState } from "../../shellBridge";
import { resetStoreForTests } from "../../store";
import type { SetupEngine } from "../../types";
import { FinaleStep } from "./FinaleStep";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  // postAnalytics：修复下场会发 pipeline_repair_result（D48）——这里不触发修复，mock 只为它绝不出网
  return { ...mod, fetchHealth: vi.fn(), postRepairActd: vi.fn(), postSeedDashboard: vi.fn(), postAnalytics: vi.fn().mockResolvedValue({ ok: true, event: "pipeline_repair_result", logged: true }) };
});

const ENGINE: SetupEngine = { cli_path: "/usr/local/bin/claude", version: "1.0.99", auth: "api_key", auth_sources: {}, ready: true };
const PAUSED = { enabled: true, start: "09:00", end: "19:00", days: [2, 3, 4, 5, 6], paused: true };

const base: ShellState = {
  recording: { available: true, on: true, mode: "screen", engine_running: false, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen", self_heal_note: "", log_tail: "" },
  captions: { available: true, on: false, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false, source: "both", translate: false, translate_direction: "auto", apple_locale: "zh", ark_model: "", font_size: 24, opacity: 0.7 },
  permissions: { screen: "granted", microphone: "unknown", notifications: "unknown", vault: "unknown" },
  launch_at_login: false, hotkey: "⌃⌥Space",
};

function installShell(recording: Partial<ShellState["recording"]>) {
  const state: ShellState = { ...base, recording: { ...base.recording, ...recording } };
  window.webkit = { messageHandlers: { zaiShell: { postMessage: vi.fn(async () => state) } } };
  applyShellState(state);
}

const captureRow = () => document.querySelector("[data-row='capture']") as HTMLElement;
const renderFinale = () => render(
  <LanguageContext.Provider value="en">
    <FinaleStep engine={ENGINE} engineChecking={false} goEngine={() => undefined} />
  </LanguageContext.Provider>,
);

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
});
afterEach(() => {
  cleanup();
  delete window.webkit;
});

describe("FinaleStep · 录制引擎 row while paused by schedule (§61.7)", () => {
  it("paused → neutral row with the schedule sentence, no Start engine fix; paused wins over engine_npm_download", () => {
    installShell({ engine_running: false, diagnosis: "engine_npm_download", schedule: PAUSED });
    renderFinale();
    expect(captureRow().className).toBe("setup-health-row is-neutral");
    expect(captureRow().textContent).toContain("Paused by schedule — recording starts automatically at the scheduled time");
    expect(screen.queryByRole("button", { name: "Start engine" })).toBeNull();
    expect(captureRow().textContent).not.toContain("downloading");
  });

  it("same state, not paused → the old branches: downloading = checking, dead = fail with Start engine", () => {
    installShell({ engine_running: false, diagnosis: "engine_npm_download", schedule: { ...PAUSED, paused: false } });
    renderFinale();
    expect(captureRow().className).toBe("setup-health-row is-checking");
    cleanup();
    installShell({ engine_running: false, diagnosis: null, schedule: { ...PAUSED, paused: false } });
    renderFinale();
    expect(captureRow().className).toBe("setup-health-row is-fail");
    expect(screen.getByRole("button", { name: "Start engine" })).toBeTruthy();
  });

  it("mode off is never 'paused by schedule' — no capture row at all (recording off = the neutral permission row)", () => {
    installShell({ on: false, mode: "off", engine_running: false, schedule: PAUSED });
    renderFinale();
    expect(captureRow()).toBeNull();
  });
});
