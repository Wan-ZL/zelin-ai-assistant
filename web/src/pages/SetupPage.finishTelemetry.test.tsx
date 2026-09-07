// 向导「完成」的两条旁路（CONTRACT §15 / §16 追记；owner 决策 D48 / D49；原生 SetupWizard.swift:615 `wizard_complete`）：
//   · complete 成功 → 请 server 落 consent 标记（markTelemetryConsentShown）+ 发 wizard_complete（trackEvent），
//     两者都在整页导航**之前**落地（离开文档会打断在飞的 fetch）；
//   · complete 被拒 → 一个都不发（没完成就不是「完成」）；
//   · 只在「完成」上发：渲染向导、走到末步、点「上一步」都不触发（§15 issue #37 追记「永不在挂载时写」）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchHealth, fetchPermissions, fetchSecrets, fetchSetup, fetchSetupEngine, postSetupStep } from "../api";
import { LanguageContext } from "../i18n";
import { navigate } from "../route";
import { resetStoreForTests } from "../store";
import { markTelemetryConsentShown, trackEvent } from "../telemetry";
import type { PermissionsSnapshot, SetupSnapshot } from "../types";
import { SetupPage } from "./SetupPage";

vi.mock("../route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../route")>();
  return { ...actual, navigate: vi.fn() };
});

vi.mock("../telemetry", () => ({
  markTelemetryConsentShown: vi.fn(),
  trackEvent: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    fetchHealth: vi.fn(), fetchPermissions: vi.fn(), fetchSetup: vi.fn(), fetchSecrets: vi.fn(), fetchSetupEngine: vi.fn(),
    postSetupStep: vi.fn(), putSettingsSection: vi.fn(), fetchSettingsCatalog: vi.fn().mockResolvedValue({ sections: [] }),
  };
});

const ENGINE_READY = { cli_path: "/usr/local/bin/claude", version: "1.0.99 (Claude Code)", auth: "oauth", auth_sources: { oauth: true, env_key: false, secrets_file: false, legacy_file: false }, ready: true };
const HEALTH_OK = { verdict: "ok", heartbeat: { age_s: 3, phase: "dashboard", pid: 1, interval: 10, stale_after_s: 90, stale: false }, dashboard: { generated_at: "2026-09-02T00:00:00Z", age_s: 30, stale: false }, loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "x" };

function permissions(): PermissionsSnapshot {
  return {
    home: "/h", on_external_volume: false,
    fda: { needed: false, pane: "x", executables: [] },
    panes: { full_disk: "x", screen: "y", microphone: "z", notifications: "n" },
    doctor: [], doctor_ran_at: "2026-09-02T00:00:00Z", doctor_ok: true,
    vault: { status: "unknown", root: "/Users/demo/Documents/Obsidian Vault" },
  };
}

function setup(over: Partial<SetupSnapshot> = {}): SetupSnapshot {
  return { needed: true, done: false, config_exists: true, config_example_exists: true, secrets: {}, home: "/h", protected_location: false, ...over };
}

function renderAt(step: string) {
  window.history.replaceState(null, "", `/?page=setup&step=${step}`);
  return render(<LanguageContext.Provider value="en"><SetupPage /></LanguageContext.Provider>);
}

const consentMock = vi.mocked(markTelemetryConsentShown);
const trackMock = vi.mocked(trackEvent);

beforeEach(() => {
  resetStoreForTests();
  for (const fn of [fetchHealth, fetchPermissions, fetchSetup, fetchSecrets, fetchSetupEngine, postSetupStep, navigate]) vi.mocked(fn).mockReset();
  consentMock.mockReset().mockResolvedValue(undefined);
  trackMock.mockReset().mockResolvedValue(undefined);
  vi.mocked(fetchSetup).mockResolvedValue(setup());
  vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [] });
  vi.mocked(fetchSetupEngine).mockResolvedValue(ENGINE_READY);
  vi.mocked(fetchHealth).mockResolvedValue(HEALTH_OK);
  vi.mocked(fetchPermissions).mockResolvedValue(permissions());
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("wizard 完成 → consent marker + wizard_complete (D48 / D49)", () => {
  it("Done: complete succeeds → both calls land before the navigation, in that order", async () => {
    const order: string[] = [];
    vi.mocked(postSetupStep).mockImplementation(async () => { order.push("complete"); return { ok: true, setup: setup({ done: true, needed: false }) }; });
    consentMock.mockImplementation(async () => { order.push("consent"); });
    trackMock.mockImplementation(async () => { order.push("track"); });
    vi.mocked(navigate).mockImplementation(() => { order.push("navigate"); });
    renderAt("finale");
    await screen.findByText("Step 7 of 7");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(postSetupStep).toHaveBeenCalledWith("complete");
    expect(consentMock).toHaveBeenCalledTimes(1);
    expect(trackMock).toHaveBeenCalledTimes(1);
    expect(trackMock).toHaveBeenCalledWith("wizard_complete");
    expect(order.indexOf("complete")).toBeLessThan(order.indexOf("consent"));
    expect(order.indexOf("consent")).toBeLessThan(order.indexOf("navigate"));
    expect(order.indexOf("track")).toBeLessThan(order.indexOf("navigate"));
  });

  it("Done: complete is rejected → neither the marker nor the event is sent", async () => {
    vi.mocked(postSetupStep).mockRejectedValue(new Error("state/ not writable"));
    renderAt("finale");
    await screen.findByText("Step 7 of 7");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await screen.findByText("state/ not writable");
    expect(consentMock).not.toHaveBeenCalled();
    expect(trackMock).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("rendering the wizard, reaching the last step and going back never fires either call", async () => {
    renderAt("credentials");
    await screen.findByText("Step 6 of 7");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Step 7 of 7");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await screen.findByText("Step 6 of 7");
    expect(consentMock).not.toHaveBeenCalled();
    expect(trackMock).not.toHaveBeenCalled();
    expect(postSetupStep).not.toHaveBeenCalled();
  });
});
