// D50（§68.8 追记）：server 对未加载的 actd 走 install.sh --reinstall-agent；只在没 pinned 解释器 / install.sh 不在时 409，
// envelope 的 details.command 是可复制的手动命令（bash <repo>/install.sh）。web 侧：
//   - 回执 action:"reinstall" 与 kickstart 走同一条 15 s 轮询（不分叉）；
//   - POST 被拒且 envelope 带 command → 失败行的「手动命令：」换成它（kickstart 对没装好的 agent 帮不上忙）；
//   - 没带 command（普通 500 / 网络错）→ 仍是默认的 kickstart 命令；再试成功后回到默认。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchHealth, postRepairActd } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { HealthSnapshot } from "../../types";
import { RepairButton, RESTART_CMD } from "./PipelineBanner";
import { manualCommandOf, REPAIR_POLL_MS } from "./repairActd";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchHealth: vi.fn(), postRepairActd: vi.fn(), postAiFixDoctor: vi.fn(), postAnalytics: vi.fn().mockResolvedValue({ ok: true, event: "pipeline_repair_result", logged: true }) };
});

const fetchHealthMock = vi.mocked(fetchHealth);
const repairMock = vi.mocked(postRepairActd);

const INSTALL_CMD = "bash /Users/demo/zelin-ai-assistant/install.sh";

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

const stale = snap({ verdict: "stale", heartbeat: null, dashboard: { generated_at: "x", age_s: 600, stale: true } });

function conflict(details: Record<string, unknown>) {
  return new ApiError(409, { error: { code: "CONFLICT", message: "com.zelin.aiassistant.actd is not loaded in launchd and no daemon interpreter is pinned - run `bash install.sh` once first", details } });
}

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

beforeEach(() => {
  resetStoreForTests();
  fetchHealthMock.mockReset();
  repairMock.mockReset();
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("manualCommandOf", () => {
  it("reads details.command off an ApiError; anything else is undefined", () => {
    expect(manualCommandOf(conflict({ fix: "bash install.sh", command: INSTALL_CMD }))).toBe(INSTALL_CMD);
    expect(manualCommandOf(conflict({ fix: "bash install.sh" }))).toBeUndefined();
    expect(manualCommandOf(conflict({ command: "   " }))).toBeUndefined();
    expect(manualCommandOf(conflict({ command: 42 }))).toBeUndefined();
    expect(manualCommandOf(new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "boom" } }))).toBeUndefined();
    expect(manualCommandOf(new Error("network down"))).toBeUndefined();
    expect(manualCommandOf(undefined)).toBeUndefined();
  });
});

describe("RepairButton × reinstall receipt", () => {
  it("action:\"reinstall\" rides the same 15 s poll as kickstart and recovers", async () => {
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "reinstall", loaded: true });
    fetchHealthMock.mockResolvedValue(stale);
    renderEn(<RepairButton verdict="stale" />);
    fireEvent.click(screen.getByRole("button", { name: "Start service" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("Starting and waiting for the first data…")).toBeTruthy();
    fetchHealthMock.mockResolvedValue(snap());
    await act(async () => { await vi.advanceTimersByTimeAsync(REPAIR_POLL_MS); });
    expect(screen.getByText("Recovered ✓ data is updating again")).toBeTruthy();
    // 成功路径的手动命令行仍是默认的 kickstart
    expect(screen.getByText(RESTART_CMD).tagName).toBe("CODE");
  });

  it("a 409 carrying details.command swaps the copyable manual command for the server's one", async () => {
    repairMock.mockRejectedValue(conflict({ label: "com.zelin.aiassistant.actd", fix: "bash install.sh", command: INSTALL_CMD, rc: 4 }));
    renderEn(<RepairButton verdict="stale" />);
    expect(screen.getByText(RESTART_CMD).tagName).toBe("CODE");
    fireEvent.click(screen.getByRole("button", { name: "Start service" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("Start didn't work:")).toBeTruthy();
    expect(screen.getByText(/no daemon interpreter is pinned/)).toBeTruthy();
    expect(screen.getByText("Manual command:")).toBeTruthy();
    expect(screen.getByText(INSTALL_CMD).tagName).toBe("CODE");
    expect(screen.queryByText(RESTART_CMD)).toBeNull();
    // 再试一次成功 → 命令行回到默认
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "reinstall", loaded: true });
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText(RESTART_CMD).tagName).toBe("CODE");
    expect(screen.queryByText(INSTALL_CMD)).toBeNull();
  });

  it("a refusal without details.command keeps the default kickstart command", async () => {
    repairMock.mockRejectedValue(new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "install.sh --reinstall-agent exited 1: failed to load", details: { label: "x", rc: 1 } } }));
    renderEn(<RepairButton verdict="stale" />);
    fireEvent.click(screen.getByRole("button", { name: "Start service" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText(/exited 1/)).toBeTruthy();
    expect(screen.getByText(RESTART_CMD).tagName).toBe("CODE");
  });
});
