// 依赖检查区 doctor 表的原生 parity（CONTRACT §25 / §68.4 2026-09-05 追记；原生 Pages.swift doctorFindingRow / Doctor.swift AIFix）：
//   · 主句 = §25 FailureCatalog 的人话（当前语言）?? 原句；用了人话时原句 + 修法降成辅助行 + title 气泡；
//   · 表里只有没通过的行，OK 行留在「完整报告」；
//   · 快照 / 完整 doctor 都带当前 UI 语言（?lang=），切语言重拉；
//   · 「让 AI 修」成功留一句状态行（与卡片共用），ai_fix_enabled=false 时整颗不出现。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchDiagnostics, fetchDoctor, fetchFailures, fetchSecrets, postAiFixDoctor } from "../../api";
import { LanguageContext, type Language } from "../../i18n";
import { resetStoreForTests, setLanguage } from "../../store";
import type { DiagnosticsSnapshot, DoctorReport, FailureCatalog } from "../../types";
import { aiFixOpenedText } from "../board/cardChrome";
import { DepsSection, doctorFindings, doctorSentence } from "./DepsSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchBoard: vi.fn().mockResolvedValue({}), fetchHealth: vi.fn(), fetchDiagnostics: vi.fn(), fetchDoctor: vi.fn(), fetchSecrets: vi.fn(),
    fetchFailures: vi.fn(), postAiFixDoctor: vi.fn(),
  };
});

const en = (_zh: string, english: string) => english;
const zh = (chinese: string) => chinese;
const renderIn = (language: Language, node: React.ReactNode) => render(<LanguageContext.Provider value={language}>{node}</LanguageContext.Provider>);

const report: DoctorReport = { ok: true, fast: true, rc: 0, home: "/h", ran_at: "x", checks: [
  { name: "claude CLI", status: "ok", detail: "/u/claude 1.0.99", fix: "" },
  { name: "launchd claude", status: "fail", detail: "claude in launchd cannot read the vault (EPERM)", fix: "grant Full Disk Access to claude", failure_id: "claude_blind" },
  { name: "gh CLI", status: "warn", detail: "missing - optional", fix: "brew install gh" },
  { name: "config", status: "fail", detail: "broken yaml at line 3", fix: "restore", failure_id: "config_invalid" },
] };
const catalog: FailureCatalog = { failures: {
  claude_blind: { zh: "后台的 claude 读不到笔记库——给它「完全磁盘访问」", en: "Background claude cannot read the vault — grant it Full Disk Access", action_id: "open_fda" },
} };

function diagnostics(over: Partial<DiagnosticsSnapshot> = {}): DiagnosticsSnapshot {
  return {
    doctor: report,
    health: { verdict: "ok", heartbeat: null, dashboard: null, loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "x" },
    deploy_state: null, install_report: null, registry_backend: "sqlite", radar_sources: null, logs: [], cron_probe: null, activity: null,
    ...over,
  } as DiagnosticsSnapshot;
}

beforeEach(() => {
  resetStoreForTests();
  setLanguage("en");
  for (const fn of [fetchDiagnostics, fetchDoctor, fetchSecrets, fetchFailures, postAiFixDoctor]) vi.mocked(fn).mockReset();
  vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [] });
  vi.mocked(fetchFailures).mockResolvedValue(catalog);
  vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics());
  window.history.replaceState(null, "", "/?page=settings&anchor=deps");
});

afterEach(() => {
  cleanup();
  setLanguage("en");
});

describe("doctorSentence / doctorFindings（原生 doctorFindingRow 的两条规则）", () => {
  it("catalog sentence only for a non-OK row whose failure_id is in the catalog", () => {
    expect(doctorSentence(report.checks[1], catalog, "en")).toBe("Background claude cannot read the vault — grant it Full Disk Access");
    expect(doctorSentence(report.checks[1], catalog, "zh")).toBe("后台的 claude 读不到笔记库——给它「完全磁盘访问」");
    expect(doctorSentence(report.checks[2], catalog, "en")).toBeNull();          // 没 failure_id → 原句
    expect(doctorSentence(report.checks[3], catalog, "en")).toBeNull();          // 目录里没有这个 id → 原句
    expect(doctorSentence({ ...report.checks[1], status: "ok" }, catalog, "en")).toBeNull();   // OK 行永不套失败句
    expect(doctorSentence(report.checks[1], null, "en")).toBeNull();             // 目录还没回
  });

  it("findings drop OK rows (they stay in the full report)", () => {
    expect(doctorFindings(report).map((r) => r.name)).toEqual(["launchd claude", "gh CLI", "config"]);
  });
});

describe("DoctorTable（DepsSection 的诊断表）", () => {
  it("leads with the plain sentence, demotes the raw detail + fix to a helper line and the tooltip; OK rows live only in the full report", async () => {
    renderIn("en", <DepsSection />);
    await screen.findByText("Background claude cannot read the vault — grant it Full Disk Access");
    const row = document.querySelector("tr[data-failure=claude_blind]") as HTMLElement;
    expect(row.querySelector(".diag-raw-detail")?.textContent).toBe("claude in launchd cannot read the vault (EPERM)");
    expect(row.querySelector("td[title]")?.getAttribute("title")).toBe("claude in launchd cannot read the vault (EPERM)\nfix: grant Full Disk Access to claude");
    expect(row.textContent).toContain("Fix: grant Full Disk Access to claude");
    // 目录里没有的 id / 没 id 的行：原句是主句，没有辅助行、没有气泡
    const config = document.querySelector("tr[data-failure=config_invalid]") as HTMLElement;
    expect(config.querySelector("td:nth-child(3) > div")?.textContent).toBe("broken yaml at line 3");
    expect(config.querySelector(".diag-raw-detail")).toBeNull();
    expect(config.querySelector("td[title]")).toBeNull();
    // OK 行不在表里，只在完整报告文本里
    expect(document.querySelectorAll(".diag-table tbody tr").length).toBe(3);
    expect(document.querySelector(".diag-table tr[data-status=ok]")).toBeNull();
    expect(document.querySelector(".diag-full-report pre")?.textContent).toContain("[ok] claude CLI: /u/claude 1.0.99");
    expect(screen.getByText("2 check(s) failed — each has its own button")).toBeTruthy();
  });

  it("zh renders the catalog's zh sentence", async () => {
    setLanguage("zh");
    renderIn("zh", <DepsSection />);
    await screen.findByText("后台的 claude 读不到笔记库——给它「完全磁盘访问」");
    expect(screen.getByText("修法：grant Full Disk Access to claude")).toBeTruthy();
  });

  it("all-OK report renders no table, just 全部通过 ✓ and the full report", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics({ doctor: { ...report, checks: [report.checks[0]] } }));
    renderIn("en", <DepsSection />);
    await screen.findByText("All checks passed ✓");
    expect(document.querySelector(".diag-table")).toBeNull();
    expect(screen.getByText("Full report")).toBeTruthy();
  });
});

describe("语言透传（原生 runFullOutput 的 AIASSISTANT_UI_LANG + onChange(lang) 重查）", () => {
  it("snapshot and full doctor carry the current UI language; a language switch refetches", async () => {
    const { rerender } = renderIn("en", <DepsSection />);
    await waitFor(() => expect(fetchDiagnostics).toHaveBeenCalledWith(false, "en"));
    vi.mocked(fetchDoctor).mockResolvedValue({ ...report, fast: false });
    fireEvent.click(screen.getByRole("button", { name: "Run diagnostics" }));
    await waitFor(() => expect(fetchDoctor).toHaveBeenCalledWith(false, true, "en"));
    // 切语言：store 与 context 一起换（app.tsx 就是这样挂的）→ 重拉一次快照，完整报告放下
    setLanguage("zh");
    rerender(<LanguageContext.Provider value="zh"><DepsSection /></LanguageContext.Provider>);
    await waitFor(() => expect(fetchDiagnostics).toHaveBeenLastCalledWith(false, "zh"));
    expect(fetchDiagnostics).toHaveBeenCalledTimes(2);
  });

  it("a language switch while the previous-language doctor run is still in flight refetches once it lands (store dedupe must not swallow it)", async () => {
    let settle: (snap: DiagnosticsSnapshot) => void = () => undefined;
    vi.mocked(fetchDiagnostics).mockReset();
    vi.mocked(fetchDiagnostics).mockImplementationOnce(() => new Promise((resolve) => { settle = resolve; }));
    vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics());
    const { rerender } = renderIn("en", <DepsSection />);
    await waitFor(() => expect(fetchDiagnostics).toHaveBeenCalledWith(false, "en"));
    // doctor 还在跑（几秒）就切了语言：同 key 的在途请求本会把这次 refresh 合并掉
    setLanguage("zh");
    rerender(<LanguageContext.Provider value="zh"><DepsSection /></LanguageContext.Provider>);
    expect(fetchDiagnostics).toHaveBeenCalledTimes(1);
    settle(diagnostics());
    await waitFor(() => expect(fetchDiagnostics).toHaveBeenLastCalledWith(false, "zh"));
    expect(fetchDiagnostics).toHaveBeenCalledTimes(2);
    await screen.findByText("2 项未通过——每条都有对应按钮");
  });
});

describe("让 AI 修（原生 AIFix：成功句 + config 关闭即隐藏）", () => {
  it("success leaves the shared repair-session sentence as the status line", async () => {
    vi.mocked(postAiFixDoctor).mockResolvedValue({ ok: true, command_file: "/tmp/x.command" });
    renderIn("en", <DepsSection />);
    const button = await screen.findByRole("button", { name: "Fix with AI" });
    fireEvent.click(button);
    await waitFor(() => expect(postAiFixDoctor).toHaveBeenCalledWith("en"));
    await screen.findByText("Repair session opened in Terminal — just follow the AI");
    expect(screen.getByRole("status").textContent).toBe(aiFixOpenedText(en));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(aiFixOpenedText(zh)).toBe("已在 Terminal 打开修复会话——跟着 AI 走即可");
  });

  it("failure keeps the native prefix + server sentence as an alert", async () => {
    vi.mocked(postAiFixDoctor).mockRejectedValue(new Error("Fix with AI opens Terminal.app — macOS only"));
    renderIn("en", <DepsSection />);
    fireEvent.click(await screen.findByRole("button", { name: "Fix with AI" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toBe("Fix with AI failed to launch: Fix with AI opens Terminal.app — macOS only");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("ai_fix_enabled:false hides the button entirely; absent keeps it", async () => {
    vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics({ ai_fix_enabled: false }));
    renderIn("en", <DepsSection />);
    await screen.findByText("2 check(s) failed — each has its own button");
    expect(screen.queryByRole("button", { name: "Fix with AI" })).toBeNull();
    cleanup();
    vi.mocked(fetchDiagnostics).mockResolvedValue(diagnostics({ ai_fix_enabled: true }));
    resetStoreForTests();
    renderIn("en", <DepsSection />);
    expect(await screen.findByRole("button", { name: "Fix with AI" })).toBeTruthy();
  });
});
