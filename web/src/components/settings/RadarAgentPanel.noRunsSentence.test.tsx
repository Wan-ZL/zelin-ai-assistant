// 「后台雷达」面板的运行状态行在源开着、health 条目全空时说「还没有运行记录。等一轮（≤N 分钟）或点「立即测试一轮」。」
// （CONTRACT §48.7 追记；原生 SettingsGmail / SettingsSlack healthSummary 的 `guard healthHasData`）：N 与上一行
// 「已安装，每 N 分钟自动运行」同源（GET /api/radars 的 interval_s）；launchd 还没回 / 问不到 → 句子省掉数字；
// 「状态未知」只在投影里根本没有这一源时；测试一轮落笔后句子换成真实结果。
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchRadarAgents, postAction, postRadarReinstall } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, resetStoreForTests } from "../../store";
import type { Board, RadarSourceHealth } from "../../types";
import { RadarAgentPanel } from "./RadarAgentPanel";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchRadarAgents: vi.fn(), postAction: vi.fn(), postRadarReinstall: vi.fn() };
});

const blank: RadarSourceHealth = { enabled: true, last_ok: null, skip_reason: null, stale: false, last_attempt: null, test_round: null };

function board(sources: Record<string, RadarSourceHealth>): Board {
  return {
    generated_at: "2026-09-03T12:00:00Z",
    needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [], archived: [],
    counts: {},
    radar_sources: sources,
  } as unknown as Board;
}

async function seedBoard(sources: Record<string, RadarSourceHealth>) {
  vi.mocked(fetchBoard).mockResolvedValue(board(sources));
  await refreshBoard();
}

function renderPanel(source: "gmail" | "slack", language: "zh" | "en" = "en") {
  return render(
    <LanguageContext.Provider value={language}>
      <RadarAgentPanel source={source} />
    </LanguageContext.Provider>,
  );
}

const agents = {
  radars: {
    gmail: { label: "com.zelin.aiassistant.gmailradar", interval_s: 300, loaded: true, plist_installed: true },
    slack: { label: "com.zelin.aiassistant.slackradar", interval_s: 180, loaded: true, plist_installed: true },
  },
};

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchBoard).mockReset();
  vi.mocked(fetchRadarAgents).mockReset();
  vi.mocked(postAction).mockReset();
  vi.mocked(postRadarReinstall).mockReset();
});
afterEach(cleanup);

describe("<RadarAgentPanel /> no-runs-yet sentence", () => {
  it("N comes from the same launchd interval as the installed line (gmail 5 / slack 3)", async () => {
    vi.mocked(fetchRadarAgents).mockResolvedValue(agents);
    await seedBoard({ gmail: blank, slack: blank });
    renderPanel("gmail");
    await screen.findByText("installed — runs every 5 minutes");
    expect(screen.getByText("No runs recorded yet. Wait one round (≤5 min) or click \"Test one round now\".")).toBeTruthy();
    expect(screen.queryByText("unknown")).toBeNull();
    cleanup();
    renderPanel("slack", "zh");
    await screen.findByText("已安装，每 3 分钟自动运行");
    expect(screen.getByText("还没有运行记录。等一轮（≤3 分钟）或点「立即测试一轮」。")).toBeTruthy();
  });

  it("drops the number while launchd has not answered and when it cannot be asked", async () => {
    let resolve: (v: unknown) => void = () => undefined;
    vi.mocked(fetchRadarAgents).mockReturnValue(new Promise((r) => { resolve = r; }) as never);
    await seedBoard({ gmail: blank });
    renderPanel("gmail");
    expect(screen.getByText("checking…")).toBeTruthy();
    expect(screen.getByText("No runs recorded yet. Wait one round or click \"Test one round now\".")).toBeTruthy();
    await act(async () => { resolve(agents); });
    await screen.findByText("installed — runs every 5 minutes");
    expect(screen.getByText("No runs recorded yet. Wait one round (≤5 min) or click \"Test one round now\".")).toBeTruthy();
    cleanup();
    vi.mocked(fetchRadarAgents).mockRejectedValue(new Error("offline"));
    renderPanel("gmail");
    await screen.findByText("unknown");
    expect(screen.getByText("No runs recorded yet. Wait one round or click \"Test one round now\".")).toBeTruthy();
  });

  it("a source missing from the projection still reads as unknown, not as no runs yet", async () => {
    vi.mocked(fetchRadarAgents).mockResolvedValue(agents);
    await seedBoard({});
    renderPanel("gmail");
    await screen.findByText("installed — runs every 5 minutes");
    expect(screen.getByText("unknown")).toBeTruthy();
    expect(screen.queryByText(/No runs recorded yet/)).toBeNull();
  });

  it("the sentence yields to the real result once a round has written the health file", async () => {
    vi.mocked(fetchRadarAgents).mockResolvedValue(agents);
    await seedBoard({ gmail: blank });
    renderPanel("gmail");
    await screen.findByText(/No runs recorded yet/);
    await seedBoard({ gmail: { ...blank, skip_reason: "command_failed", last_attempt: "2026-09-03T11:59:00Z" } });
    expect(screen.queryByText(/No runs recorded yet/)).toBeNull();
    expect(screen.getByText(/The fetch command failed/).textContent).toMatch(/\(last round .*\)$/);
    await seedBoard({ gmail: { ...blank, last_ok: "2026-09-03T12:00:00Z", last_attempt: "2026-09-03T12:00:00Z" } });
    expect(screen.getByText(/Working ✓ last success/)).toBeTruthy();
  });
});
