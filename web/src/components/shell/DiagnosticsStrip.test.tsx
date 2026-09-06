// 板级诊断条（§48 / §54.4；原生 Diagnostics.swift DiagnosticsRules 的 web 判例）：
// 出卡规则（intent = enabled；skip_reason 分类；录制链按引擎 / TCC 细分）、dismiss 的 7 天窗口与「修好过再坏」重现、
// vault_empty 的预热防抖、渲染面（只在看板页；每卡一颗动作 + ×）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchRadarAgents, postRadarReinstall } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, resetStoreForTests } from "../../store";
import type { Board, RadarSourceHealth } from "../../types";
import { buildDiagnosticCards, DiagnosticsStrip, gmailCardKind, isDebounced, isDismissed, schedulerMissing, type DiagnosticCard } from "./DiagnosticsStrip";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchRadarAgents: vi.fn(), postRadarReinstall: vi.fn() };
});

/** launchd 里两个 agent 都装着（默认：不出 agent_missing 卡） */
const loadedAgents = { gmail: { label: "com.zelin.aiassistant.gmailradar", interval_s: 300, loaded: true, plist_installed: true },
  slack: { label: "com.zelin.aiassistant.slackradar", interval_s: 180, loaded: true, plist_installed: true } };

const en = (_zh: string, english: string) => english;
const on = (skip_reason: string | null, extra: Partial<RadarSourceHealth> = {}): RadarSourceHealth => ({ enabled: true, last_ok: null, skip_reason, stale: false, ...extra });
const rec = (over: Partial<{ mode: string; engine_running: boolean; tcc_lost: boolean }> = {}) => ({
  available: true, on: true, mode: "screen", engine_running: true, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen", ...over,
});

describe("buildDiagnosticCards（原生 DiagnosticsRules）", () => {
  it("fresh user: nothing intended → zero cards; disabled sources never alarm", () => {
    // 全新安装：开关默认全开（enabled:true）、雷达如实报 no_credentials / mcp_not_configured，但没人碰过开关也没凭证
    // （§48.4 投影 intent:false）→ 不出常驻卡（原生 gmailCardEligible 的 switchTouched || credentialFileExists）
    expect(buildDiagnosticCards({ gmail: on("no_credentials", { intent: false, secret_present: false }), slack: on("mcp_not_configured", { intent: false, secret_present: false }), obsidian: { enabled: false, skip_reason: "vault_empty" } }, null, en)).toEqual([]);
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

  it("agent_missing（§48.7）：源开着且 launchd 里没它 → 重装后台调度；失败回执 → 上次重装失败：+ 再试一次；同源凭证卡让位；状态未知不判", () => {
    const notLoaded = { label: "com.zelin.aiassistant.gmailradar", interval_s: 300, loaded: false, plist_installed: false };
    expect(schedulerMissing(on("no_credentials"), notLoaded, false)).toBe(true);
    expect(schedulerMissing(on(null), { ...notLoaded, loaded: true, plist_installed: true }, false)).toBe(false);
    expect(schedulerMissing(on(null), { ...notLoaded, loaded: true, plist_installed: true }, true)).toBe(true);   // 重装写成 plist 但 load 失败 → 卡留着
    expect(schedulerMissing(on(null), { ...notLoaded, loaded: null }, false)).toBe(false);                        // 非 darwin / 问不到
    expect(schedulerMissing({ enabled: false }, notLoaded, false)).toBe(false);
    expect(schedulerMissing(on(null), undefined, false)).toBe(false);
    const cards = buildDiagnosticCards({ gmail: on("no_credentials"), slack: on("connect_failed") }, null, en,
      { radars: { gmail: notLoaded, slack: { ...notLoaded, label: "com.zelin.aiassistant.slackradar", loaded: true, plist_installed: true } }, failures: {} });
    expect(cards.map((c) => [c.signature, c.actionLabel])).toEqual([["gmail:agent_missing", "Reinstall the scheduler"], ["slack:connect_failed", "Check Slack settings"]]);
    expect(cards[0].title).toBe("The Gmail radar is on but its scheduler isn't installed");
    expect(cards[0].action).toEqual({ kind: "reinstall_agent", source: "gmail" });
    const failed = buildDiagnosticCards({ slack: on(null) }, null, en, { radars: { slack: { ...notLoaded, loaded: true, plist_installed: true } }, failures: { slack: "launchctl load failed (exit 5)" } });
    expect(failed).toHaveLength(1);
    expect(failed[0].detailPrefix).toBe("The last reinstall failed: ");
    expect(failed[0].detail).toBe("The last reinstall failed: launchctl load failed (exit 5)");
    expect(failed[0].actionLabel).toBe("Try again");
    // agents 缺席（旧调用方 / 还没问到）= 老规则原样
    expect(buildDiagnosticCards({ gmail: on("no_credentials") }, null, en)[0].signature).toBe("gmail:no_credentials");
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
    vi.mocked(fetchRadarAgents).mockResolvedValue({ radars: loadedAgents });
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

  it("scheduler missing → 重装后台调度 posts the reinstall; a rejected reinstall stays on the card as 上次重装失败：+ 再试一次", async () => {
    vi.mocked(fetchRadarAgents).mockResolvedValue({ radars: { ...loadedAgents, slack: { ...loadedAgents.slack, loaded: false, plist_installed: false } } });
    vi.mocked(postRadarReinstall).mockRejectedValueOnce(new Error("install.sh --reinstall-agent exited 3"));
    await boardWith({ slack: on("connect_failed") });
    window.history.replaceState(null, "", "/");
    render(<LanguageContext.Provider value="en"><DiagnosticsStrip /></LanguageContext.Provider>);
    // launchd 还没回答前：老规则的凭证卡；回答落地后 agent_missing 顶替它（同源一卡）
    const reinstallButton = await screen.findByRole("button", { name: "Reinstall the scheduler" });
    expect(screen.queryByRole("link", { name: "Check Slack settings" })).toBeNull();
    fireEvent.click(reinstallButton);
    expect(postRadarReinstall).toHaveBeenCalledWith("slack");
    await screen.findByText((_content, node) => node?.classList.contains("shell-banner-prefix") === true && node.textContent === "The last reinstall failed: ");
    expect(screen.getByText("install.sh --reinstall-agent exited 3")).toBeTruthy();
    // 再试一次 → server 装好并问过 launchd → 卡撤下
    vi.mocked(postRadarReinstall).mockResolvedValueOnce({ ok: true, source: "slack", label: "com.zelin.aiassistant.slackradar", loaded: true });
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Try again" })).toBeNull());
    // 调度回来了 → 让位的凭证卡重新出现
    expect(screen.getByRole("link", { name: "Check Slack settings" })).toBeTruthy();
  });
});
