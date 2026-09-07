// 重跑向导时的「登录时自动启动」默认值（CONTRACT §28 追记；决策 D39；原生 Onboarding.registerLaunchAtLoginDefault 的
// `launchAtLoginDefaultApplied` 一次性标记保留为 localStorage 同名键）：
//   · 首跑（无标记）「完成」放行 → 写标记；
//   · 标记在 ∧ 壳 launch_at_login=false（owner 在 设置 → 关于 关掉了）→ 重跑向导时行**未勾选**，「完成」不打桥——
//     原生保证「用户之后把开关关掉，永不被重新注册」，一路 Return 到终章也不会把登录项偷偷加回来；
//   · 标记在 ∧ 壳 launch_at_login=true → 行勾选，「完成」no-op（diff-write）；
//   · 标记在、owner 重新勾上 → 显式要求 → `setLaunchAtLogin {on:true}`；
//   · 标记在、快照晚于首帧到 → 默认值跟快照走（不能在 mount 时定死成 false，否则会把开着的登录项 `on:false` 关掉）。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchHealth, fetchPermissions, fetchSecrets, fetchSetup, fetchSetupEngine, postSetupStep } from "../api";
import { LAUNCH_AT_LOGIN_DEFAULT_APPLIED_KEY, markLaunchAtLoginDefaultApplied } from "../components/setup/LaunchAtLoginChoice";
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

/** 假壳：getState / getPermissions 回当前快照；setLaunchAtLogin 记账并回带新值的快照。`snapshot:false` = 桥装了但快照还没到
 *  ——此时读类调用（向导一开就跑的 2 s 权限轮询也会带回快照）一律悬着不回，直到测试调 `releaseSnapshot()` */
let snapshotHeld = false;
function installShell(state: ShellState, snapshot = true) {
  snapshotHeld = !snapshot;
  postMessage.mockImplementation((body: unknown) => {
    const { method, on } = body as { method: string; on?: boolean };
    if (method === "setLaunchAtLogin") return Promise.resolve({ ...state, launch_at_login: Boolean(on) });
    if (snapshotHeld) return new Promise<unknown>(() => undefined);
    return Promise.resolve(state);
  });
  window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
  if (snapshot) applyShellState(state);
}

function releaseSnapshot(state: ShellState) {
  snapshotHeld = false;
  act(() => { applyShellState(state); });
}

function renderFinale() {
  window.history.replaceState(null, "", "/?page=setup&step=finale");
  return render(<LanguageContext.Provider value="en"><SetupPage /></LanguageContext.Provider>);
}

const box = () => screen.getByRole("checkbox", { name: /Launch at login/ }) as HTMLInputElement;
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
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  delete window.webkit;
  window.history.replaceState(null, "", "/");
});

describe("wizard re-run → 登录项默认值 (D39 one-shot marker)", () => {
  it("first run: Done writes the one-shot marker (after the register succeeded, before complete)", async () => {
    installShell(shellState());
    const order: string[] = [];
    postMessage.mockImplementation(async (body: unknown) => {
      const { method } = body as { method: string };
      if (method === "setLaunchAtLogin") order.push(`register marker=${window.localStorage.getItem(LAUNCH_AT_LOGIN_DEFAULT_APPLIED_KEY)}`);
      return shellState({ launch_at_login: method === "setLaunchAtLogin" });
    });
    vi.mocked(postSetupStep).mockImplementation(async () => { order.push(`complete marker=${window.localStorage.getItem(LAUNCH_AT_LOGIN_DEFAULT_APPLIED_KEY)}`); return { ok: true, setup: setup({ done: true, needed: false }) }; });
    renderFinale();
    await screen.findByText("Step 7 of 7");
    expect(window.localStorage.getItem(LAUNCH_AT_LOGIN_DEFAULT_APPLIED_KEY)).toBeNull();
    expect(box().checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(order).toEqual(["register marker=null", "complete marker=1"]);
  });

  it("first run, row unchecked: Done still writes the marker (a choice was made) without any bridge call", async () => {
    installShell(shellState());
    renderFinale();
    await screen.findByText("Step 7 of 7");
    fireEvent.click(box());
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(setLaunchCalls()).toEqual([]);
    expect(window.localStorage.getItem(LAUNCH_AT_LOGIN_DEFAULT_APPLIED_KEY)).toBe("1");
  });

  it("bridge rejected: no marker (the choice has not taken effect yet)", async () => {
    installShell(shellState());
    postMessage.mockImplementation(async (body: unknown) => {
      if ((body as { method: string }).method === "setLaunchAtLogin") throw new Error("INVALID_ARGS: launch at login: SMAppService: Operation not permitted");
      return shellState();
    });
    renderFinale();
    await screen.findByText("Step 7 of 7");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await screen.findByRole("status");
    expect(window.localStorage.getItem(LAUNCH_AT_LOGIN_DEFAULT_APPLIED_KEY)).toBeNull();
  });

  it("marker present + shell off (owner turned it off in Settings → About): row renders unchecked, Done makes no bridge call", async () => {
    markLaunchAtLoginDefaultApplied();
    installShell(shellState({ launch_at_login: false }));
    renderFinale();
    await screen.findByText("Step 7 of 7");
    expect(box().disabled).toBe(false);
    expect(box().checked).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(setLaunchCalls()).toEqual([]);
    expect(postSetupStep).toHaveBeenCalledWith("complete");
  });

  it("marker present + shell on: row renders checked, Done is a bridge no-op (diff-write)", async () => {
    markLaunchAtLoginDefaultApplied();
    installShell(shellState({ launch_at_login: true }));
    renderFinale();
    await screen.findByText("Step 7 of 7");
    expect(box().checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(setLaunchCalls()).toEqual([]);
  });

  it("marker present + shell off, owner re-checks the row: Done registers (explicit ask wins over the memory)", async () => {
    markLaunchAtLoginDefaultApplied();
    installShell(shellState({ launch_at_login: false }));
    renderFinale();
    await screen.findByText("Step 7 of 7");
    fireEvent.click(box());
    expect(box().checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(setLaunchCalls()).toEqual([{ method: "setLaunchAtLogin", on: true }]);
  });

  it("marker present, snapshot arrives after the first frame: the default follows the snapshot (an on login item is not turned off)", async () => {
    markLaunchAtLoginDefaultApplied();
    installShell(shellState({ launch_at_login: true }), false);
    renderFinale();
    await screen.findByText("Step 7 of 7");
    // 快照未到：行禁用、显示未勾选（不能动手就不许看起来会动手）
    expect(box().disabled).toBe(true);
    expect(box().checked).toBe(false);
    releaseSnapshot(shellState({ launch_at_login: true }));
    expect(box().disabled).toBe(false);
    expect(box().checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(setLaunchCalls()).toEqual([]);
  });

  it("no marker (first run), snapshot arrives late: default stays checked → Done registers", async () => {
    installShell(shellState({ launch_at_login: false }), false);
    renderFinale();
    await screen.findByText("Step 7 of 7");
    expect(box().disabled).toBe(true);
    releaseSnapshot(shellState({ launch_at_login: false }));
    expect(box().checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    expect(setLaunchCalls()).toEqual([{ method: "setLaunchAtLogin", on: true }]);
  });
});
