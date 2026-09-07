// 向导「完成」→ 登录项（CONTRACT §28 追记；决策 D39；原生 Onboarding.registerLaunchAtLoginDefault 的显式版）：
//   · 壳报正式安装 ∧ 行勾着（默认）∧ 壳的 launch_at_login 还是 false → 「完成」先桥 `setLaunchAtLogin {on:true}`，再 complete、再回看板；
//   · 取消勾选 → 不打桥（壳本来就是 false），complete 照走；
//   · 桥拒绝 → 原句留在本步（role=status）、complete 与导航都不发生；取消勾选再点「完成」就放行；
//   · 浏览器（无桥）/ 开发版（launch_at_login_available:false）→ 行禁用、不打桥、complete 照走。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchHealth, fetchPermissions, fetchSecrets, fetchSetup, fetchSetupEngine, postSetupStep } from "../api";
import { LanguageContext } from "../i18n";
import { navigate } from "../route";
import { applyShellState, resetShellBridgeForTests, type ShellState } from "../shellBridge";
import { resetStoreForTests } from "../store";
import type { PermissionsSnapshot, SetupSnapshot } from "../types";
import { SetupPage } from "./SetupPage";

vi.mock("../route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../route")>();
  return { ...actual, navigate: vi.fn() };
});

vi.mock("../telemetry", () => ({
  markTelemetryConsentShown: vi.fn().mockResolvedValue(undefined),
  trackEvent: vi.fn().mockResolvedValue(undefined),
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

function shellState(over: Partial<ShellState> = {}): ShellState {
  return {
    recording: { available: true, on: false, mode: "off", engine_running: false, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen" },
    captions: { available: true, on: false, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false, source: "both", translate: false, translate_direction: "auto", apple_locale: "zh", ark_model: "m", font_size: 24, opacity: 0.7 },
    permissions: { screen: "granted", microphone: "unknown", notifications: "unknown", vault: "unknown" },
    launch_at_login: false,
    launch_at_login_available: true,
    hotkey: "⌃⌥Space",
    language: "en",
    ...over,
  };
}

const postMessage = vi.fn<(body: unknown) => Promise<unknown>>();

/** 假壳：getState / getPermissions 回当前快照；setLaunchAtLogin 记账并按 `reject` 决定成败 */
function installShell(state: ShellState, reject: string | null = null) {
  postMessage.mockImplementation(async (body: unknown) => {
    const { method, on } = body as { method: string; on?: boolean };
    if (method === "setLaunchAtLogin") {
      if (reject) throw new Error(reject);
      return { ...state, launch_at_login: Boolean(on) };
    }
    return state;
  });
  window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
  applyShellState(state);
}

function renderFinale() {
  window.history.replaceState(null, "", "/?page=setup&step=finale");
  return render(<LanguageContext.Provider value="en"><SetupPage /></LanguageContext.Provider>);
}

const setLaunchCalls = () => postMessage.mock.calls.map((c) => c[0] as { method: string; on?: boolean }).filter((b) => b.method === "setLaunchAtLogin");

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
  postMessage.mockReset();
  for (const fn of [fetchHealth, fetchPermissions, fetchSetup, fetchSecrets, fetchSetupEngine, postSetupStep, navigate]) vi.mocked(fn).mockReset();
  vi.mocked(fetchSetup).mockResolvedValue(setup());
  vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [] });
  vi.mocked(fetchSetupEngine).mockResolvedValue(ENGINE_READY);
  vi.mocked(fetchHealth).mockResolvedValue(HEALTH_OK);
  vi.mocked(fetchPermissions).mockResolvedValue(permissions());
  vi.mocked(postSetupStep).mockResolvedValue({ ok: true, setup: setup({ done: true, needed: false }) });
  window.sessionStorage.clear();
  window.localStorage.clear(); // 首跑语义：一次性标记 launchAtLoginDefaultApplied 不在（重跑判例住 SetupPage.launchAtLoginRerun.test.tsx）
});

afterEach(() => {
  cleanup();
  delete window.webkit;
  window.history.replaceState(null, "", "/");
});

describe("wizard 完成 → 登录项 (D39)", () => {
  it("installed shell, row left at its default (checked): Done registers the login item BEFORE complete, then navigates", async () => {
    const order: string[] = [];
    installShell(shellState());
    postMessage.mockImplementation(async (body: unknown) => {
      const { method } = body as { method: string };
      if (method === "setLaunchAtLogin") order.push("register");
      return shellState({ launch_at_login: method === "setLaunchAtLogin" });
    });
    vi.mocked(postSetupStep).mockImplementation(async () => { order.push("complete"); return { ok: true, setup: setup({ done: true, needed: false }) }; });
    vi.mocked(navigate).mockImplementation(() => { order.push("navigate"); });
    renderFinale();
    await screen.findByText("Step 7 of 7");
    const box = screen.getByRole("checkbox", { name: /Launch at login/ }) as HTMLInputElement;
    expect(box.checked).toBe(true);
    expect(box.disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(setLaunchCalls()).toEqual([{ method: "setLaunchAtLogin", on: true }]);
    expect(order.indexOf("register")).toBeLessThan(order.indexOf("complete"));
    expect(order.indexOf("complete")).toBeLessThan(order.indexOf("navigate"));
  });

  it("unchecking the row: Done never calls the bridge (shell already off) and completes normally", async () => {
    installShell(shellState());
    renderFinale();
    await screen.findByText("Step 7 of 7");
    fireEvent.click(screen.getByRole("checkbox", { name: /Launch at login/ }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(setLaunchCalls()).toEqual([]);
    expect(postSetupStep).toHaveBeenCalledWith("complete");
  });

  it("shell already has it on: Done is a no-op on the bridge (diff-write)", async () => {
    installShell(shellState({ launch_at_login: true }));
    renderFinale();
    await screen.findByText("Step 7 of 7");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(setLaunchCalls()).toEqual([]);
  });

  it("bridge rejects: the reason stays on this step, complete and navigation do not happen; unchecking then Done proceeds", async () => {
    installShell(shellState(), "INVALID_ARGS: launch at login: SMAppService: Operation not permitted");
    renderFinale();
    await screen.findByText("Step 7 of 7");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    const note = await screen.findByRole("status");
    expect(note.textContent).toBe("Failed to enable launch at login: SMAppService: Operation not permitted");
    expect(postSetupStep).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "Done" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("checkbox", { name: /Launch at login/ }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(postSetupStep).toHaveBeenCalledWith("complete");
    expect(setLaunchCalls()).toHaveLength(1); // 只有失败那一次；第二次「完成」勾选已取消 = 与壳真相一致
  });

  it("browser (no bridge): the row is disabled with a reason and Done completes without any bridge call", async () => {
    renderFinale();
    await screen.findByText("Step 7 of 7");
    const box = screen.getByRole("checkbox", { name: /Launch at login/ }) as HTMLInputElement;
    expect(box.disabled).toBe(true);
    expect(screen.getByTestId("setup-launch-at-login-reason").textContent).toContain("browser");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("dev build (shell present, launch_at_login_available false): disabled row, no bridge call, complete proceeds", async () => {
    installShell(shellState({ launch_at_login_available: false }));
    renderFinale();
    await screen.findByText("Step 7 of 7");
    expect((screen.getByRole("checkbox", { name: /Launch at login/ }) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByTestId("setup-launch-at-login-reason").textContent).toContain("/Applications");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(setLaunchCalls()).toEqual([]);
  });

  it("the row lives only on the finale step", async () => {
    installShell(shellState());
    window.history.replaceState(null, "", "/?page=setup&step=credentials");
    render(<LanguageContext.Provider value="en"><SetupPage /></LanguageContext.Provider>);
    await screen.findByText("Step 6 of 7");
    expect(screen.queryByRole("checkbox", { name: /Launch at login/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Step 7 of 7");
    expect(screen.getByRole("checkbox", { name: /Launch at login/ })).toBeTruthy();
  });
});
