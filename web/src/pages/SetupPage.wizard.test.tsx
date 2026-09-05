// 首次运行向导追回的原生窗口行为（CONTRACT §68.5 2026-09-05 追记；原生 SetupWizard.swift:81 / 361 / 581-585 / 603-609、
// Permissions.swift:46、AppDelegate.swift:82-90）：
//   1) Return = 下一步 / 完成（.keyboardShortcut(.defaultAction)）：焦点在 body 上也算；焦点在输入框 / 按钮里不抢；
//      带修饰键 / IME 组字 / 被内层 preventDefault 认领的不算；末步 Return = 「完成」（写标记 + 回看板），忙时不重发；
//      GET /api/setup 未回（首开步还没定）时 Return / 「下一步」惰性——欢迎步（建 config.yaml）不能被抢跳过；
//   2) 整个向导 2 s 轮询壳 TCC 探针（不只权限步），末步另起 2.5 s 管线探针（health + permissions），引擎每 4 拍静默复检，
//      离开末步即停；浏览器（无桥）不打桥；
//   3) 「先去看板（下次再来）」落 sessionStorage 标记，app.tsx 的 shouldRedirectToSetup 本会话不再跳；
//   4) 笔记库步「选择…」：壳在场 → 桥 chooseFolder(current = 默认目录, prompt「选择」)，选中即成自定义行；取消不动；
//      老壳 UNKNOWN_METHOD → 退化成路径输入框；无桥 → 直接输入框。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchHealth, fetchPermissions, fetchSecrets, fetchSetup, fetchSetupEngine, postSetupStep } from "../api";
import { shouldRedirectToSetup } from "../app";
import { LanguageContext } from "../i18n";
import { navigate } from "../route";
import { callShell, resetShellBridgeForTests } from "../shellBridge";
import { resetStoreForTests } from "../store";
import type { PermissionsSnapshot, SetupSnapshot } from "../types";
import { DEFAULT_CUSTOM_ROOT } from "../components/setup/VaultStep";
import { ENGINE_RECHECK_EVERY, FINALE_PROBE_MS, isSetupSkipped, isWizardReturn, markSetupSkipped, SETUP_SKIPPED_KEY, SetupPage } from "./SetupPage";
import { PERMISSION_POLL_MS } from "../components/permissions/usePermissionPolling";

vi.mock("../route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../route")>();
  return { ...actual, navigate: vi.fn() };
});

// callShell 只包一层 spy（真实现照跑）：「无桥不打桥」要能数出零次调用，而不是只看 window.webkit 仍是 undefined
vi.mock("../shellBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../shellBridge")>();
  return { ...actual, callShell: vi.fn(actual.callShell) };
});

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

const SHELL_SNAPSHOT = { recording: {}, captions: {}, permissions: { screen: "granted", notifications: "granted", microphone: "unknown", vault: "unknown" } };

function installShell(postMessage: (body: { method: string } & Record<string, unknown>) => Promise<unknown>) {
  window.webkit = { messageHandlers: { zaiShell: { postMessage: postMessage as (body: unknown) => Promise<unknown> } } };
}

function renderAt(step: string) {
  window.history.replaceState(null, "", `/?page=setup&step=${step}`);
  return render(<LanguageContext.Provider value="en"><SetupPage /></LanguageContext.Provider>);
}

const stepLabel = (n: number) => screen.findByText(`Step ${n} of 7`);

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
  for (const fn of [fetchHealth, fetchPermissions, fetchSetup, fetchSecrets, fetchSetupEngine, postSetupStep, navigate]) vi.mocked(fn).mockReset();
  vi.mocked(callShell).mockClear(); // 只清计数——实现仍是真 callShell
  vi.mocked(fetchSetup).mockResolvedValue(setup());
  vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [] });
  vi.mocked(fetchSetupEngine).mockResolvedValue(ENGINE_READY);
  vi.mocked(fetchHealth).mockResolvedValue(HEALTH_OK);
  vi.mocked(fetchPermissions).mockResolvedValue(permissions());
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  delete window.webkit;
});

describe("Return advances the wizard (原生 Next / Done .keyboardShortcut(.defaultAction))", () => {
  it("Enter with nothing focused → next step; on the last step → Done (complete + board)", async () => {
    vi.mocked(postSetupStep).mockResolvedValue({ ok: true, setup: setup({ done: true, needed: false }) });
    renderAt("credentials");
    await stepLabel(6);
    fireEvent.keyDown(document.body, { key: "Enter" });
    await stepLabel(7);
    expect(postSetupStep).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "Enter" });
    await waitFor(() => expect(postSetupStep).toHaveBeenCalledWith("complete"));
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(String(vi.mocked(navigate).mock.calls[0][0])).not.toContain("page=");
  });

  it("Enter inside an input is inert; modifiers / IME / a claimed event do not advance", async () => {
    renderAt("vault");
    await stepLabel(5);
    fireEvent.click(screen.getByRole("button", { name: "Choose…" })); // 无桥 → 路径输入框
    const input = await screen.findByRole("textbox", { name: "Folder path" });
    input.focus();
    fireEvent.change(input, { target: { value: "~/Typed" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.keyDown(document.body, { key: "Enter", metaKey: true });
    fireEvent.keyDown(document.body, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(document.body, { key: "Enter", isComposing: true });
    fireEvent.keyDown(document.body, { key: "Escape" });
    await stepLabel(5);
    expect(screen.queryByText("Step 6 of 7")).toBeNull();
    expect(screen.getByRole("textbox", { name: "Folder path" })).toBeTruthy(); // 输入框还开着、值还在
    // 焦点在「下一步」按钮上时 Enter 归按钮（浏览器自己 click 一次），向导不再叠一次——这里只钉「不抢」
    const next = screen.getByRole("button", { name: "Next" });
    next.focus();
    fireEvent.keyDown(next, { key: "Enter" });
    await stepLabel(5);
    // 判据函数本身：按钮 / 链接归自己，body / div 归向导；被内层 preventDefault 认领的不算
    const ev = (over: Partial<Parameters<typeof isWizardReturn>[0]> = {}) => ({ key: "Enter", altKey: false, ctrlKey: false, metaKey: false, shiftKey: false, defaultPrevented: false, isComposing: false, target: document.body, ...over });
    expect(isWizardReturn(ev())).toBe(true);
    expect(isWizardReturn(ev({ target: document.createElement("div") }))).toBe(true);
    for (const tag of ["input", "textarea", "select", "button", "a"]) expect(isWizardReturn(ev({ target: document.createElement(tag) }))).toBe(false);
    expect(isWizardReturn(ev({ defaultPrevented: true }))).toBe(false);
    expect(isWizardReturn(ev({ altKey: true }))).toBe(false);
    expect(isWizardReturn(ev({ ctrlKey: true }))).toBe(false);
    expect(isWizardReturn(ev({ key: " " }))).toBe(false);
  });

  it("a slow Done is not re-fired by a second Return while busy", async () => {
    let release: (() => void) | null = null;
    vi.mocked(postSetupStep).mockImplementation(() => new Promise((resolve) => { release = () => resolve({ ok: true, setup: setup({ done: true, needed: false }) }); }));
    renderAt("finale");
    await stepLabel(7);
    fireEvent.keyDown(document.body, { key: "Enter" });
    await screen.findByRole("button", { name: "Saving…" });
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(postSetupStep).toHaveBeenCalledTimes(1);
    await act(async () => { release?.(); });
  });

  it("Return (and Next) before GET /api/setup resolves is inert, so a fresh machine still lands on the config.yaml step", async () => {
    let release: ((snapshot: SetupSnapshot) => void) | null = null;
    vi.mocked(fetchSetup).mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    window.history.replaceState(null, "", "/?page=setup"); // 无 ?step= → 首开步等 setup 回来才定
    render(<LanguageContext.Provider value="en"><SetupPage /></LanguageContext.Provider>);
    await stepLabel(1);
    const next = () => screen.getByRole("button", { name: "Next" }) as HTMLButtonElement;
    expect(next().disabled).toBe(true);
    fireEvent.keyDown(document.body, { key: "Enter" });
    fireEvent.click(next());
    await stepLabel(1);
    expect(screen.queryByText("Step 2 of 7")).toBeNull();
    await act(async () => { release?.(setup({ config_exists: false })); });
    await screen.findByRole("button", { name: "Create from config.example.yaml" }); // 欢迎步没被抢跳过
    expect(screen.getByText("Step 1 of 7")).toBeTruthy();
    expect(next().disabled).toBe(false);
    fireEvent.keyDown(document.body, { key: "Enter" }); // 首开步定了，Return 照常推进
    await stepLabel(2);
  });
});

describe("live polling (原生 perms.startPolling 2 s + PipelineProbeModel 2.5 s)", () => {
  it("with the shell: getPermissions every 2 s on any step; the finale adds health + permissions every 2.5 s and a quiet engine re-check every 4th tick; leaving the finale stops the probe", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const postMessage = vi.fn(async (_body: { method: string }) => SHELL_SNAPSHOT);
    installShell(postMessage);
    renderAt("credentials");
    await stepLabel(6);
    const bridgeCalls = () => postMessage.mock.calls.filter(([body]) => body.method === "getPermissions").length;
    const before = bridgeCalls();
    expect(before).toBeGreaterThanOrEqual(1); // 挂载即打一次
    await act(async () => { vi.advanceTimersByTime(PERMISSION_POLL_MS * 3); });
    expect(bridgeCalls()).toBe(before + 3);
    // 非末步：没有管线探针——health / permissions 只在挂载时各拉一次
    const healthBefore = vi.mocked(fetchHealth).mock.calls.length;
    const permsBefore = vi.mocked(fetchPermissions).mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(FINALE_PROBE_MS * 2); });
    expect(vi.mocked(fetchHealth).mock.calls.length).toBe(healthBefore);
    expect(vi.mocked(fetchPermissions).mock.calls.length).toBe(permsBefore);

    // 进末步：进场一拉（原生 setStep finale）+ 每 2.5 s 一拍（逐拍推进——store 的 loadPage 会把同一在途请求合并）
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await stepLabel(7);
    await waitFor(() => expect(vi.mocked(fetchHealth).mock.calls.length).toBe(healthBefore + 1));
    await waitFor(() => expect(vi.mocked(fetchPermissions).mock.calls.length).toBe(permsBefore + 1));
    const engineBefore = vi.mocked(fetchSetupEngine).mock.calls.length;
    for (let tick = 1; tick <= ENGINE_RECHECK_EVERY; tick += 1) {
      await act(async () => { vi.advanceTimersByTime(FINALE_PROBE_MS); });
      await waitFor(() => expect(vi.mocked(fetchHealth).mock.calls.length).toBe(healthBefore + 1 + tick));
      await waitFor(() => expect(vi.mocked(fetchPermissions).mock.calls.length).toBe(permsBefore + 1 + tick));
      expect(vi.mocked(fetchSetupEngine).mock.calls.length).toBe(engineBefore + (tick === ENGINE_RECHECK_EVERY ? 1 : 0)); // 第 4 拍才静默复检
    }
    expect(screen.queryByText("Detecting…")).toBeNull(); // quiet：行不闪成「检测中…」

    // 回上一步：管线探针停，TCC 轮询继续
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await stepLabel(6);
    const healthAfter = vi.mocked(fetchHealth).mock.calls.length;
    const bridgeAfter = bridgeCalls();
    await act(async () => { vi.advanceTimersByTime(FINALE_PROBE_MS * 4); });
    expect(vi.mocked(fetchHealth).mock.calls.length).toBe(healthAfter);
    expect(bridgeCalls()).toBeGreaterThan(bridgeAfter);
  });

  it("in a plain browser (no bridge) nothing is posted to a shell and no poll timer is scheduled", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    expect(window.webkit).toBeUndefined();
    renderAt("permissions");
    await stepLabel(3);
    await act(async () => { vi.advanceTimersByTime(PERMISSION_POLL_MS * 3); });
    expect(callShell).not.toHaveBeenCalled(); // 挂载那一拍也没打（NO_BRIDGE 被 .catch 吞掉，所以要数调用而不是看报错）
    expect(vi.getTimerCount()).toBe(0); // 非末步无管线探针、无桥无 TCC 轮询：向导一个 interval 都不挂
  });
});

describe("「先去看板（下次再来）」 = 原生关窗：这次不问", () => {
  it("clicking the link sets the session marker and shouldRedirectToSetup stops sending the board back to the wizard", async () => {
    renderAt("engine");
    await stepLabel(2);
    expect(shouldRedirectToSetup("board", true)).toBe(true);
    const link = screen.getByRole("link", { name: "Go to the board (come back later)" });
    expect(link.getAttribute("href")).not.toContain("page=");
    link.addEventListener("click", (e) => e.preventDefault()); // jsdom 不会整页导航；React 的 onClick 照跑
    fireEvent.click(link);
    expect(window.sessionStorage.getItem(SETUP_SKIPPED_KEY)).toBe("1");
    expect(isSetupSkipped()).toBe(true);
    expect(shouldRedirectToSetup("board", true)).toBe(false);
    expect(shouldRedirectToSetup("board", false)).toBe(false);
    // 新会话（sessionStorage 空）再问
    window.sessionStorage.clear();
    expect(shouldRedirectToSetup("board", true)).toBe(true);
  });

  it("an unwritable sessionStorage means 'not skipped' (宁多问)", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("QuotaExceeded"); });
    expect(() => markSetupSkipped()).not.toThrow();
    spy.mockRestore();
    expect(isSetupSkipped()).toBe(false);
    const getSpy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("SecurityError"); });
    expect(isSetupSkipped()).toBe(false);
    expect(shouldRedirectToSetup("board", true)).toBe(true);
    getSpy.mockRestore();
  });
});

describe("vault step 「选择…」 (原生 NSOpenPanel chooseCustomFolder)", () => {
  it("with the shell: opens chooseFolder at the native default, the pick becomes the custom row; cancel leaves it untouched", async () => {
    let reply: unknown = { ...SHELL_SNAPSHOT, dialog: { path: null } };
    const postMessage = vi.fn(async (body: { method: string }) => (body.method === "chooseFolder" ? reply : SHELL_SNAPSHOT));
    installShell(postMessage);
    renderAt("vault");
    await stepLabel(5);
    await screen.findByText("/Users/demo/Documents/Obsidian Vault");
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith({ method: "chooseFolder", current: DEFAULT_CUSTOM_ROOT, prompt: "Choose" }));
    expect(screen.getByText("(no folder chosen yet)")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Folder path" })).toBeNull(); // 壳在场不长输入框
    reply = { ...SHELL_SNAPSHOT, dialog: { path: "~/Picked Notes" } };
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    await screen.findByText("~/Picked Notes");
    expect(screen.getByRole("button", { name: "No Obsidian — plain markdown folder" }).getAttribute("aria-pressed")).toBe("true");
    // 再选一次：起点是刚选的目录
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith({ method: "chooseFolder", current: "~/Picked Notes", prompt: "Choose" }));
  });

  it("an old shell (UNKNOWN_METHOD) and a plain browser fall back to the typed path", async () => {
    installShell(async (body) => { if (body.method === "chooseFolder") throw "UNKNOWN_METHOD: chooseFolder"; return SHELL_SNAPSHOT; });
    renderAt("vault");
    await stepLabel(5);
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    const input = await screen.findByRole("textbox", { name: "Folder path" });
    expect(input.getAttribute("placeholder")).toBe(DEFAULT_CUSTOM_ROOT);
    fireEvent.change(input, { target: { value: "~/Typed" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose" }));
    await screen.findByText("~/Typed");
    cleanup();
    delete window.webkit;
    resetStoreForTests();
    renderAt("vault");
    await stepLabel(5);
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    expect(await screen.findByRole("textbox", { name: "Folder path" })).toBeTruthy();
  });

  it("a real bridge error shows its text instead of silently falling back", async () => {
    installShell(async (body) => { if (body.method === "chooseFolder") throw "INVALID_ARGS: chooseFolder current must be a string"; return SHELL_SNAPSHOT; });
    renderAt("vault");
    await stepLabel(5);
    fireEvent.click(screen.getByRole("button", { name: "Choose…" }));
    await screen.findByText(/INVALID_ARGS/);
    expect(screen.queryByRole("textbox", { name: "Folder path" })).toBeNull();
  });
});
