// 录制页引擎诊断块与刷新语义的 parity 判例（CONTRACT §61.1 / §68.3 / §68.4 追记；批 `ingest-page-diagnosis-ui`，原生 Pages.swift
// EngineDiagnosisRow / IngestView）：npm 首次下载带 spinner 且不是错误色；壳给的 `log_tail` 原文照印成等宽日志尾、空则不渲；
// ffmpeg 缺失也给「查看引擎日志」；consent-race 自愈的绿色 ✓ 句在 rec.note 之前；进场与引擎行「刷新」经桥 `refreshRecording`
// 立刻探引擎、老壳 UNKNOWN_METHOD 退回纯读 getState、最近活动块的「刷新」不探引擎；手动触发一轮跑完（成功 / 跳过 / 失败）
// 立刻重拉 diagnostics；ingest 的「查看日志」翻开 `screenpipe-auto.log`、导出的只到日志清单。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchDiagnostics, fetchFailures, fetchIngestJob, postIngestExport, postIngestRun } from "../api";
import { LanguageContext } from "../i18n";
import { applyShellState, resetShellBridgeForTests, type ShellState } from "../shellBridge";
import { resetStoreForTests } from "../store";
import type { DiagnosticsSnapshot } from "../types";
import { INGEST_LOG_NAME, IngestPage, logTailHref, probeRecording, showsEngineLog } from "./IngestPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    fetchBoard: vi.fn().mockResolvedValue({}), fetchHealth: vi.fn(), fetchDiagnostics: vi.fn(), fetchFailures: vi.fn(),
    postIngestExport: vi.fn(), postIngestRun: vi.fn(), fetchIngestJob: vi.fn(),
  };
});

const renderEn = (node: React.ReactNode) => render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);

const shell: ShellState = {
  recording: { available: true, on: true, mode: "screen", engine_running: false, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen", self_heal_note: "", log_tail: "" },
  captions: { available: true, on: false, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false, source: "both", translate: false, translate_direction: "auto", apple_locale: "zh", ark_model: "", font_size: 24, opacity: 0.7 },
  permissions: { screen: "granted", microphone: "unknown", notifications: "granted", vault: "unknown" },
  launch_at_login: false, hotkey: "⌃⌥Space",
};

const diagnostics: DiagnosticsSnapshot = {
  doctor: { ok: true, fast: true, rc: 0, home: "/h", ran_at: "x", checks: [] },
  health: { verdict: "ok", heartbeat: null, dashboard: null, loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "x" },
  deploy_state: null, install_report: null, registry_backend: "sqlite", radar_sources: null, logs: [], cron_probe: null,
  activity: { screenpipe_db: { path: "/d", mtime: 1788350100 }, actd_log: { path: "/a", mtime: 1788350000 }, unprocessed: { path: "/u", mtime: null, readable: true } },
} as unknown as DiagnosticsSnapshot;

/** 装一个记录请求的假壳：`reply` 决定每个 method 的回执（抛 = 壳 reject 的原文） */
function installShell(state: ShellState, reply?: (method: string) => ShellState) {
  const calls: string[] = [];
  const postMessage = vi.fn(async (body: unknown) => {
    const method = (body as { method: string }).method;
    calls.push(method);
    return reply ? reply(method) : state;
  });
  (window as Window & { webkit?: unknown }).webkit = { messageHandlers: { zaiShell: { postMessage } } };
  applyShellState(state);
  return { calls, postMessage };
}

function withRecording(over: Partial<ShellState["recording"]>): ShellState {
  return { ...shell, recording: { ...shell.recording, ...over } };
}

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
  for (const fn of [fetchDiagnostics, fetchFailures, postIngestExport, postIngestRun, fetchIngestJob]) vi.mocked(fn).mockReset();
  vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics);
  vi.mocked(fetchFailures).mockResolvedValue({ failures: {
    engine_npm_download: { zh: "首次下载引擎中", en: "Downloading engine (first run)", action_id: "engine_npm_download" },
    engine_crashed: { zh: "引擎崩了", en: "Engine crashed", action_id: "engine_crashed" },
    engine_ffmpeg_missing: { zh: "缺 ffmpeg", en: "ffmpeg missing", action_id: "install_ffmpeg" },
  } });
  window.history.replaceState(null, "", "/?page=ingest");
  delete (window as Window & { webkit?: unknown }).webkit;
});

afterEach(() => {
  cleanup();
});

describe("EngineDiagnosisRow（原生 Pages.swift:881-909）", () => {
  it("npm first-run download renders a spinner in the calm (non-warning) row; a crash does not", async () => {
    installShell(withRecording({ diagnosis: "engine_npm_download" }));
    renderEn(<IngestPage />);
    await screen.findByText("Downloading engine (first run)");
    const row = document.querySelector("[data-failure=engine_npm_download]") as HTMLElement;
    expect(row.classList.contains("is-warning")).toBe(false);
    expect(row.querySelector(".shell-spinner")).toBeTruthy();
    expect(row.querySelector(".shell-spinner")?.getAttribute("aria-hidden")).toBe("true");
    expect(row.querySelector(".settings-warning")).toBeNull();
    cleanup();
    resetStoreForTests();
    installShell(withRecording({ diagnosis: "engine_crashed" }));
    renderEn(<IngestPage />);
    await screen.findByText("Engine crashed");
    const crashed = document.querySelector("[data-failure=engine_crashed]") as HTMLElement;
    expect(crashed.classList.contains("is-warning")).toBe(true);
    expect(crashed.querySelector(".shell-spinner")).toBeNull();
  });

  it("a non-empty log_tail is printed verbatim as a monospaced <pre> under the row; empty tail renders nothing", async () => {
    installShell(withRecording({ diagnosis: "engine_crashed", log_tail: "thread 'main' panicked\nno monitors\n  at core.rs:12" }));
    renderEn(<IngestPage />);
    await screen.findByText("Engine crashed");
    const tail = document.querySelector("[data-failure=engine_crashed] pre.diag-log.diag-log-tail") as HTMLElement;
    expect(tail).toBeTruthy();
    expect(tail.textContent).toBe("thread 'main' panicked\nno monitors\n  at core.rs:12");   // 原文，不美化
    expect(tail.classList.contains("engine-log-tail")).toBe(true);
    cleanup();
    resetStoreForTests();
    installShell(withRecording({ diagnosis: "engine_crashed", log_tail: "" }));
    renderEn(<IngestPage />);
    await screen.findByText("Engine crashed");
    expect(document.querySelector("[data-failure=engine_crashed] pre")).toBeNull();
    expect(screen.getByRole("link", { name: "View engine log" }).getAttribute("href")).toContain("log=engine.log");
  });

  it("engine_ffmpeg_missing keeps Install ffmpeg + Installed — restart engine and now also links the engine log + shows its tail", async () => {
    installShell(withRecording({ diagnosis: "engine_ffmpeg_missing", log_tail: "ffmpeg: command not found" }));
    renderEn(<IngestPage />);
    await screen.findByText("ffmpeg missing");
    expect(screen.getByRole("link", { name: "Install ffmpeg" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Installed — restart engine" })).toBeTruthy();
    const link = screen.getByRole("link", { name: "View engine log" });
    expect(link.getAttribute("href")).toContain("page=settings");
    expect(link.getAttribute("href")).toContain("anchor=deps");
    expect(link.getAttribute("href")).toContain("log=engine.log");
    expect(document.querySelector("[data-failure=engine_ffmpeg_missing] pre.diag-log-tail")?.textContent).toBe("ffmpeg: command not found");
    // 词表：崩了 / 死了 / ffmpeg 缺失出链接；npm 下载与 node 缺失不出
    expect(["engine_crashed", "engine_dead", "engine_ffmpeg_missing"].every(showsEngineLog)).toBe(true);
    expect(showsEngineLog("engine_npm_download")).toBe(false);
    expect(showsEngineLog("node_missing")).toBe(false);
  });

  it("self_heal_note renders as a green ✓ status line before the refusal note", async () => {
    installShell(withRecording({ engine_running: true, self_heal_note: "屏幕权限已生效，录制引擎已自动重启", note: "拒绝了这次切换" }));
    renderEn(<IngestPage />);
    const heal = await screen.findByRole("status");
    expect(heal.classList.contains("settings-helper")).toBe(true);
    expect(heal.classList.contains("is-ok")).toBe(true);
    expect(heal.textContent).toBe("✓ 屏幕权限已生效，录制引擎已自动重启");
    const note = screen.getByText("拒绝了这次切换");
    expect(heal.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();   // ✓ 句在 note 之前
    cleanup();
    resetStoreForTests();
    installShell(withRecording({ engine_running: true, self_heal_note: "" }));
    renderEn(<IngestPage />);
    await screen.findByRole("button", { name: "Restart engine" });
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("刷新 / 进场 = 原生 rec.refreshEngineState()（Pages.swift:999-1002, 1147-1150）", () => {
  it("mount calls refreshRecording once; the engine row's Refresh calls it again; the activity block's Refresh does not probe the engine", async () => {
    const { calls } = installShell(withRecording({ engine_running: true }));
    renderEn(<IngestPage />);
    await screen.findByRole("button", { name: "Restart engine" });
    await waitFor(() => expect(calls).toEqual(["refreshRecording"]));
    expect(fetchDiagnostics).toHaveBeenCalledTimes(1);   // 进场的首拉（缺快照才拉）
    const [engineRefresh, activityRefresh] = screen.getAllByRole("button", { name: "Refresh" });
    fireEvent.click(engineRefresh);
    await waitFor(() => expect(calls).toEqual(["refreshRecording", "refreshRecording"]));
    expect(fetchDiagnostics).toHaveBeenLastCalledWith(true, "en");
    fireEvent.click(activityRefresh);
    await waitFor(() => expect(fetchDiagnostics).toHaveBeenCalledTimes(3));
    expect(calls).toEqual(["refreshRecording", "refreshRecording"]);   // 最近活动的刷新只 refreshLabels()
    expect(calls).not.toContain("getState");
  });

  it("an old shell that rejects refreshRecording with UNKNOWN_METHOD falls back to a pure getState; other errors do not", async () => {
    const { calls } = installShell(withRecording({ engine_running: true }), (method) => {
      if (method === "refreshRecording") throw new Error("UNKNOWN_METHOD: refreshRecording");
      return withRecording({ engine_running: true });
    });
    renderEn(<IngestPage />);
    await screen.findByRole("button", { name: "Restart engine" });
    await waitFor(() => expect(calls).toEqual(["refreshRecording", "getState"]));
    // 纯函数：真错误不退化、不抛给页面
    cleanup();
    const strict = installShell(shell, (method) => {
      if (method === "refreshRecording") throw new Error("INVALID_ARGS: boom");
      return shell;
    });
    await expect(probeRecording()).resolves.toBeUndefined();
    expect(strict.calls).toEqual(["refreshRecording"]);
    // 没有壳：NO_BRIDGE 也吞掉
    delete (window as Window & { webkit?: unknown }).webkit;
    await expect(probeRecording()).resolves.toBeUndefined();
  });
});

describe("手动触发跑完 → 最近活动立刻刷新（Pages.swift:778/810 refreshLabels）", () => {
  it("done / skipped / failed all re-fetch diagnostics (refresh=true) and re-read the shell snapshot when present", async () => {
    const { calls } = installShell(withRecording({ engine_running: true }));
    vi.mocked(postIngestExport).mockResolvedValue({ ok: true, job: "j1", state: "running", script: "ingest/screenpipe-export.sh" });
    vi.mocked(postIngestRun).mockResolvedValue({ ok: true, job: "j2", state: "running", script: "ingest/process-screenpipe.sh" });
    vi.mocked(fetchIngestJob).mockImplementation(async (id: string) => id === "j1"
      ? { id, script: "ingest/screenpipe-export.sh", state: "done", started_at: "x", ok: true, rc: 0, skipped: false, tail: "", seconds: 0.3 }
      : { id, script: "ingest/process-screenpipe.sh", state: "done", started_at: "x", ok: false, rc: 3, skipped: true, tail: "lock held", seconds: 0.1 });
    renderEn(<IngestPage />);
    await screen.findByRole("button", { name: "Restart engine" });
    await waitFor(() => expect(calls).toEqual(["refreshRecording"]));
    expect(fetchDiagnostics).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Export Now" }));
    await screen.findByText("Done ✓");
    await waitFor(() => expect(fetchDiagnostics).toHaveBeenCalledTimes(2));
    expect(fetchDiagnostics).toHaveBeenLastCalledWith(true, "en");
    await waitFor(() => expect(calls).toEqual(["refreshRecording", "getState"]));   // 纯读，不探引擎
    fireEvent.click(screen.getByRole("button", { name: "Ingest Now" }));
    await screen.findByText("Already running — skipped");
    await waitFor(() => expect(fetchDiagnostics).toHaveBeenCalledTimes(3));
    // 失败也刷（原生完成回调不分 rc）
    vi.mocked(postIngestRun).mockResolvedValue({ ok: true, job: "j3", state: "running", script: "ingest/process-screenpipe.sh" });
    vi.mocked(fetchIngestJob).mockResolvedValue({ id: "j3", script: "ingest/process-screenpipe.sh", state: "done", started_at: "x", ok: false, rc: 1, skipped: false, tail: "claude: boom", seconds: 2 });
    fireEvent.click(screen.getByRole("button", { name: "Ingest Now" }));
    await screen.findByText("Failed (exit 1)");
    await waitFor(() => expect(fetchDiagnostics).toHaveBeenCalledTimes(4));
  });

  it("in the browser (no shell) the refresh still happens and nothing is posted", async () => {
    vi.mocked(postIngestRun).mockResolvedValue({ ok: true, job: "j2", state: "running", script: "ingest/process-screenpipe.sh" });
    vi.mocked(fetchIngestJob).mockResolvedValue({ id: "j2", script: "ingest/process-screenpipe.sh", state: "done", started_at: "x", ok: true, rc: 0, skipped: false, tail: "", seconds: 1 });
    renderEn(<IngestPage />);
    await screen.findByText(/only controllable inside the board app/);
    fireEvent.click(screen.getByRole("button", { name: "Ingest Now" }));
    await screen.findByText("Done ✓");
    await waitFor(() => expect(fetchDiagnostics).toHaveBeenCalledTimes(2));
  });
});

describe("失败后的「查看日志」深链（Pages.swift:814-822 revealIngestLog；§68.4 追记 (4) 的 web 半边）", () => {
  it("a failed ingest links ?page=settings&anchor=deps&log=screenpipe-auto.log; a failed export links the log list only", async () => {
    vi.mocked(postIngestExport).mockResolvedValue({ ok: true, job: "j1", state: "running", script: "ingest/screenpipe-export.sh" });
    vi.mocked(postIngestRun).mockResolvedValue({ ok: true, job: "j2", state: "running", script: "ingest/process-screenpipe.sh" });
    vi.mocked(fetchIngestJob).mockImplementation(async (id: string) => ({
      id, script: id === "j1" ? "ingest/screenpipe-export.sh" : "ingest/process-screenpipe.sh", state: "done", started_at: "x", ok: false, rc: 1, skipped: false, tail: `${id} failed`, seconds: 0.2,
    }));
    renderEn(<IngestPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Ingest Now" }));
    await screen.findByText("j2 failed");
    const ingestRow = screen.getByText("j2 failed").closest(".ingest-trigger") as HTMLElement;
    const ingestLink = ingestRow.querySelector("a") as HTMLAnchorElement;
    const ingestUrl = new URL(ingestLink.href);
    expect(ingestUrl.searchParams.get("page")).toBe("settings");
    expect(ingestUrl.searchParams.get("anchor")).toBe("deps");
    expect(ingestUrl.searchParams.get("log")).toBe(INGEST_LOG_NAME);
    expect(INGEST_LOG_NAME).toBe("screenpipe-auto.log");   // server/diagnostics.INGEST_LOG_NAME 逐字
    fireEvent.click(screen.getByRole("button", { name: "Export Now" }));
    await screen.findByText("j1 failed");
    const exportRow = screen.getByText("j1 failed").closest(".ingest-trigger") as HTMLElement;
    const exportUrl = new URL((exportRow.querySelector("a") as HTMLAnchorElement).href);
    expect(exportUrl.searchParams.get("anchor")).toBe("deps");
    expect(exportUrl.searchParams.get("log")).toBeNull();   // 导出脚本没有日志文件——不假装有
    // helper：名字进 ?log=，其余 query 原样保留
    const built = new URL(logTailHref("http://x/?page=ingest&theme=dark", "engine.log"));
    expect(built.searchParams.get("page")).toBe("settings");
    expect(built.searchParams.get("anchor")).toBe("deps");
    expect(built.searchParams.get("log")).toBe("engine.log");
    expect(built.searchParams.get("theme")).toBe("dark");
  });
});
