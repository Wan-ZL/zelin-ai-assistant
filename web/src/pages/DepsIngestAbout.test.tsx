// §66 清账轮 SLICE A（deps / ingest / about）：依赖检查快速行（doctor + 壳 + 凭证 + cron 探针四态；D30 起是设置页一区）、
// 录制页手动触发（同一条脚本、同一套退出码 → 完成 ✓ / 已有 ingest 在运行 / 失败 (exit N)）、引擎诊断行、最近活动时间戳、
// 关于页更新行逐态。v2.0.0：雷达健康三态词、gh / Slack / Gmail 三行、一键更新的 kickstart 与 409 → release 页、卸载失败弹窗
// 随各自的功能退役（升级 / 卸载的新判例在 components/settings/AboutSection.test.tsx）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchAbout, fetchDiagnostics, fetchFailures, fetchIngestJob, fetchSecrets, postFolderOpen, postIngestExport, postIngestRun,
  postUpdateCheck, postUpdateInstall,
} from "../api";
import { AboutSection, updateStatusText, updateView } from "../components/settings/AboutSection";
import { buildDepRows, CRON_PROBE_FRESH_MS, cronVerdict } from "../components/settings/DepRows";
import { DepsSection } from "../components/settings/DepsSection";
import { LanguageContext } from "../i18n";
import { applyShellState, resetShellBridgeForTests, type ShellState } from "../shellBridge";
import { resetStoreForTests } from "../store";
import type { AboutInfo, DiagnosticsSnapshot, DoctorReport } from "../types";
import { IngestPage, stamp, verdictOf } from "./IngestPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    fetchRecaps: vi.fn().mockResolvedValue({ recaps: [] }), fetchHealth: vi.fn(), fetchDiagnostics: vi.fn(), fetchSecrets: vi.fn(), fetchFailures: vi.fn(),
    fetchAbout: vi.fn(), postFolderOpen: vi.fn(), postIngestExport: vi.fn(), postIngestRun: vi.fn(), fetchIngestJob: vi.fn(), postUpdateCheck: vi.fn(),
    postUpdateInstall: vi.fn(), postSetupStep: vi.fn(),
  };
});

const en = (_zh: string, english: string) => english;
const zh = (chinese: string) => chinese;
const renderEn = (node: React.ReactNode) => render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);

const report: DoctorReport = { ok: true, fast: true, rc: 0, home: "/h", ran_at: "x", checks: [
  { name: "node/npx", status: "ok", detail: "/opt/homebrew/bin/npx", fix: "" },
  { name: "claude CLI", status: "ok", detail: "/u/claude", fix: "" },
  { name: "daemon python", status: "ok", detail: "/usr/bin/python3 (Python 3.9, PyYAML importable)", fix: "" },
  { name: "obsidian vault", status: "ok", detail: "/v (+ ingest inbox)", fix: "" },
] };
const secrets = { secrets: [
  { name: "anthropic-api-key.txt", label: { zh: "k", en: "k" }, present: true, verifiable: true, mtime: 1 },
] };
const shell: ShellState = {
  recording: { available: true, on: true, mode: "screen_audio", engine_running: true, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen" },
  permissions: { screen: "granted", microphone: "unknown", notifications: "granted", vault: "unknown" },
  launch_at_login: false,
};
const NOW = Date.parse("2026-09-02T12:00:00Z");

function diagnostics(over: Partial<DiagnosticsSnapshot> = {}): DiagnosticsSnapshot {
  return {
    doctor: report,
    health: { verdict: "ok", server: { ok: true }, cron: null, recap: null, checked_at: "x" },
    install_report: null, logs: [],
    cron_probe: { ts: "2026-09-02T11:30:00Z", read_ok: true, protected_path: "/Users/d/Documents/V" },
    activity: { screenpipe_db: { path: "/d", mtime: 1788350100 }, recap_log: { path: "/a", mtime: 1788350000 }, unprocessed: { path: "/u", mtime: null, readable: false } },
    ...over,
  } as DiagnosticsSnapshot;
}

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
  }
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
  for (const fn of [fetchDiagnostics, fetchSecrets, fetchFailures, fetchAbout, postFolderOpen, postIngestExport, postIngestRun, fetchIngestJob, postUpdateCheck, postUpdateInstall]) vi.mocked(fn).mockReset();
  vi.mocked(fetchSecrets).mockResolvedValue(secrets);
  vi.mocked(fetchFailures).mockResolvedValue({ failures: { engine_ffmpeg_missing: { zh: "缺 ffmpeg", en: "ffmpeg missing", action_id: "install_ffmpeg" }, screen_tcc_lost: { zh: "授权丢了", en: "grant lost", action_id: null } } });
  vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics());
  window.history.replaceState(null, "", "/?page=settings&anchor=deps");
  delete (window as Window & { webkit?: unknown }).webkit;
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("DepRows（原生 DepsModel.check 的行，v2.0.0 剩九行）", () => {
  it("cronVerdict mirrors cronFDARow's four states", () => {
    expect(cronVerdict(null, NOW, en).ok).toBe(false);
    expect(cronVerdict(null, NOW, en).detail).toMatch(/No probe data yet/);
    const stale = cronVerdict({ ts: "2026-09-02T06:00:00Z", read_ok: true, protected_path: "/v" }, NOW, en);
    expect(stale).toEqual({ ok: false, detail: "Last probe 6h ago — the scheduled jobs may have stopped (run Diagnostics)" });
    expect(cronVerdict({ ts: "2026-09-02T11:30:00Z", read_ok: true, protected_path: "/v" }, NOW, zh)).toEqual({ ok: true, detail: "定时任务能读取 /v" });
    expect(cronVerdict({ ts: "2026-09-02T11:30:00Z", read_ok: false, protected_path: "/v" }, NOW, en).detail).toMatch(/^macOS blocks the scheduled jobs from reading \/v/);
    expect(CRON_PROBE_FRESH_MS).toBe(2 * 3600 * 1000);
  });

  it("builds the native row order from doctor + shell + secrets + probe (browser rows are honest)", () => {
    const rows = buildDepRows(report, null, secrets, null, null, "en", en);
    expect(rows.map((r) => r.id)).toEqual(["npx", "engine", "screen_tcc", "mic_tcc", "claude", "pyyaml", "vault", "anthropic", "cron_fda"]);
    expect(rows.map((r) => r.ok)).toEqual([true, null, null, null, true, true, true, true, false]);
    expect(rows[1].detail).toMatch(/Only probeable inside the app \(shell\)/);
    const withShell = buildDepRows(report, shell, secrets, null, { ts: "2026-09-02T11:30:00Z", read_ok: true, protected_path: "/v" }, "en", en);
    expect(withShell[1].ok).toBe(true);
    expect(withShell[3].ok).toBe(false);          // screen_audio 模式且麦克风 unknown → 阻塞
    expect(withShell[8].ok).toBe(true);
    const dead = buildDepRows(report, { ...shell, recording: { ...shell.recording, engine_running: false, diagnosis: "engine_crashed" } }, secrets, null, null, "en", en);
    expect(dead[1].ok).toBe(false);
    const withCatalog = buildDepRows(report, { ...shell, recording: { ...shell.recording, engine_running: false, diagnosis: "engine_crashed" } }, secrets,
      { failures: { engine_crashed: { zh: "崩了", en: "crashed", action_id: null } } }, null, "en", en);
    expect(withCatalog[1].detail).toBe("crashed");   // 目录的句子；没目录时退回 id
  });

  it("DepsSection renders the rows (no radar health any more), Reveal posts the obsidian_raw key", async () => {
    vi.mocked(postFolderOpen).mockResolvedValue({ ok: true, key: "obsidian_raw", path: "/v" });
    renderEn(<DepsSection />);
    await screen.findByText("cron can read /Users/d/Documents/V");
    expect(screen.getByText("(managed in-app)")).toBeTruthy();
    expect(screen.queryByText("Radar Health")).toBeNull();
    expect(screen.queryByRole("button", { name: "Fix with AI" })).toBeNull();
    expect(screen.getByText("All checks passed ✓")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reveal" }));
    await waitFor(() => expect(postFolderOpen).toHaveBeenCalledWith("obsidian_raw"));
    expect(screen.getByText("Full report")).toBeTruthy();
  });
});

describe("IngestPage（原生 IngestView 手动触发 / 引擎诊断 / 最近活动）", () => {
  function installShell(state: ShellState) {
    (window as Window & { webkit?: unknown }).webkit = { messageHandlers: { zaiShell: { postMessage: async () => state } } };
    applyShellState(state);
  }

  it("Export Now / Ingest Now start a job, poll it, and read the receipt like the native model", async () => {
    const running = { id: "j1", script: "ingest/screenpipe-export.sh", state: "running", started_at: "x" };
    vi.mocked(postIngestExport).mockResolvedValue({ ok: true, job: "j1", state: "running", script: running.script });
    vi.mocked(postIngestRun).mockResolvedValue({ ok: true, job: "j2", state: "running", script: "ingest/process-screenpipe.sh" });
    vi.mocked(fetchIngestJob).mockImplementation(async (id: string) => id === "j1"
      ? { ...running, state: "done", ok: false, rc: 1, skipped: false, tail: "rsync: not found", seconds: 0.3 }
      : { id: "j2", script: "ingest/process-screenpipe.sh", state: "done", started_at: "x", ok: false, rc: 3, skipped: true, tail: "lock held", seconds: 0.1 });
    renderEn(<IngestPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Export Now" }));
    expect(screen.getByText("Running…")).toBeTruthy();
    await screen.findByText("Failed (exit 1)");
    expect(screen.getByText("rsync: not found")).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "View log" })).toHaveLength(2);   // 失败行的 + 最近活动的
    fireEvent.click(screen.getByRole("button", { name: "Ingest Now" }));
    await screen.findByText("Already running — skipped");
    expect(postIngestExport).toHaveBeenCalledTimes(1);
    expect(postIngestRun).toHaveBeenCalledTimes(1);
    expect(fetchIngestJob).toHaveBeenCalledWith("j1");
    expect(fetchIngestJob).toHaveBeenCalledWith("j2");
    // 判词纯函数：export 的 exit 3 不是跳过
    expect(verdictOf({ id: "x", script: "s", state: "done", started_at: "t", ok: false, rc: 3, tail: "" }, null)).toEqual({ kind: "failed", rc: 3, tail: "" });
    expect(verdictOf({ id: "x", script: "s", state: "done", started_at: "t", ok: true, rc: 0 }, 3)).toEqual({ kind: "done" });
  });

  it("recent activity: HH:mm for the db, No files vs unreadable, No log", async () => {
    renderEn(<IngestPage />);
    expect(await screen.findByText(/cannot read that folder/)).toBeTruthy();
    expect(screen.getByText(stamp(1788350000, true))).toBeTruthy();
    cleanup();
    resetStoreForTests();
    vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics({ activity: { screenpipe_db: { path: "/d", mtime: null }, recap_log: { path: "/a", mtime: null }, unprocessed: { path: "/u", mtime: null, readable: true } } }));
    renderEn(<IngestPage />);
    expect(await screen.findByText("No files")).toBeTruthy();
    expect(screen.getByText("No log")).toBeTruthy();
    expect(stamp(0, false)).toMatch(/^\d\d:\d\d$/);
    expect(stamp(0, true)).toMatch(/^\d{4}-\d\d-\d\d \d\d:\d\d$/);
  });

  it("with the shell: engine diagnosis row (ffmpeg → Install ffmpeg + Installed — restart engine) and the db stamp", async () => {
    installShell({ ...shell, recording: { ...shell.recording, engine_running: false, diagnosis: "engine_ffmpeg_missing" } });
    renderEn(<IngestPage />);
    await screen.findByText("ffmpeg missing");
    expect(screen.getByRole("link", { name: "Install ffmpeg" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Installed — restart engine" })).toBeTruthy();
    expect(screen.getByText("Last write")).toBeTruthy();
    expect(screen.getByText("Not recording")).toBeTruthy();
  });

  it("with the shell: TCC lost banner carries the catalog sentence and Grant…; a crashed engine links the engine log", async () => {
    installShell({ ...shell, recording: { ...shell.recording, tcc_lost: true, screen_permission: false } });
    renderEn(<IngestPage />);
    await screen.findByText("grant lost");
    expect(screen.getByRole("button", { name: "Grant…" })).toBeTruthy();
    cleanup();
    resetStoreForTests();
    installShell({ ...shell, recording: { ...shell.recording, engine_running: false, diagnosis: "engine_crashed" } });
    renderEn(<IngestPage />);
    const link = await screen.findByRole("link", { name: "View engine log" });
    expect(link.getAttribute("href")).toContain("log=engine.log");
  });
});

describe("AboutSection（原生 updateSection / updateStatus / confirmUninstall）", () => {
  const about: AboutInfo = { version: "1.0.7", home: "/h", repo: "/r", update_available: { latest: "1.0.8", url: "https://rel/1.0.8" }, update_check: { checked_at: "2026-09-02T11:00:00Z", latest: "1.0.8", url: "https://rel/1.0.8" } };

  it("updateView / updateStatusText follow the native ladder", () => {
    const v = updateView(about, null);
    expect(v).toMatchObject({ current: "1.0.7", latest: "1.0.8", updateAvailable: true, checkedAt: "2026-09-02T11:00:00Z", failed: false, enabled: true });
    expect(updateStatusText(v, true, en)).toBe("Checking…");
    expect(updateStatusText(v, false, en)).toBeNull();
    const failed = updateView(about, { ok: false, error: "rate_limited" });
    expect(updateStatusText(failed, false, en)).toMatch(/rate limit/);
    expect(updateStatusText(updateView(about, { ok: false, error: "network" }), false, en)).toMatch(/network unavailable/);
    expect(updateStatusText(updateView(about, { ok: true, enabled: false, latest: null }), false, en)).toMatch(/turned off|are off/);
    expect(updateView(about, { ok: true, current: "1.0.8", latest: "1.0.8", update_available: false, checked_at: "2026-09-02T11:30:00Z" })).toMatchObject({ current: "1.0.8", latest: "1.0.8", updateAvailable: false });
  });

  it("Check now shows Checking… then Up to date (last checked: …) when latest == current", async () => {
    vi.mocked(fetchAbout).mockResolvedValue({ ...about, update_available: null, update_check: { checked_at: "2026-09-02T11:00:00Z", latest: "1.0.7" } });
    vi.mocked(postUpdateCheck).mockResolvedValue({ ok: true, enabled: true, current: "1.0.7", latest: "1.0.7", update_available: false, checked_at: "2026-09-02T11:59:00Z" });
    renderEn(<AboutSection />);
    expect(await screen.findByText("Up to date")).toBeTruthy();
    expect(screen.getByText("(last checked:")).toBeTruthy();
    const button = screen.getByRole("button", { name: "Check now" });
    expect(button.getAttribute("title")).toBe("Check for updates");
    fireEvent.click(button);
    expect(screen.getByText("Checking…")).toBeTruthy();
    await waitFor(() => expect(postUpdateCheck).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Up to date")).toBeTruthy();
  });
});
