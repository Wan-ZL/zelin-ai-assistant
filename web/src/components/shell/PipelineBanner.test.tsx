// PipelineBanner（§47.4）：/api/health verdict → 横幅文案分派；与离线横幅互斥。
// 2026-08-31 静默卡死的 web 替身：stalled 必须指名最后阶段，横幅必须带 kickstart 命令（可复制的「手动命令：」行，
// 见 PipelineBanner.repair.test.tsx）；正文只说已知的——server 不探进程，「进程还活着」不许出现（§47.4 读者 3）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchHealth } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshHealth, resetStoreForTests, setConnection } from "../../store";
import type { HealthSnapshot } from "../../types";
import { describeHealth, PipelineBanner } from "./PipelineBanner";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchHealth: vi.fn() };
});

const fetchHealthMock = vi.mocked(fetchHealth);
const en = (_zh: string, english: string) => english;

function snap(overrides: Partial<HealthSnapshot> = {}): HealthSnapshot {
  return {
    verdict: "ok",
    heartbeat: { age_s: 4, phase: "idle", pid: 4242, interval: 10, stale_after_s: 90, stale: false },
    dashboard: { generated_at: "2026-09-01T08:00:00Z", age_s: 5, stale: false },
    loop_health: { consecutive_failures: 0, last_error: null },
    checked_at: "2026-09-01T08:00:05Z",
    ...overrides,
  };
}

function renderBanner() {
  return render(
    <LanguageContext.Provider value="en">
      <PipelineBanner />
    </LanguageContext.Provider>,
  );
}

beforeEach(() => {
  resetStoreForTests();
  fetchHealthMock.mockReset();
});
afterEach(cleanup);

describe("describeHealth", () => {
  it("ok / unknown say nothing", () => {
    expect(describeHealth(snap(), en)).toBeNull();
    expect(describeHealth(snap({ verdict: "unknown", heartbeat: null }), en)).toBeNull();
  });

  it("stalled names the age and the last phase, says only what is known (stuck or stopped), no inline command", () => {
    const stalled = snap({
      verdict: "stalled",
      heartbeat: { age_s: 150 * 60, phase: "reconcile", pid: 4242, interval: 10, stale_after_s: 90, stale: true },
    });
    const d = describeHealth(stalled, en);
    expect(d?.tone).toBe("danger");
    expect(d?.title).toBe("Background service is stuck");
    expect(d?.detail).toBe("No heartbeat for 150 min (last phase: reconcile) — stuck or stopped; cards will not move.");
    // server/health.py 只 stat 文件、不探进程：一个崩了的 actd 留下的心跳文件也长这样——不许断言「进程还活着」
    expect(d?.detail).not.toMatch(/alive/);
    const zh = describeHealth(stalled, (chinese) => chinese);
    expect(zh?.detail).toBe("后台服务已 150 分钟没有心跳（最后阶段：reconcile）——卡在原地或已停止，卡片不会动。");
    expect(zh?.detail).not.toContain("还活着");
    // 命令不再揉进句子：它是动作行里那条可复制的「手动命令：」（PipelineBanner.repair.test.tsx）
    expect(d?.detail).not.toContain("launchctl");
  });

  it("failing carries the crash count and last error", () => {
    const d = describeHealth(
      snap({ verdict: "failing", loop_health: { consecutive_failures: 3, last_error: "NameError: x" } }),
      en,
    );
    expect(d?.tone).toBe("danger");
    expect(d?.detail).toContain("3 passes");
    expect(d?.detail).toContain("NameError: x");
  });

  it("stale is the softer warning with the board age", () => {
    const d = describeHealth(
      snap({ verdict: "stale", heartbeat: null, dashboard: { generated_at: "x", age_s: 600, stale: true } }),
      en,
    );
    expect(d?.tone).toBe("warning");
    expect(d?.title).toBe("Background service is not running");
    expect(d?.detail).toContain("10 min");
    expect(d?.detail).toContain("bash install.sh");
    expect(d?.detail).not.toContain("launchctl"); // 命令住可复制的「手动命令：」行，不在句子里
  });

  // §47.4 追记 2026-09-18（issue #423）：dashboard == null 有两种原因，此前一律说「从未生成」
  it("stale without a board says 'never generated' only when nobody was denied a read", () => {
    const d = describeHealth(snap({ verdict: "stale", heartbeat: null, dashboard: null }), en);
    expect(d?.detail).toContain("The board was never generated");
  });

  it("stale + a read denial says the board can't be read (with the errno), not 'never generated'", () => {
    const d = describeHealth(
      snap({
        verdict: "stale",
        heartbeat: null,
        dashboard: null,
        dashboard_error: { path: "/x/state/dashboard.json", errno: 1, strerror: "Operation not permitted" },
      }),
      en,
    );
    expect(d?.detail).toContain("can't be read");
    expect(d?.detail).toContain("errno 1");
    expect(d?.detail).not.toContain("never generated");   // 文件在，没人查过它在不在
  });

  it("a read denial NEVER outranks stalled / failing — those are the more urgent truths", () => {
    const denial = { path: "/x", errno: 1, strerror: "Operation not permitted" };
    const stalled = describeHealth(
      snap({
        verdict: "stalled",
        heartbeat: { age_s: 3600, phase: "dispatch", pid: 1, interval: 10, stale_after_s: 90, stale: true },
        dashboard: null,
        dashboard_error: denial,
      }),
      en,
    );
    expect(stalled?.title).toBe("Background service is stuck");
    const failing = describeHealth(
      snap({ verdict: "failing", dashboard: null, dashboard_error: denial, loop_health: { consecutive_failures: 4, last_error: "boom" } }),
      en,
    );
    expect(failing?.title).toBe("Background service crashes every pass");
  });

  it("does NOT grow a banner of its own for a read denial on an otherwise healthy pipeline", () => {
    // 看板页那句话由 §49 追记 2026-09-18 的 boardUnreadable 面说；两条横幅说同一句 =
    // §47.4 读者 3 明令禁止的「同一信息双份」
    const d = describeHealth(
      snap({ verdict: "ok", dashboard: null, dashboard_error: { path: "/x", errno: 1, strerror: "x" } }),
      en,
    );
    expect(d).toBeNull();
  });
});

describe("<PipelineBanner>", () => {
  it("renders nothing before the first health fetch and on ok", async () => {
    renderBanner();
    expect(screen.queryByRole("alert")).toBeNull();
    fetchHealthMock.mockResolvedValue(snap());
    await refreshHealth();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the stalled banner once the store has a stalled snapshot", async () => {
    fetchHealthMock.mockResolvedValue(
      snap({
        verdict: "stalled",
        heartbeat: { age_s: 9000, phase: "dispatch", pid: 1, interval: 10, stale_after_s: 90, stale: true },
      }),
    );
    await refreshHealth();
    renderBanner();
    const alert = screen.getByRole("alert");
    expect(alert.getAttribute("data-verdict")).toBe("stalled");
    expect(alert.className).toContain("is-danger");
    expect(screen.getByText("Background service is stuck")).toBeTruthy();
  });

  it("yields to the offline banner while the server is unreachable", async () => {
    fetchHealthMock.mockResolvedValue(snap({ verdict: "stalled" }));
    await refreshHealth();
    setConnection("reconnecting");
    renderBanner();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a failed health fetch keeps the previous snapshot instead of blanking", async () => {
    fetchHealthMock.mockResolvedValue(snap({ verdict: "stale", heartbeat: null }));
    await refreshHealth();
    fetchHealthMock.mockRejectedValue(new Error("boom"));
    await refreshHealth();
    renderBanner();
    expect(screen.getByRole("alert").getAttribute("data-verdict")).toBe("stale");
  });
});
