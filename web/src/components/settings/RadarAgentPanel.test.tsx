// 「后台雷达」行 + 「立即测试一轮」（CONTRACT §48.7；原生 SettingsGmail / SettingsSlack agentRow + healthRow）：
//   1) 状态词三态 + 非 darwin 的「状态未知」，N 从 interval_s 算；2) 「重新安装」→ POST 回执 → 「已重新安装 ✓」+
//   回执的 loaded 覆盖状态；失败 → 错误原文；3) 「立即测试一轮」→ inbox radar_test_round {source}，忙态「测试中…」
//   直到看板回执落地（新 requested_at 且不再 running）；源关着按钮禁用；4) noop / lost 回执各说一句；
//   5) 运行状态行带「最近一轮 <相对时间>」。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard, fetchRadarAgents, postAction, postRadarReinstall } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, resetStoreForTests } from "../../store";
import type { Board, RadarSourceHealth } from "../../types";
import { RadarAgentPanel, agentStatusLabel, testRoundNote } from "./RadarAgentPanel";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchRadarAgents: vi.fn(), postAction: vi.fn(), postRadarReinstall: vi.fn() };
});

const text = (zh: string, en: string) => en;

function board(gmail: Partial<RadarSourceHealth>): Board {
  return {
    generated_at: "2026-09-03T12:00:00Z",
    needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [], archived: [],
    counts: {},
    radar_sources: {
      gmail: { enabled: true, last_ok: null, skip_reason: null, stale: false, last_attempt: null, test_round: null, ...gmail },
    },
  } as unknown as Board;
}

async function seedBoard(gmail: Partial<RadarSourceHealth>) {
  vi.mocked(fetchBoard).mockResolvedValue(board(gmail));
  await refreshBoard();
}

function renderPanel() {
  return render(
    <LanguageContext.Provider value="en">
      <RadarAgentPanel source="gmail" />
    </LanguageContext.Provider>,
  );
}

const agents = (loaded: boolean | null) => ({
  radars: {
    gmail: { label: "com.zelin.aiassistant.gmailradar", interval_s: 300, loaded, plist_installed: loaded === true },
    slack: { label: "com.zelin.aiassistant.slackradar", interval_s: 180, loaded, plist_installed: loaded === true },
  },
});

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchBoard).mockReset();
  vi.mocked(fetchRadarAgents).mockReset();
  vi.mocked(postAction).mockReset();
  vi.mocked(postRadarReinstall).mockReset();
  vi.useRealTimers();
});
afterEach(cleanup);

describe("agentStatusLabel / testRoundNote", () => {
  it("mirrors the native three states, N from the template interval, unknown off-darwin", () => {
    expect(agentStatusLabel(undefined, text)).toBe("checking…");
    expect(agentStatusLabel(null, text)).toBe("unknown");
    expect(agentStatusLabel({ label: "x", interval_s: 300, loaded: null, plist_installed: false }, text)).toBe("unknown");
    expect(agentStatusLabel({ label: "x", interval_s: 300, loaded: false, plist_installed: true }, text)).toBe("not installed");
    expect(agentStatusLabel({ label: "x", interval_s: 180, loaded: true, plist_installed: true }, text)).toBe("installed — runs every 3 minutes");
    expect(agentStatusLabel({ label: "x", interval_s: null, loaded: true, plist_installed: true }, text)).toBe("installed");
    expect(agentStatusLabel({ label: "x", interval_s: 300, loaded: true, plist_installed: true }, (zh) => zh)).toBe("已安装，每 5 分钟自动运行");
  });

  it("speaks only for noop / lost rounds", () => {
    expect(testRoundNote(null, text)).toBeNull();
    expect(testRoundNote({ requested_at: "t", state: "running", note: null }, text)).toBeNull();
    expect(testRoundNote({ requested_at: "t", state: "done", note: null }, text)).toBeNull();
    expect(testRoundNote({ requested_at: "t", state: "noop", note: "disabled" }, text)).toMatch(/switched off/);
    expect(testRoundNote({ requested_at: "t", state: "noop", note: "launch_failed" }, text)).toMatch(/failed to start/);
    expect(testRoundNote({ requested_at: "t", state: "lost", note: null }, text)).toMatch(/No word/);
  });
});

describe("<RadarAgentPanel />", () => {
  it("shows checking… then the launchd answer, and the last-round relative time", async () => {
    let resolve: (v: unknown) => void = () => undefined;
    vi.mocked(fetchRadarAgents).mockReturnValue(new Promise((r) => { resolve = r; }) as never);
    await seedBoard({ skip_reason: "no_credentials", last_attempt: "2026-09-03T11:57:00Z" });
    renderPanel();
    expect(screen.getByText("Background radar")).toBeTruthy();
    expect(screen.getByText("checking…")).toBeTruthy();
    await act(async () => { resolve(agents(true)); });
    await screen.findByText("installed — runs every 5 minutes");
    expect(screen.getByText(/last round/).textContent).toMatch(/^last round /);
  });

  it("Reinstall posts the source, reports Reinstalled ✓ and trusts the receipt's loaded", async () => {
    vi.mocked(fetchRadarAgents).mockResolvedValue(agents(false));
    vi.mocked(postRadarReinstall).mockResolvedValue({ ok: true, source: "gmail", label: "com.zelin.aiassistant.gmailradar", loaded: true });
    await seedBoard({ last_ok: "2026-09-03T11:55:00Z" });
    renderPanel();
    await screen.findByText("not installed");
    fireEvent.click(screen.getByRole("button", { name: "Reinstall" }));
    await screen.findByText("Reinstalled ✓");
    expect(postRadarReinstall).toHaveBeenCalledWith("gmail");
    expect(screen.getByText("installed — runs every 5 minutes")).toBeTruthy();
  });

  it("Reinstall failure shows the server sentence verbatim as an alert", async () => {
    vi.mocked(fetchRadarAgents).mockResolvedValue(agents(false));
    vi.mocked(postRadarReinstall).mockRejectedValue(new ApiError(409, { error: { code: "CONFLICT", message: "the gmail source is switched off - enable it first" } }));
    await seedBoard({});
    renderPanel();
    await screen.findByText("not installed");
    fireEvent.click(screen.getByRole("button", { name: "Reinstall" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("switched off");
    expect(screen.getByText("not installed")).toBeTruthy();
  });

  it("Test one round now writes the inbox action and stays Testing… until the board receipt lands", async () => {
    vi.mocked(fetchRadarAgents).mockResolvedValue(agents(true));
    vi.mocked(postAction).mockResolvedValue({ ok: true });
    await seedBoard({ test_round: { requested_at: "2026-09-03T11:00:00Z", state: "done", note: null } });
    renderPanel();
    await screen.findByText("installed — runs every 5 minutes");
    const button = screen.getByRole("button", { name: "Test one round now" }) as HTMLButtonElement;
    fireEvent.click(button);
    expect(screen.getByRole("button", { name: "Testing…" })).toBeTruthy();
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({ action: "radar_test_round", source: "gmail" }));
    // 看板回流：同一份旧回执 → 仍在测试中；新 requested_at 但 running → 仍在；done → 结束
    await seedBoard({ test_round: { requested_at: "2026-09-03T11:00:00Z", state: "done", note: null } });
    expect(screen.getByRole("button", { name: "Testing…" })).toBeTruthy();
    await seedBoard({ test_round: { requested_at: "2026-09-03T12:00:05Z", state: "running", note: null } });
    expect(screen.getByRole("button", { name: "Testing…" })).toBeTruthy();
    await seedBoard({ test_round: { requested_at: "2026-09-03T12:00:05Z", state: "done", note: null }, last_ok: "2026-09-03T12:00:09Z" });
    await screen.findByRole("button", { name: "Test one round now" });
    expect(screen.getByText(/Working ✓ last success/)).toBeTruthy();
  });

  it("a noop receipt explains itself and the button is disabled while the source is off", async () => {
    vi.mocked(fetchRadarAgents).mockResolvedValue(agents(true));
    await seedBoard({ enabled: false, test_round: { requested_at: "2026-09-03T11:59:00Z", state: "noop", note: "disabled" } });
    renderPanel();
    await screen.findByText("installed — runs every 5 minutes");
    expect((screen.getByRole("button", { name: "Test one round now" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toMatch(/switched off/);
  });

  it("a rejected inbox write ends the busy state with the error", async () => {
    vi.mocked(fetchRadarAgents).mockResolvedValue(agents(true));
    vi.mocked(postAction).mockRejectedValue(new Error("boom"));
    await seedBoard({});
    renderPanel();
    await screen.findByText("installed — runs every 5 minutes");
    fireEvent.click(screen.getByRole("button", { name: "Test one round now" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("boom");
    expect(screen.getByRole("button", { name: "Test one round now" })).toBeTruthy();
  });

  it("Refresh re-asks launchd; an unreachable server reads as unknown", async () => {
    vi.mocked(fetchRadarAgents).mockResolvedValueOnce(agents(true)).mockRejectedValueOnce(new Error("offline"));
    await seedBoard({});
    renderPanel();
    await screen.findByText("installed — runs every 5 minutes");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("unknown");
    expect(fetchRadarAgents).toHaveBeenCalledTimes(2);
  });
});
