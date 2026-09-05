// doctor 行的 status 词表 = §25 小写 ok|warn|fail（CONTRACT §25 / §68.4 2026-09-05 追记；act/lib/checks/core 的常量，
// server/doctor_run 归一后原样透出）。web 四处消费者——依赖检查区的 doctor 表 / 判决 / 让 AI 修门（DepsSection）、
// 依赖快速行（DepRows.fromDoctor）、向导末步的 cron 行（FinaleStep.cronVerdict）、权限体检的 TCC 行徽记（PermissionsPage）——
// 都只认小写。夹具照抄真 `python3 -m act.doctor --json --fast` 的输出形（曾经全套测试都用大写夹具，真数据上每行都算没过）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchDiagnostics, fetchFailures, fetchPermissions, fetchSecrets, fetchSetup } from "./api";
import { buildDepRows } from "./components/settings/DepRows";
import { DepsSection, doctorFindings, doctorSummary, fullReportText, hasDoctorFail } from "./components/settings/DepsSection";
import { cronVerdict } from "./components/setup/FinaleStep";
import { LanguageContext } from "./i18n";
import { PermissionsPage } from "./pages/PermissionsPage";
import { resetStoreForTests, setLanguage } from "./store";
import type { DiagnosticsSnapshot, DoctorReport, PermissionsSnapshot } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    fetchBoard: vi.fn().mockResolvedValue({}), fetchHealth: vi.fn(), fetchDiagnostics: vi.fn(), fetchDoctor: vi.fn(), fetchSecrets: vi.fn(),
    fetchFailures: vi.fn(), fetchPermissions: vi.fn(), fetchSetup: vi.fn(), fetchSettingsCatalog: vi.fn().mockResolvedValue({ sections: [] }),
  };
});

const en = (_zh: string, english: string) => english;

// 真 doctor --json --fast 的行（2026-09-05 临时 home 一跑；detail 截短）：小写 status、空串 failure_id / action_id、row_class 多余键
const REAL: DoctorReport = { ok: true, fast: true, rc: 4, home: "/tmp/h", ran_at: "2026-09-05T10:00:00Z", checks: [
  { name: "claude CLI", status: "ok", detail: "/Users/demo/.local/bin/claude (2.1.261 (Claude Code))", fix: "", failure_id: "", action_id: "", row_class: "" },
  { name: "node/npx", status: "ok", detail: "/opt/homebrew/bin/npx", fix: "", failure_id: "", action_id: "", row_class: "" },
  { name: "gh CLI", status: "ok", detail: "/opt/homebrew/bin/gh (authenticated)", fix: "", failure_id: "", action_id: "", row_class: "" },
  { name: "cron disk access", status: "warn", detail: "no probe yet - the cron chain has not run since this version was installed", fix: "rerun bash install.sh (updates the cron line), then wait ~30 min", failure_id: "", action_id: "", row_class: "" },
  { name: "dashboard", status: "fail", detail: "state/dashboard.json missing - the app shows 'missing' forever", fix: "start actd (bash install.sh), or seed once: python3 -m act.lib.dashboard", failure_id: "", action_id: "", row_class: "" },
  { name: "launchd claude", status: "fail", detail: "claude in launchd cannot read the vault (EPERM)", fix: "grant Full Disk Access", failure_id: "claude_blind", action_id: "open_fda", row_class: "" },
] };

function diagnostics(): DiagnosticsSnapshot {
  return {
    doctor: REAL,
    health: { verdict: "ok", heartbeat: null, dashboard: null, loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "x" },
    deploy_state: null, install_report: null, registry_backend: "sqlite", radar_sources: null, logs: [], cron_probe: null, activity: null,
  } as DiagnosticsSnapshot;
}

function permissions(): PermissionsSnapshot {
  return {
    home: "/Volumes/Storage/repo", on_external_volume: true,
    fda: { needed: true, pane: "x", executables: [] },
    panes: { full_disk: "x", screen: "y", microphone: "z", notifications: "n" },
    doctor: [REAL.checks[3], REAL.checks[5]], doctor_ran_at: "2026-09-05T10:00:00Z", doctor_ok: true,
    vault: { status: "unknown", root: "/Users/demo/Documents/Obsidian Vault" },
  };
}

beforeEach(() => {
  resetStoreForTests();
  setLanguage("en");
  vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics());
  vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [] });
  vi.mocked(fetchFailures).mockResolvedValue({ failures: {} });
  vi.mocked(fetchPermissions).mockResolvedValue(permissions());
  vi.mocked(fetchSetup).mockResolvedValue({ needed: false, done: true, config_exists: true, config_example_exists: true, secrets: {}, home: "/h", protected_location: false });
  window.history.replaceState(null, "", "/?page=settings&anchor=deps");
});

afterEach(cleanup);

describe("DepsSection helpers on the real (lowercase) wire shape", () => {
  it("findings drop the ok rows, summary counts 2 fail / 1 warn, the fail gate opens", () => {
    expect(doctorFindings(REAL).map((r) => r.name)).toEqual(["cron disk access", "dashboard", "launchd claude"]);
    expect(doctorSummary(REAL, en)).toEqual({ verdict: "2 check(s) failed — each has its own button", counts: "(3 ok / 1 warn)" });
    expect(hasDoctorFail(REAL)).toBe(true);
    expect(hasDoctorFail({ ...REAL, checks: REAL.checks.slice(0, 4) })).toBe(false);   // 只有 warn 不算红
    // 完整报告：ok 行不带 fix 缩进行，非 ok 行带
    const text = fullReportText(REAL);
    expect(text).toContain("[ok] claude CLI: /Users/demo/.local/bin/claude (2.1.261 (Claude Code))");
    expect(text).toContain("[warn] cron disk access: no probe yet - the cron chain has not run since this version was installed\n    fix: rerun bash install.sh");
  });

  it("the table lists only the non-ok rows, fail = chip-danger, warn = chip-warning, and 让 AI 修 appears", async () => {
    render(<LanguageContext.Provider value="en"><DepsSection /></LanguageContext.Provider>);
    await screen.findByText("2 check(s) failed — each has its own button");
    const rows = Array.from(document.querySelectorAll(".diag-table tbody tr"));
    expect(rows.map((r) => r.getAttribute("data-status"))).toEqual(["warn", "fail", "fail"]);
    expect(rows[0].querySelector(".chip")?.className).toBe("chip chip-warning");
    expect(rows[1].querySelector(".chip")?.className).toBe("chip chip-danger");
    expect(document.querySelector(".diag-table tr[data-status=ok]")).toBeNull();
    expect(document.querySelector(".diag-verdict")?.className).toContain("is-warning");
    expect(screen.getByRole("button", { name: "Fix with AI" })).toBeTruthy();
  });
});

describe("DepRows.fromDoctor / FinaleStep.cronVerdict / PermissionsPage chips", () => {
  it("ok doctor rows render as ok dep rows (they used to all read as failed)", () => {
    const rows = buildDepRows(REAL, null, { secrets: [] }, null, null, "en", en);
    const byId = Object.fromEntries(rows.map((r) => [r.id, r.ok]));
    expect(byId.npx).toBe(true);
    expect(byId.claude).toBe(true);
    expect(byId.gh).toBe(true);
  });

  it("cronVerdict reads the lowercase ok", () => {
    expect(cronVerdict([{ name: "cron disk access", status: "ok", detail: "cron read ok", fix: "" }])).toBe("ok");
    expect(cronVerdict([{ name: "cron disk access", status: "fail", detail: "", fix: "", failure_id: "cron_fda_blocked" }])).toBe("blocked");
  });

  it("PermissionsPage doctor rows: fail = chip-danger, warn = chip-warning", async () => {
    window.history.replaceState(null, "", "/?page=permissions");
    render(<LanguageContext.Provider value="en"><PermissionsPage /></LanguageContext.Provider>);
    await screen.findByText("launchd claude");
    const chips = Array.from(document.querySelectorAll(".settings-list-row[data-status] .chip")).map((c) => c.className);
    expect(chips).toEqual(["chip chip-warning", "chip chip-danger"]);
  });
});
