// 向导末步「首次数据」行对读被拒的分派（CONTRACT §47.4 追记 2026-09-18，issue #423）。
// 病灶：那一行只看 health.dashboard，null 就说「还没有——后台服务启动后约 10 秒自动生成」并给「立即生成一次」。
// 文件在、只是读不动时，前半句是假话，而那颗按钮会 POST /api/setup/seed-dashboard → write_dashboard 原子替换
// dashboard.json，而 registry 对读不动的卡片文件是**静默跳过**——于是一份可能零卡的看板盖掉最后一份好快照。
// 自此：health.dashboard_error 在场 → 行说读不出来（带 errno）+ **撤掉 fix**（播种治不了权限）。
// 这是 issue 里 owner 实际落到的那块屏幕。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchHealth } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshHealth, resetStoreForTests } from "../../store";
import type { HealthSnapshot, SetupEngine } from "../../types";
import { FinaleStep } from "./FinaleStep";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchHealth: vi.fn(), postRepairActd: vi.fn(), postSeedDashboard: vi.fn(), postAnalytics: vi.fn() };
});

const fetchHealthMock = vi.mocked(fetchHealth);

const ENGINE: SetupEngine = { cli_path: "/usr/local/bin/claude", version: "1.0.99", auth: "api_key", auth_sources: {}, ready: true };

/** 读被拒：actd 还活着（心跳新鲜、每 5 s 还在重写看板），只是 server 这个进程读不动那个文件 */
const DENIED: HealthSnapshot = {
  verdict: "ok",
  heartbeat: { age_s: 4, phase: "idle", pid: 4242, interval: 10, stale_after_s: 90, stale: false },
  dashboard: null,
  dashboard_error: { path: "/Volumes/Storage/.../state/dashboard.json", errno: 1, strerror: "Operation not permitted" },
  loop_health: { consecutive_failures: 0, last_error: null },
  checked_at: "2026-09-18T08:00:05Z",
};
/** 真的还没生成（首次安装）：老行为一字不动 */
const NOT_YET: HealthSnapshot = { ...DENIED, dashboard: null, dashboard_error: null };

function dataRow() {
  return document.querySelector("[data-row='data']") as HTMLElement;
}

async function renderFinale(health: HealthSnapshot) {
  fetchHealthMock.mockResolvedValue(health);
  await refreshHealth();
  render(
    <LanguageContext.Provider value="en">
      <FinaleStep engine={ENGINE} engineChecking={false} goEngine={() => undefined} />
    </LanguageContext.Provider>,
  );
}

beforeEach(() => {
  resetStoreForTests();
  fetchHealthMock.mockReset();
});
afterEach(cleanup);

describe("FinaleStep · 首次数据行 · 读被拒", () => {
  it("says the file can't be read, with the errno — not 'not yet'", async () => {
    await renderFinale(DENIED);
    const row = dataRow();
    expect(row.className).toContain("is-fail");
    expect(row.textContent).toContain("can't be read");
    expect(row.textContent).toContain("errno 1");
    expect(row.textContent).toContain("Operation not permitted");
    expect(row.textContent).not.toContain("Not yet");
  });

  it("drops the 'Generate now' button — seeding can overwrite the last good board", async () => {
    await renderFinale(DENIED);
    expect(screen.queryByRole("button", { name: "Generate now" })).toBeNull();
  });

  it("a genuinely un-generated board keeps the old copy and the button", async () => {
    await renderFinale(NOT_YET);
    const row = dataRow();
    expect(row.textContent).toContain("Not yet");
    expect(screen.getByRole("button", { name: "Generate now" })).toBeTruthy();
  });
});
