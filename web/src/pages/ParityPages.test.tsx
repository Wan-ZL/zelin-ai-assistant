// §66 P4 parity 页：权限体检（FDA 清单 + 可复制路径 + 无桥诚实说明）、诊断（doctor 表 + 日志尾巴）、
// 首次运行向导（config-from-example → 完成标记）、永久性完成整页（unarchive）、横幅一键修复、
// 路由新页 / anchor、Dock 徽章计数与向导跳转判定。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchBoard, fetchDiagnostics, fetchDoctor, fetchHealth, fetchLogTail, fetchPermissions, fetchSecrets, fetchSetup,
  postAction, postRepairActd, postSetupStep,
} from "../api";
import { badgeCount, shouldRedirectToSetup } from "../app";
import { RepairButton } from "../components/shell/PipelineBanner";
import { LanguageContext } from "../i18n";
import { navigate, readAnchor, readPage } from "../route";
import { refreshBoard, resetStoreForTests } from "../store";
import type { Board, DiagnosticsSnapshot, PermissionsSnapshot, SetupSnapshot } from "../types";
import { ArchivePage } from "./ArchivePage";
import { DiagnosticsPage, doctorSummary } from "./DiagnosticsPage";
import { PermissionsPage, statusLabel } from "./PermissionsPage";
import { firstOpenStep, SetupPage } from "./SetupPage";

vi.mock("../route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../route")>();
  return { ...actual, navigate: vi.fn() };
});

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    fetchBoard: vi.fn(), fetchHealth: vi.fn(), fetchPermissions: vi.fn(), fetchDiagnostics: vi.fn(), fetchDoctor: vi.fn(),
    fetchLogTail: vi.fn(), fetchSetup: vi.fn(), fetchSecrets: vi.fn(), postSetupStep: vi.fn(), postAction: vi.fn(),
    postRepairActd: vi.fn(),
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
    doctor_ran_at: "2026-09-02T00:00:00Z", doctor_ok: true, ...over,
  };
}

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
    vi.mocked(fetchLogTail), vi.mocked(fetchSetup), vi.mocked(fetchSecrets), vi.mocked(postSetupStep), vi.mocked(postAction), vi.mocked(postRepairActd),
    vi.mocked(navigate),
  ];
  for (const fn of mocks) fn.mockReset();
  vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [] });
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
});

describe("DiagnosticsPage", () => {
  it("renders doctor rows, deploy state, install steps and loads a log tail", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics());
    vi.mocked(fetchLogTail).mockResolvedValue({ name: "actd.launchd.log", path: "/l/actd.launchd.log", size: 2048, lines: ["a", "b"], truncated: true });
    renderEn(<DiagnosticsPage />);
    await screen.findByText("actd heartbeat");
    expect(screen.getByText(/1 failed \(1 ok \/ 0 warn\)/)).toBeTruthy();
    expect(screen.getByText("deployed")).toBeTruthy();
    expect(screen.getByText("skipped_tcc")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Pick a log"), { target: { value: "actd.launchd.log" } });
    await waitFor(() => expect(fetchLogTail).toHaveBeenCalledWith("actd.launchd.log", 300));
    await screen.findByText(/tail only/);
    expect(document.querySelector(".diag-log")?.textContent).toBe("a\nb");
  });

  it("full checkup calls fetchDoctor(fast=false, refresh=true)", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics());
    vi.mocked(fetchDoctor).mockResolvedValue({ ...diagnostics().doctor, fast: false });
    renderEn(<DiagnosticsPage />);
    await screen.findByText("actd heartbeat");
    fireEvent.click(screen.getByRole("button", { name: /Run diagnostics/ }));
    await waitFor(() => expect(fetchDoctor).toHaveBeenCalledWith(false, true));
  });

  it("doctorSummary counts statuses（原生 DepsView：零失败说全部通过 ✓）", () => {
    expect(doctorSummary(diagnostics().doctor, en)).toBe("1 failed (1 ok / 0 warn)");
    const clean = { ...diagnostics().doctor, checks: diagnostics().doctor.checks.filter((c) => c.status !== "FAIL") };
    expect(doctorSummary(clean, en)).toBe("All checks passed ✓ (1 ok / 0 warn)");
  });
});

describe("SetupPage", () => {
  it("opens on the config step when config.yaml is missing and copies the example", async () => {
    vi.mocked(fetchSetup).mockResolvedValue(setup());
    vi.mocked(fetchPermissions).mockResolvedValue(permissions({ fda: { ...permissions().fda, needed: false } }));
    vi.mocked(postSetupStep).mockResolvedValue({ ok: true, path: "/h/config.yaml", setup: setup({ config_exists: true }) });
    renderEn(<SetupPage />);
    const button = await screen.findByRole("button", { name: "Create from config.example.yaml" });
    fireEvent.click(button);
    await waitFor(() => expect(postSetupStep).toHaveBeenCalledWith("config-from-example"));
    await screen.findByText("config.yaml exists ✓");
    expect(firstOpenStep(false)).toBe("config");
    expect(firstOpenStep(true)).toBe("fda");
  });

  it("finish writes the completion marker", async () => {
    vi.mocked(fetchSetup).mockResolvedValue(setup({ config_exists: true }));
    vi.mocked(fetchPermissions).mockResolvedValue(permissions());
    vi.mocked(postSetupStep).mockResolvedValue({ ok: true, setup: setup({ config_exists: true, done: true, needed: false }) });
    renderEn(<SetupPage />);
    fireEvent.click(await screen.findByRole("button", { name: "4. Done" }));
    fireEvent.click(await screen.findByRole("button", { name: "Finish and open the board" }));
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
