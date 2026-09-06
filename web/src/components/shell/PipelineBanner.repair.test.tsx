// 横幅一键修复的诚实下场（§68.8 追记；原生 Doctor.swift PipelineRepair + Freshness.swift 203-239）：
//   POST 成功后每 1 s 问一次 /api/health、最多 15 轮 → 「已恢复 ✓ 数据重新更新了」停 6 s 再刷 store（横幅退场）；
//   15 轮没转好 → 「自动修复没成功：后台服务已重启，但数据还没更新——…」+ 再试一次 + 让 AI 修 + 可复制的手动命令；
//   POST 被拒 → 同一失败行，原因 = server 原文。三态横幅动作行都带可复制的「手动命令：」（原生 CopyPathLine）。
// 假定时器驱动轮询；health 假值一律 mockResolvedValue（§66 追记：Once 会被后台刷新吃掉）。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchHealth, postAiFixDoctor, postRepairActd } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshHealth, resetStoreForTests } from "../../store";
import type { HealthSnapshot } from "../../types";
import { PipelineBanner, RepairButton, RESTART_CMD } from "./PipelineBanner";
import { REPAIR_POLL_MS, REPAIR_POLL_ROUNDS, REPAIR_SUCCESS_MS } from "./repairActd";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchHealth: vi.fn(), postRepairActd: vi.fn(), postAiFixDoctor: vi.fn() };
});

const fetchHealthMock = vi.mocked(fetchHealth);
const repairMock = vi.mocked(postRepairActd);
const aiFixMock = vi.mocked(postAiFixDoctor);

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

const stalled = snap({
  verdict: "stalled",
  heartbeat: { age_s: 9000, phase: "dispatch", pid: 1, interval: 10, stale_after_s: 90, stale: true },
});

const RECOVERED = "Recovered ✓ data is updating again";

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

/** 走过 n 轮轮询（每轮 1 s 定时器 + fetchHealth 的 promise 落地） */
async function pollRounds(n: number) {
  for (let i = 0; i < n; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REPAIR_POLL_MS);
    });
  }
}

beforeEach(() => {
  resetStoreForTests();
  fetchHealthMock.mockReset();
  repairMock.mockReset();
  aiFixMock.mockReset();
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("RepairButton — 15 s recovery poll", () => {
  it("pins the native cadence as literals: 15 rounds × 1 s, success shown 6 s (Doctor.swift:367-368, :383)", () => {
    // 其余用例拿常量驱动假时钟——常量改了它们照样过；本例才是把原生数字钉死的判例（§68.8 追记）
    expect([REPAIR_POLL_ROUNDS, REPAIR_POLL_MS, REPAIR_SUCCESS_MS]).toEqual([15, 1000, 6000]);
  });

  it("recovers: health turns ok on the 3rd poll → 已恢复 ✓ for 6 s, then the store is refreshed once", async () => {
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
    fetchHealthMock.mockResolvedValue(stalled);
    renderEn(<RepairButton verdict="stalled" />);
    fireEvent.click(screen.getByRole("button", { name: "Fix now" }));
    await act(async () => { await Promise.resolve(); });
    expect(repairMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Repairing…" })).toBeTruthy();
    expect(screen.getByText("Restarting the background service and waiting for data (up to 15 s)…")).toBeTruthy();
    await pollRounds(2);
    expect(fetchHealthMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/Recovered/)).toBeNull();
    fetchHealthMock.mockResolvedValue(snap());
    await pollRounds(1);
    expect(fetchHealthMock).toHaveBeenCalledTimes(3);
    const verdict = screen.getByText(RECOVERED);
    expect(verdict.getAttribute("role")).toBe("status");
    expect(verdict.className).toContain("is-success");
    expect(screen.queryByRole("button", { name: "Fix now" })).toBeNull();
    // 庆祝期间不再轮询、也不刷 store；6 s 后 idle + refreshHealth（第 4 次 fetch）
    await act(async () => { await vi.advanceTimersByTimeAsync(REPAIR_SUCCESS_MS - 1); });
    expect(fetchHealthMock).toHaveBeenCalledTimes(3);
    expect(screen.getByText(RECOVERED)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(fetchHealthMock).toHaveBeenCalledTimes(4);
    expect(screen.queryByText(RECOVERED)).toBeNull();
    expect(screen.getByRole("button", { name: "Fix now" })).toBeTruthy();
  });

  it("times out: 15 polls still stalled → honest failure row with 再试一次, 让 AI 修 and the copyable command", async () => {
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
    fetchHealthMock.mockResolvedValue(stalled);
    renderEn(<RepairButton verdict="stalled" />);
    fireEvent.click(screen.getByRole("button", { name: "Fix now" }));
    await act(async () => { await Promise.resolve(); });
    await pollRounds(REPAIR_POLL_ROUNDS - 1);
    expect(fetchHealthMock).toHaveBeenCalledTimes(REPAIR_POLL_ROUNDS - 1);
    expect(screen.getByRole("button", { name: "Repairing…" })).toBeTruthy();
    await pollRounds(1);
    expect(fetchHealthMock).toHaveBeenCalledTimes(REPAIR_POLL_ROUNDS);
    // 原生 Freshness.swift 失败行：前缀与原因两个节点、再试一次、让 AI 修、手动命令
    expect(screen.getByText("Auto-repair didn't work:")).toBeTruthy();
    expect(screen.getByText("Service restarted but data still isn't updating — try \"Fix with AI\" or view the log")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Fix with AI" })).toBeTruthy();
    expect(screen.getByText("Manual command:")).toBeTruthy();
    expect(screen.getByText(RESTART_CMD).tagName).toBe("CODE");
    expect(screen.queryByText(/up to 15 s/)).toBeNull(); // 等待句不再永远挂着
    // 没有更多轮询在飞
    await pollRounds(3);
    expect(fetchHealthMock).toHaveBeenCalledTimes(REPAIR_POLL_ROUNDS);
  });

  it("让 AI 修 posts /api/ai-fix {source: doctor} in the UI language; a refused launch shows the native prefix + server text", async () => {
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
    fetchHealthMock.mockResolvedValue(stalled);
    aiFixMock.mockRejectedValue(new Error("claude CLI missing"));
    renderEn(<RepairButton verdict="stalled" />);
    fireEvent.click(screen.getByRole("button", { name: "Fix now" }));
    await act(async () => { await Promise.resolve(); });
    await pollRounds(REPAIR_POLL_ROUNDS);
    fireEvent.click(screen.getByRole("button", { name: "Fix with AI" }));
    expect(screen.getByRole("button", { name: "Preparing the diagnostic bundle…" })).toBeTruthy();
    await act(async () => { await Promise.resolve(); });
    expect(aiFixMock).toHaveBeenCalledWith("en");
    expect(screen.getByText("Fix with AI failed to launch: claude CLI missing")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Fix with AI" })).toBeTruthy();
  });

  it("a health fetch that throws mid-poll is one missed round, not a verdict", async () => {
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
    fetchHealthMock.mockRejectedValue(new Error("ECONNREFUSED"));
    renderEn(<RepairButton verdict="stalled" />);
    fireEvent.click(screen.getByRole("button", { name: "Fix now" }));
    await act(async () => { await Promise.resolve(); });
    await pollRounds(2);
    expect(screen.getByRole("button", { name: "Repairing…" })).toBeTruthy();
    fetchHealthMock.mockResolvedValue(snap({ verdict: "unknown", heartbeat: null }));
    await pollRounds(1);
    expect(screen.getByText(RECOVERED)).toBeTruthy();
  });

  it("a refused POST is the same failure row with the server's sentence; 再试一次 re-posts", async () => {
    repairMock.mockRejectedValue(new Error("com.zelin.aiassistant.actd is not loaded in launchd - run `bash install.sh`"));
    renderEn(<RepairButton verdict="stale" />);
    fireEvent.click(screen.getByRole("button", { name: "Start service" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("Start didn't work:")).toBeTruthy();
    expect(screen.getByText(/not loaded in launchd/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Fix with AI" })).toBeTruthy();
    expect(fetchHealthMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await act(async () => { await Promise.resolve(); });
    expect(repairMock).toHaveBeenCalledTimes(2);
  });

  it("unmount during the poll clears the timer — no fetch, no state update afterwards", async () => {
    repairMock.mockResolvedValue({ ok: true, label: "com.zelin.aiassistant.actd", action: "kickstart" });
    fetchHealthMock.mockResolvedValue(stalled);
    const view = renderEn(<RepairButton verdict="stalled" />);
    fireEvent.click(screen.getByRole("button", { name: "Fix now" }));
    await act(async () => { await Promise.resolve(); });
    await pollRounds(1);
    view.unmount();
    await pollRounds(REPAIR_POLL_ROUNDS);
    expect(fetchHealthMock).toHaveBeenCalledTimes(1);
  });
});

describe("<PipelineBanner> manual command", () => {
  it("every speaking verdict carries the kickstart command as a copyable line; the chip writes the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    fetchHealthMock.mockResolvedValue(stalled);
    await refreshHealth();
    renderEn(<PipelineBanner />);
    expect(screen.getByText("Manual command:")).toBeTruthy();
    expect(screen.getByText(RESTART_CMD).tagName).toBe("CODE");
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await act(async () => { await Promise.resolve(); });
    expect(writeText).toHaveBeenCalledWith(RESTART_CMD);
    expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy();
    expect(screen.getByText("Copied to clipboard", { selector: "[role='status']" })).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
  });

  it("stale and failing banners carry the same line", async () => {
    for (const health of [
      snap({ verdict: "stale", heartbeat: null, dashboard: { generated_at: "x", age_s: 600, stale: true } }),
      snap({ verdict: "failing", loop_health: { consecutive_failures: 3, last_error: "boom" } }),
    ]) {
      fetchHealthMock.mockResolvedValue(health);
      await refreshHealth();
      renderEn(<PipelineBanner />);
      expect(screen.getByRole("alert").getAttribute("data-verdict")).toBe(health.verdict);
      expect(screen.getByText("Manual command:")).toBeTruthy();
      expect(screen.getByText(RESTART_CMD).tagName).toBe("CODE");
      cleanup();
    }
  });
});
