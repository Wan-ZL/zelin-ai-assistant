// PipelineBanner（§47.4）：/api/health verdict → 横幅文案分派；与离线横幅互斥。
// 2026-08-31 静默卡死的 web 替身：stalled 必须指名 kickstart 命令与最后阶段。
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

  it("stalled names the age, the last phase and the kickstart command", () => {
    const d = describeHealth(
      snap({
        verdict: "stalled",
        heartbeat: { age_s: 150 * 60, phase: "reconcile", pid: 4242, interval: 10, stale_after_s: 90, stale: true },
      }),
      en,
    );
    expect(d?.tone).toBe("danger");
    expect(d?.title).toBe("Background service is stuck");
    expect(d?.detail).toContain("150 min");
    expect(d?.detail).toContain("reconcile");
    expect(d?.detail).toContain("launchctl kickstart -k gui/$(id -u)/com.zelin.aiassistant.actd");
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
