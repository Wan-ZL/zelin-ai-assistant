// 板级诊断条的 §48.4 意愿信号 + 首见台账修剪（原生 DiagnosticsRules.gmailCardEligible / Diagnostics.swift:161-168,268-291,397-403）：
// setup 类卡（Gmail no_credentials / no_address、Slack mcp_not_configured）要投影的 `intent`，Slack token 类卡要 `secret_present`，
// 键缺（旧 actd payload）= 老判据；`diagnosticsFirstSeen` 只留还活着的签名——消失过的卡再出现要重新等满 35 min 预热。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchRadarAgents } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, resetStoreForTests } from "../../store";
import type { Board, RadarSourceHealth } from "../../types";
import { buildDiagnosticCards, DiagnosticsStrip, FIRST_SEEN_KEY, gmailCardEligible, pruneFirstSeen, slackCardEligible } from "./DiagnosticsStrip";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchRadarAgents: vi.fn(), postRadarReinstall: vi.fn() };
});

const en = (_zh: string, english: string) => english;
const on = (skip_reason: string | null, extra: Partial<RadarSourceHealth> = {}): RadarSourceHealth => ({ enabled: true, last_ok: null, skip_reason, stale: false, ...extra });
const signatures = (sources: Record<string, RadarSourceHealth>) => buildDiagnosticCards(sources, null, en).map((c) => c.signature);

describe("gmailCardEligible（原生 DiagnosticsRules.gmailCardEligible）", () => {
  it("setup reasons need the intent signal; connection / command reasons do not; `disabled` never alarms", () => {
    for (const reason of ["no_credentials", "no_address"]) {
      expect(gmailCardEligible(reason, on(reason, { intent: false }))).toBe(false);   // 全新安装：开关默认开 ≠ intent
      expect(gmailCardEligible(reason, on(reason, { intent: true }))).toBe(true);     // 碰过开关 / 凭证文件在
      expect(gmailCardEligible(reason, on(reason))).toBe(true);                       // 旧 payload 缺键 → 老判据
    }
    expect(gmailCardEligible("auth_failed", on("auth_failed", { intent: false, secret_present: false }))).toBe(true);
    expect(gmailCardEligible("fetch_command_failed", on("fetch_command_failed", { intent: false }))).toBe(true);
    expect(gmailCardEligible("disabled", on("disabled", { intent: true, secret_present: true }))).toBe(false);
  });
});

describe("slackCardEligible（原生 Diagnostics.swift:268-291）", () => {
  it("token rejected needs a stored token; MCP fallback missing needs intent; keys absent = old rule", () => {
    for (const reason of ["connect_failed", "auth_failed"]) {
      expect(slackCardEligible(reason, on(reason, { secret_present: false, intent: true }))).toBe(false); // 没 token 谈不上「被拒绝」
      expect(slackCardEligible(reason, on(reason, { secret_present: true }))).toBe(true);
      expect(slackCardEligible(reason, on(reason))).toBe(true);
    }
    expect(slackCardEligible("mcp_not_configured", on("mcp_not_configured", { intent: false }))).toBe(false);
    expect(slackCardEligible("mcp_not_configured", on("mcp_not_configured", { intent: true, secret_present: false }))).toBe(true);
    expect(slackCardEligible("mcp_not_configured", on("mcp_not_configured"))).toBe(true);
  });
});

describe("buildDiagnosticCards × §48.4 signals", () => {
  it("fresh install (enabled:true, no intent) → no gmail / slack setup cards; touched switch or half-configured credential → cards", () => {
    expect(signatures({ gmail: on("no_credentials", { intent: false, secret_present: false }), slack: on("mcp_not_configured", { intent: false, secret_present: false }) })).toEqual([]);
    expect(signatures({ gmail: on("no_address", { intent: true, secret_present: true }), slack: on("mcp_not_configured", { intent: true, secret_present: false }) }))
      .toEqual(["gmail:no_address", "slack:mcp_not_configured"]);
  });

  it("slack connect_failed without a stored token stays silent; with one it alarms", () => {
    expect(signatures({ slack: on("connect_failed", { intent: true, secret_present: false }) })).toEqual([]);
    expect(signatures({ slack: on("connect_failed", { intent: true, secret_present: true }) })).toEqual(["slack:connect_failed"]);
  });

  it("connection-class gmail reasons ignore intent (a credential is erroring = the user clearly configured it)", () => {
    expect(signatures({ gmail: on("auth_failed", { intent: false, secret_present: true }) })).toEqual(["gmail:auth_failed"]);
    expect(signatures({ gmail: on("disabled", { intent: true }) })).toEqual([]);
  });

  it("old payload without the keys keeps today's behaviour", () => {
    expect(signatures({ gmail: on("no_credentials"), slack: on("mcp_not_configured") })).toEqual(["gmail:no_credentials", "slack:mcp_not_configured"]);
  });

  it("agent_missing (§48.7) still wins regardless of intent — the scheduler card is about the switch, not the credential", () => {
    const notLoaded = { label: "com.zelin.aiassistant.gmailradar", interval_s: 300, loaded: false, plist_installed: false };
    const cards = buildDiagnosticCards({ gmail: on("no_credentials", { intent: false }) }, null, en, { radars: { gmail: notLoaded }, failures: {} });
    expect(cards.map((c) => c.signature)).toEqual(["gmail:agent_missing"]);
  });
});

describe("pruneFirstSeen（原生 Diagnostics.swift pruneFirstSeen）", () => {
  it("forgets signatures that are no longer live, keeps the live ones, returns the same object when nothing changed", () => {
    const seen = { "screenpipe:vault_empty.other": 1, "screenpipe:vault_empty.engine": 2 };
    expect(pruneFirstSeen(seen, new Set(["screenpipe:vault_empty.engine", "gmail:auth_failed"]))).toEqual({ "screenpipe:vault_empty.engine": 2 });
    expect(pruneFirstSeen(seen, new Set())).toEqual({});
    expect(pruneFirstSeen(seen, new Set(Object.keys(seen)))).toBe(seen);
    expect(pruneFirstSeen({}, new Set(["x"]))).toEqual({});
  });
});

describe("<DiagnosticsStrip /> first-seen ledger", () => {
  beforeEach(() => {
    resetStoreForTests();
    window.localStorage.clear();
    delete (window as Window & { webkit?: unknown }).webkit;
    vi.mocked(fetchRadarAgents).mockResolvedValue({ radars: {} });
    window.history.replaceState(null, "", "/");
  });
  afterEach(cleanup);

  async function boardWith(sources: Record<string, RadarSourceHealth>) {
    vi.mocked(fetchBoard).mockResolvedValue({ generated_at: "x", counts: {}, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [], archived: [], radar_sources: sources } as unknown as Board);
    await refreshBoard();
  }
  const mount = () => render(<LanguageContext.Provider value="en"><DiagnosticsStrip /></LanguageContext.Provider>);
  const stored = () => JSON.parse(window.localStorage.getItem(FIRST_SEEN_KEY) ?? "{}") as Record<string, number>;

  it("a vanished vault_empty signature is forgotten, so its next appearance waits the 35 min warm-up again", async () => {
    const sig = "screenpipe:vault_empty.other";
    // 几天前录制开着、vault 空过：首见台账里躺着一个早就过了预热的时间戳
    window.localStorage.setItem(FIRST_SEEN_KEY, JSON.stringify({ [sig]: Date.now() - 3 * 86_400_000, "gmail:auth_failed": 5 }));
    // 录制关了（源关着）→ 这张卡不再活着 → 台账里的它被忘掉（gmail 那条也不活着，一样忘）
    await boardWith({ obsidian: { enabled: false, skip_reason: "vault_empty" } });
    mount();
    expect(screen.queryAllByRole("status")).toHaveLength(0);
    expect(stored()).toEqual({});
    cleanup();
    // 录制重新打开、vault 仍空 → 不能立刻告警：重新记首见、等满预热
    const before = Date.now();
    await boardWith({ obsidian: on("vault_empty") });
    mount();
    expect(screen.queryAllByRole("status")).toHaveLength(0);
    expect(stored()[sig]).toBeGreaterThanOrEqual(before);
    cleanup();
    // 预热过了（把首见拨到 36 分钟前）→ 卡出来，台账原样（签名还活着）
    window.localStorage.setItem(FIRST_SEEN_KEY, JSON.stringify({ [sig]: Date.now() - 36 * 60_000 }));
    mount();
    expect(screen.getByRole("link", { name: "Open Dependencies" })).toBeTruthy();
    expect(Object.keys(stored())).toEqual([sig]);
  });

  it("without the prune the stale timestamp would alarm at once: live signatures are never touched", async () => {
    const sig = "screenpipe:vault_empty.other";
    const first = Date.now() - 40 * 60_000;
    window.localStorage.setItem(FIRST_SEEN_KEY, JSON.stringify({ [sig]: first }));
    await boardWith({ obsidian: on("vault_empty") });
    mount();
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(stored()).toEqual({ [sig]: first });   // 活着的签名一分不动
  });
});
