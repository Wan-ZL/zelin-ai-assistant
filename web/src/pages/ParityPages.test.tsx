// §66 P4 parity 页：权限体检（FDA 清单 + 可复制路径 + 无桥诚实说明 + 首启 / 体检页脚）、诊断（doctor 表 + 对症一键 +
// 日志尾巴）、首次运行向导（七步：?step= 深链、config-from-example、引擎检测三态、先验后存、末步健康行、完成标记）、
// 永久性完成整页（unarchive）、横幅一键修复、路由新页 / anchor、Dock 徽章计数与向导跳转判定。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchBoard, fetchDiagnostics, fetchDoctor, fetchHealth, fetchLogTail, fetchPermissions, fetchSecrets, fetchSetup, fetchSetupEngine,
  postAction, postRepairActd, postRevealTarget, postSeedDashboard, postSetupStep, putSecret, verifySecret,
} from "../api";
import { badgeCount, shouldRedirectToSetup } from "../app";
import { RepairButton } from "../components/shell/PipelineBanner";
import { LanguageContext } from "../i18n";
import { navigate, readAnchor, readPage } from "../route";
import { refreshBoard, resetStoreForTests } from "../store";
import type { Board, DiagnosticsSnapshot, PermissionsSnapshot, SetupSnapshot } from "../types";
import { ArchivePage } from "./ArchivePage";
import { consentPending } from "../components/permissions/RecordingConsentSection";
import { cronVerdict, daemonRunning } from "../components/setup/FinaleStep";
import { failureActionLabel } from "../components/settings/failureAction";
import { DiagnosticsPage, doctorSummary, fullReportText } from "./DiagnosticsPage";
import { PermissionsPage, statusLabel } from "./PermissionsPage";
import { firstOpenStep, SetupPage, stepFromSearch } from "./SetupPage";

vi.mock("../route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../route")>();
  return { ...actual, navigate: vi.fn() };
});

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    fetchBoard: vi.fn(), fetchHealth: vi.fn(), fetchPermissions: vi.fn(), fetchDiagnostics: vi.fn(), fetchDoctor: vi.fn(),
    fetchLogTail: vi.fn(), fetchSetup: vi.fn(), fetchSecrets: vi.fn(), fetchSetupEngine: vi.fn(), postSetupStep: vi.fn(), postAction: vi.fn(),
    postRepairActd: vi.fn(), postSeedDashboard: vi.fn(), postRevealTarget: vi.fn(), putSecret: vi.fn(), verifySecret: vi.fn(),
    fetchSettingsCatalog: vi.fn().mockResolvedValue({ sections: [] }),
  };
});

const en = (_zh: string, english: string) => english;

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

function permissions(over: Partial<PermissionsSnapshot> = {}): PermissionsSnapshot {
  return {
    home: "/Volumes/Storage/repo", on_external_volume: true,
    fda: { needed: true, pane: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
      executables: [
        { role: "daemon_python", path: "/usr/bin/python3", realpath: "/usr/bin/python3", exists: true, note: { zh: "守护", en: "Daemon interpreter" } },
        { role: "claude", path: null, realpath: null, exists: false, note: { zh: "c", en: "claude CLI" } },
      ] },
    panes: { full_disk: "x", screen: "y", microphone: "z", notifications: "n" },
    doctor: [{ name: "launchd volume access", status: "FAIL", detail: "EPERM", fix: "grant FDA", failure_id: "deploy_blind_tcc" }],
    doctor_ran_at: "2026-09-02T00:00:00Z", doctor_ok: true,
    vault: { status: "unknown", root: "/Users/demo/Documents/Obsidian Vault" }, ...over,
  };
}

const ENGINE_READY = { cli_path: "/usr/local/bin/claude", version: "1.0.99 (Claude Code)", auth: "oauth", auth_sources: { oauth: true, env_key: false, secrets_file: false, legacy_file: false }, ready: true };
const ENGINE_NONE = { cli_path: null, version: null, auth: null, auth_sources: { oauth: false, env_key: false, secrets_file: false, legacy_file: false }, ready: false };
const HEALTH_OK = { verdict: "ok", heartbeat: { age_s: 3, phase: "dashboard", pid: 1, interval: 10, stale_after_s: 90, stale: false }, dashboard: { generated_at: "2026-09-02T00:00:00Z", age_s: 30, stale: false }, loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "x" };
const HEALTH_DEAD = { verdict: "stale", heartbeat: null, dashboard: null, loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "x" };

function diagnostics(): DiagnosticsSnapshot {
  return {
    doctor: { ok: true, fast: true, rc: 0, home: "/h", ran_at: "2026-09-02T00:00:00Z", checks: [
      { name: "claude CLI", status: "OK", detail: "found", fix: "" },
      { name: "actd heartbeat", status: "FAIL", detail: "stalled", fix: "kickstart it" },
    ] },
    health: { verdict: "stalled", heartbeat: { age_s: 400, phase: "idle", pid: 1, interval: 10, stale_after_s: 90, stale: true }, dashboard: null,
      loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "x" },
    deploy_state: { status: "deployed", version: "0.48.22" },
    radar_sources: { gmail: { enabled: true, skip_reason: "no_credentials", last_ok: null, stale: false } },
    install_report: { version: "0.48.22", generated_at: "x", ok: true, steps: [{ name: "cron", status: "skipped_tcc", detail: "EPERM" }] },
    registry_backend: "sqlite",
    logs: [{ name: "actd.launchd.log", path: "/l/actd.launchd.log", size: 2048, mtime: 1 }],
  };
}

function setup(over: Partial<SetupSnapshot> = {}): SetupSnapshot {
  return { needed: true, done: false, config_exists: false, config_example_exists: true,
    secrets: { "slack-user-token.txt": false }, home: "/h", protected_location: false, ...over };
}

beforeEach(() => {
  resetStoreForTests();
  const mocks: Array<{ mockReset: () => unknown }> = [
    vi.mocked(fetchBoard), vi.mocked(fetchHealth), vi.mocked(fetchPermissions), vi.mocked(fetchDiagnostics), vi.mocked(fetchDoctor),
    vi.mocked(fetchLogTail), vi.mocked(fetchSetup), vi.mocked(fetchSecrets), vi.mocked(fetchSetupEngine), vi.mocked(postSetupStep), vi.mocked(postAction),
    vi.mocked(postRepairActd), vi.mocked(postSeedDashboard), vi.mocked(postRevealTarget), vi.mocked(putSecret), vi.mocked(verifySecret),
    vi.mocked(navigate),
  ];
  for (const fn of mocks) fn.mockReset();
  vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [] });
  vi.mocked(fetchSetupEngine).mockResolvedValue(ENGINE_READY);
  vi.mocked(fetchHealth).mockResolvedValue(HEALTH_OK);
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  delete (window as Window & { webkit?: unknown }).webkit;
});

afterEach(cleanup);

describe("PermissionsPage", () => {
  it("lists FDA executables with copyable paths, TCC doctor rows, and is honest without the bridge", async () => {
    vi.mocked(fetchPermissions).mockResolvedValue(permissions());
    renderEn(<PermissionsPage />);
    await screen.findByText("daemon_python");
    expect(screen.getByText("/usr/bin/python3")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy path" })).toBeTruthy();
    expect(screen.getByText("(path not resolved)")).toBeTruthy();
    expect(screen.getByText("launchd volume access")).toBeTruthy();
    expect(screen.getByText(/can only be probed by the board app itself/)).toBeTruthy();
    expect(screen.getByText(/sits in a location macOS protects/)).toBeTruthy();
  });

  it("re-probe asks the server to bypass its cache", async () => {
    vi.mocked(fetchPermissions).mockResolvedValue(permissions());
    renderEn(<PermissionsPage />);
    await screen.findByText("daemon_python");
    fireEvent.click(screen.getByRole("button", { name: "Re-probe" }));
    await waitFor(() => expect(fetchPermissions).toHaveBeenLastCalledWith(true));
  });

  it("statusLabel covers the three TCC states", () => {
    expect(statusLabel("granted", en)).toBe("Granted");
    expect(statusLabel("denied", en)).toBe("Denied");
    expect(statusLabel("unknown", en)).toMatch(/not asked/);
  });

  it("footer is Done during first run (→ wizard) and Close afterwards (→ board)（原生 firstRun）", async () => {
    vi.mocked(fetchPermissions).mockResolvedValue(permissions());
    vi.mocked(fetchSetup).mockResolvedValue(setup({ needed: true }));
    renderEn(<PermissionsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Done" }));
    expect(String(vi.mocked(navigate).mock.calls[0][0])).toContain("page=setup");
    cleanup();
    resetStoreForTests();
    vi.mocked(fetchSetup).mockResolvedValue(setup({ needed: false, done: true }));
    renderEn(<PermissionsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Close" }));
    expect(String(vi.mocked(navigate).mock.calls[1][0])).not.toContain("page=");
  });

  it("with the bridge: capability rows show native words and Grant… asks the shell (vault included)", async () => {
    const calls: unknown[] = [];
    (window as Window & { webkit?: unknown }).webkit = { messageHandlers: { zaiShell: { postMessage: async (body: unknown) => {
      calls.push(body);
      return { recording: { available: true, on: false, mode: "off", engine_running: false, resume_mode: "screen_audio" }, captions: {},
        permissions: { screen: "denied", microphone: "unknown", notifications: "unknown", vault: "unknown" }, launch_at_login: false, hotkey: "x" };
    } } } };
    vi.mocked(fetchPermissions).mockResolvedValue(permissions());
    vi.mocked(fetchSetup).mockResolvedValue(setup());
    renderEn(<PermissionsPage />);
    await screen.findByText("Notes vault access (Documents)");
    expect(screen.getByText("Not requested yet", { selector: "[data-kind=vault] .perm-status" })).toBeTruthy();
    expect(screen.getByText("Not granted", { selector: "[data-kind=screen] .perm-status" })).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Grant…" })[1]);
    await waitFor(() => expect(calls.some((c) => JSON.stringify(c) === JSON.stringify({ method: "requestPermission", kind: "vault" }))).toBe(true));
    // 录制关着且向导没完成 → 一次性同意块；「暂不」答过（本会话）后换成状态行，恢复模式 screen_audio → 开启(屏幕+音频)
    fireEvent.click(screen.getByRole("button", { name: "Not Now" }));
    expect(await screen.findByRole("button", { name: "Turn On (screen + audio)" })).toBeTruthy();
    expect(consentPending({ mode: "off" } as never, { done: true } as never)).toBe(false);   // 向导完成 = 问过了
    expect(consentPending({ mode: "screen" } as never, null)).toBe(false);                    // 录制开着 = 同意过
    expect(consentPending({ mode: "off" } as never, null)).toBe(true);
  });
});

describe("DiagnosticsPage", () => {
  it("renders doctor rows, deploy state, install steps and loads a log tail", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics());
    vi.mocked(fetchLogTail).mockResolvedValue({ name: "actd.launchd.log", path: "/l/actd.launchd.log", size: 2048, lines: ["a", "b"], truncated: true });
    renderEn(<DiagnosticsPage />);
    await screen.findByText("actd heartbeat");
    expect(screen.getByText("1 check(s) failed — each has its own button")).toBeTruthy();
    expect(screen.getByText("(1 ok / 0 warn)")).toBeTruthy();
    expect(screen.getByText("deployed")).toBeTruthy();
    expect(screen.getByText("skipped_tcc")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Pick a log"), { target: { value: "actd.launchd.log" } });
    await waitFor(() => expect(fetchLogTail).toHaveBeenCalledWith("actd.launchd.log", 300));
    await screen.findByText(/tail only/);
    expect(document.querySelector(".diag-log-tail")?.textContent).toBe("a\nb");
  });

  it("full checkup calls fetchDoctor(fast=false, refresh=true)", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics());
    vi.mocked(fetchDoctor).mockResolvedValue({ ...diagnostics().doctor, fast: false });
    renderEn(<DiagnosticsPage />);
    await screen.findByText("actd heartbeat");
    fireEvent.click(screen.getByRole("button", { name: /Run diagnostics/ }));
    await waitFor(() => expect(fetchDoctor).toHaveBeenCalledWith(false, true));
  });

  it("doctor rows carry the §25 one-click action; Reveal file posts {target:'config'}", async () => {
    const diag = diagnostics();
    diag.doctor.checks.push({ name: "config", status: "FAIL", detail: "broken yaml", fix: "restore", failure_id: "config_invalid" },
      { name: "cron chain", status: "FAIL", detail: "missing", fix: "bash install.sh", failure_id: "cron_missing" });
    vi.mocked(fetchDiagnostics).mockResolvedValue(diag);
    vi.mocked(postRevealTarget).mockResolvedValue({ ok: true });
    renderEn(<DiagnosticsPage />);
    await screen.findByText("actd heartbeat");
    expect(screen.getByRole("link", { name: "How to fix" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reveal file" }));
    await waitFor(() => expect(postRevealTarget).toHaveBeenCalledWith("config"));
    expect(failureActionLabel("claude_cli_missing", en)).toBe("Install page");
    expect(failureActionLabel("screen_tcc_lost", en)).toBe("Grant…");
    expect(failureActionLabel("dashboard_stale", en)).toBe("Fix now");
    expect(failureActionLabel("nope", en)).toBeNull();
  });

  it("doctorSummary counts statuses（原生 DepsView：零失败说全部通过 ✓，判词与计数两个节点）", () => {
    expect(doctorSummary(diagnostics().doctor, en)).toEqual({ verdict: "1 check(s) failed — each has its own button", counts: "(1 ok / 0 warn)" });
    const clean = { ...diagnostics().doctor, checks: diagnostics().doctor.checks.filter((c) => c.status !== "FAIL") };
    expect(doctorSummary(clean, en)).toEqual({ verdict: "All checks passed ✓", counts: "(1 ok / 0 warn)" });
    expect(fullReportText(diagnostics().doctor)).toBe("[ok] claude CLI: found\n[fail] actd heartbeat: stalled\n    fix: kickstart it");
  });
});

describe("SetupPage", () => {
  it("opens on the welcome step when config.yaml is missing and copies the example there", async () => {
    vi.mocked(fetchSetup).mockResolvedValue(setup());
    vi.mocked(fetchPermissions).mockResolvedValue(permissions({ fda: { ...permissions().fda, needed: false } }));
    vi.mocked(postSetupStep).mockResolvedValue({ ok: true, path: "/h/config.yaml", setup: setup({ config_exists: true }) });
    renderEn(<SetupPage />);
    const button = await screen.findByRole("button", { name: "Create from config.example.yaml" });
    expect(screen.getByText("Step 1 of 7")).toBeTruthy();
    fireEvent.click(button);
    await waitFor(() => expect(postSetupStep).toHaveBeenCalledWith("config-from-example"));
    await screen.findByText("config.yaml exists ✓");
    expect(firstOpenStep(false)).toBe("welcome");
    expect(firstOpenStep(true)).toBe("engine");
    expect(stepFromSearch("?page=setup&step=finale")).toBe("finale");
    expect(stepFromSearch("?page=setup&step=bogus")).toBeNull();
  });

  it("engine step: detected login shows Connected; no CLI shows the install path + Re-detect", async () => {
    vi.mocked(fetchSetup).mockResolvedValue(setup({ config_exists: true }));
    vi.mocked(fetchPermissions).mockResolvedValue(permissions());
    window.history.replaceState(null, "", "/?page=setup&step=engine");
    renderEn(<SetupPage />);
    await screen.findByText("Connected — nothing to configure");
    expect(screen.getAllByText("Claude Code login").length).toBeGreaterThan(0); // 认证方式: 一行 + 梯子一行
    vi.mocked(fetchSetupEngine).mockResolvedValue(ENGINE_NONE);
    fireEvent.click(screen.getByRole("button", { name: "Re-detect" }));
    await screen.findByText("npm install -g @anthropic-ai/claude-code");
    expect(screen.getByRole("link", { name: "Open install page" })).toBeTruthy();
    expect(fetchSetupEngine).toHaveBeenCalledTimes(2);
  });

  it("paste-a-key verifies BEFORE saving: invalid never saved, valid saved once (先验后存)", async () => {
    vi.mocked(fetchSetup).mockResolvedValue(setup({ config_exists: true }));
    vi.mocked(fetchPermissions).mockResolvedValue(permissions());
    vi.mocked(fetchSetupEngine).mockResolvedValue(ENGINE_NONE);
    vi.mocked(verifySecret).mockResolvedValueOnce({ ok: false, network: false, detail: "HTTP 401", extra: {} });
    window.history.replaceState(null, "", "/?page=setup&step=engine");
    renderEn(<SetupPage />);
    const input = await screen.findByPlaceholderText("sk-ant-… (verifies on paste)");
    fireEvent.change(input, { target: { value: "sk-ant-bad" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/Invalid key/);
    expect(verifySecret).toHaveBeenCalledWith("anthropic-api-key.txt", "sk-ant-bad");
    expect(putSecret).not.toHaveBeenCalled();
    vi.mocked(verifySecret).mockResolvedValueOnce({ ok: true, network: false, detail: "ok", extra: {} });
    vi.mocked(putSecret).mockResolvedValue({ name: "anthropic-api-key.txt", label: { zh: "k", en: "k" }, present: true, verifiable: true, mtime: 1 });
    fireEvent.change(input, { target: { value: "sk-ant-good" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("✅ Key valid — saved");
    expect(putSecret).toHaveBeenCalledWith("anthropic-api-key.txt", "sk-ant-good");
  });

  it("finale rows: dead daemon → Start it (POST repair), no data → Generate now (seed); Done writes the marker", async () => {
    vi.mocked(fetchSetup).mockResolvedValue(setup({ config_exists: true }));
    vi.mocked(fetchPermissions).mockResolvedValue(permissions({ doctor: [{ name: "cron disk access", status: "FAIL", detail: "blocked", fix: "", failure_id: "cron_fda_blocked" }] }));
    vi.mocked(fetchHealth).mockResolvedValue(HEALTH_DEAD);
    vi.mocked(postRepairActd).mockRejectedValue(new Error("not loaded"));
    vi.mocked(postSeedDashboard).mockResolvedValue({ ok: false, rc: 1, error: "boom" });
    vi.mocked(postSetupStep).mockResolvedValue({ ok: true, setup: setup({ config_exists: true, done: true, needed: false }) });
    window.history.replaceState(null, "", "/?page=setup&step=finale");
    renderEn(<SetupPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Start it" }));
    await screen.findByText("Start failed:");
    fireEvent.click(screen.getByRole("button", { name: "Generate now" }));
    await screen.findByText("Seeding failed:");
    expect(screen.getByText("boom")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Grant…" }).getAttribute("href")).toContain("page=permissions");
    expect(cronVerdict([{ name: "cron disk access", status: "OK", detail: "", fix: "" }])).toBe("ok");
    expect(cronVerdict([])).toBe("neutral");
    expect(cronVerdict(undefined)).toBe("checking");
    expect(daemonRunning(HEALTH_OK)).toBe(true);
    expect(daemonRunning(HEALTH_DEAD)).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(postSetupStep).toHaveBeenCalledWith("complete"));
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(String(vi.mocked(navigate).mock.calls[0][0])).not.toContain("page=");
  });
});

describe("ArchivePage", () => {
  it("lists archived rows and unarchive posts {action:'unarchive', comment:null, id}", async () => {
    vi.mocked(fetchBoard).mockResolvedValue({
      generated_at: "x", counts: { archived: 1 }, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [],
      archived: [{ id: "R-7", title: "Old thread", archive_reason: "user", prev_status: "delivered", archived_at: "2026-08-01T00:00:00Z" }],
    } as unknown as Board);
    vi.mocked(postAction).mockResolvedValue({});
    await refreshBoard();
    renderEn(<ArchivePage />);
    expect(screen.getByText("Old thread")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Put back" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({ action: "unarchive", comment: null, id: "R-7" }));
  });
});

describe("RepairButton", () => {
  it("posts the repair and shows the server's 409 sentence on failure", async () => {
    vi.mocked(postRepairActd).mockRejectedValue(new Error("com.zelin.aiassistant.actd is not loaded in launchd - run `bash install.sh`"));
    renderEn(<RepairButton verdict="stalled" />);
    fireEvent.click(screen.getByRole("button", { name: "Fix now" }));
    await screen.findByText(/not loaded in launchd/);
    expect(postRepairActd).toHaveBeenCalledTimes(1);
    // 原生 Freshness.swift：失败后换成「自动修复没成功：」+「再试一次」+ 手动命令
    expect(screen.getByText("Auto-repair didn't work:")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(screen.getByText("Manual command:")).toBeTruthy();
  });
});

describe("route + app helpers", () => {
  it("knows the new pages and the anchor param", () => {
    for (const page of ["archive", "permissions", "diagnostics", "setup", "ask", "deps", "ingest", "about"]) expect(readPage(`?page=${page}`)).toBe(page);
    expect(readAnchor("?page=settings&anchor=live_captions")).toBe("live_captions");
    expect(readAnchor("?anchor=<script>")).toBeNull();
    expect(readAnchor("")).toBeNull();
  });

  it("badgeCount = proposals + needs_input + review (counts first) and setup redirect only from the board", () => {
    expect(badgeCount(null)).toBe(0);
    expect(badgeCount({ counts: { needs_approval: 2, needs_input: 1, review: 3 } })).toBe(6);
    expect(badgeCount({ needs_approval: [{}], needs_input: [], review: [{}, {}] })).toBe(3);
    expect(shouldRedirectToSetup("board", true)).toBe(true);
    expect(shouldRedirectToSetup("settings", true)).toBe(false);
    expect(shouldRedirectToSetup("board", false)).toBe(false);
    expect(shouldRedirectToSetup("board", undefined)).toBe(false);
  });
});
