// 板级诊断条（§48 / §54.4；原生 Diagnostics.swift DiagnosticsRules 的 web 判例）：
// 出卡规则（intent = enabled；skip_reason 分类；录制链按引擎 / TCC 细分）、dismiss 的 7 天窗口与「修好过再坏」重现、
// vault_empty 的预热防抖、渲染面（只在看板页；每卡一颗动作 + ×）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, resetStoreForTests } from "../../store";
import type { Board, RadarSourceHealth } from "../../types";
import { buildDiagnosticCards, DiagnosticsStrip, gmailCardKind, isDebounced, isDismissed, type DiagnosticCard } from "./DiagnosticsStrip";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn() };
});

const en = (_zh: string, english: string) => english;
const on = (skip_reason: string | null, extra: Partial<RadarSourceHealth> = {}): RadarSourceHealth => ({ enabled: true, last_ok: null, skip_reason, stale: false, ...extra });
const rec = (over: Partial<{ mode: string; engine_running: boolean; tcc_lost: boolean }> = {}) => ({
  available: true, on: true, mode: "screen", engine_running: true, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen", ...over,
});

describe("buildDiagnosticCards（原生 DiagnosticsRules）", () => {
  it("fresh user: nothing intended → zero cards; disabled sources never alarm", () => {
    expect(buildDiagnosticCards({ gmail: { enabled: false, skip_reason: "no_credentials" }, slack: { enabled: false }, obsidian: { enabled: false, skip_reason: "vault_empty" } }, null, en)).toEqual([]);
    expect(buildDiagnosticCards(null, null, en)).toEqual([]);
  });

  it("gmail: reason class picks the sentence, the action always opens Gmail settings", () => {
    expect(gmailCardKind("no_credentials")).toBe("setup");
    expect(gmailCardKind("fetch_command_failed")).toBe("command");
    expect(gmailCardKind("auth_failed")).toBe("connection");
    const [card] = buildDiagnosticCards({ gmail: on("auth_failed") }, null, en);
    expect(card.title).toBe("The Gmail radar can't connect");
    expect(card.actionLabel).toBe("Check Gmail settings");
    expect(card.action).toEqual({ kind: "page", page: "settings", anchor: "gmail" });
  });

  it("slack: invalid token vs missing MCP fallback; other reasons stay silent", () => {
    expect(buildDiagnosticCards({ slack: on("connect_failed") }, null, en)[0].title).toBe("The Slack token is invalid");
    expect(buildDiagnosticCards({ slack: on("mcp_not_configured") }, null, en)[0].actionLabel).toBe("Connect Slack");
    expect(buildDiagnosticCards({ slack: on("no_credentials") }, null, en)).toEqual([]);
  });

  it("screenpipe: vault_empty refines by engine / TCC / other; intent = recording on when the shell is present", () => {
    const empty = { obsidian: on("vault_empty") };
    expect(buildDiagnosticCards(empty, rec({ engine_running: false }), en)[0].actionLabel).toBe("Restart the engine");
    expect(buildDiagnosticCards(empty, rec({ tcc_lost: true }), en)[0].actionLabel).toBe("Grant Screen Recording");
    expect(buildDiagnosticCards(empty, rec(), en)[0].actionLabel).toBe("Open Dependencies");
    expect(buildDiagnosticCards(empty, rec({ mode: "off" }), en)).toEqual([]);          // 录制关着 = 没有 intent
    expect(buildDiagnosticCards({ obsidian: on("vault_missing") }, null, en)[0].actionLabel).toBe("Set the Obsidian folder");
    expect(buildDiagnosticCards({ obsidian: on("no_api_key") }, null, en)[0].action).toEqual({ kind: "page", page: "settings", anchor: "credentials" });
    expect(buildDiagnosticCards({ obsidian: on("disabled") }, null, en)).toEqual([]);
  });
});

describe("dismiss + warm-up", () => {
  const card: DiagnosticCard = { id: "diag.gmail", signature: "gmail:auth_failed", title: "t", detail: "d", actionLabel: "a", action: { kind: "page", page: "deps" }, lastOk: null, lastAttempt: null };
  const now = Date.parse("2026-09-02T12:00:00Z");

  it("dismissed within 7 days stays hidden; a success after the dismissal re-alerts; 7 days re-appear", () => {
    expect(isDismissed(card, {}, now)).toBe(false);
    expect(isDismissed(card, { "gmail:auth_failed": now - 3600_000 }, now)).toBe(true);
    expect(isDismissed({ ...card, lastOk: "2026-09-02T11:30:00Z" }, { "gmail:auth_failed": now - 3600_000 }, now)).toBe(false);
    expect(isDismissed(card, { "gmail:auth_failed": now - 8 * 86_400_000 }, now)).toBe(false);
  });

  it("vault_empty waits one ingest cycle after first sight; everything else surfaces at once", () => {
    const vault = { ...card, signature: "screenpipe:vault_empty.other" };
    const first = isDebounced(vault, {}, now);
    expect(first.debounced).toBe(true);
    expect(first.seen["screenpipe:vault_empty.other"]).toBe(now);
    expect(isDebounced(vault, first.seen, now + 10 * 60_000).debounced).toBe(true);
    expect(isDebounced(vault, first.seen, now + 40 * 60_000).debounced).toBe(false);
    expect(isDebounced(card, {}, now).debounced).toBe(false);
  });
});

describe("<DiagnosticsStrip />", () => {
  beforeEach(() => {
    resetStoreForTests();
    window.localStorage.clear();
    delete (window as Window & { webkit?: unknown }).webkit;
  });
  afterEach(cleanup);

  async function boardWith(sources: Record<string, RadarSourceHealth>) {
    vi.mocked(fetchBoard).mockResolvedValue({ generated_at: "x", counts: {}, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [], archived: [], radar_sources: sources } as unknown as Board);
    await refreshBoard();
  }

  it("renders one card per failing intended path on the board page only, and × dismisses it", async () => {
    await boardWith({ gmail: on("auth_failed", { last_attempt: "2026-09-02T11:50:00Z" }), slack: on("connect_failed") });
    window.history.replaceState(null, "", "/");
    render(<LanguageContext.Provider value="en"><DiagnosticsStrip /></LanguageContext.Provider>);
    expect(screen.getAllByRole("status")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Check Gmail settings" }).getAttribute("href")).toContain("anchor=gmail");
    expect(screen.getByText("last tried")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: /Dismiss/ })[0]);
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(Object.keys(JSON.parse(window.localStorage.getItem("dismissedDiagnostics") ?? "{}"))).toEqual(["gmail:auth_failed"]);
    cleanup();
    window.history.replaceState(null, "", "/?page=settings");
    render(<LanguageContext.Provider value="en"><DiagnosticsStrip /></LanguageContext.Provider>);
    expect(screen.queryAllByRole("status")).toHaveLength(0);
  });
});
