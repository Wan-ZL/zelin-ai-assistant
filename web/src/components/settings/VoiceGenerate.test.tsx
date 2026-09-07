// 语气档案「从我的消息生成/更新档案」（CONTRACT §68.1 追记 / §10 voice_generate / §49 generate-status；D47；
// 原生 Settings.swift runVoiceGen）：按钮 = POST /api/actions {action:"voice_generate"}，当拍换「生成中…」并禁用；
// 忙着才每 3 s 轮询 GET /api/voice/generate-status；新回执 started_at 变了即交棒；done → 工具那一句（缺席「已生成 ✓」）
// 且重拉 store.voiceProfile；failed → 错误原文；lost → 一句 + 解锁；90 s 没人接手 → 一句 + 解锁；刷新回来 running 接着忙。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchVoiceGenerateStatus, fetchVoiceProfile, postAction } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { VoiceGenJob } from "../../types";
import { generateStatusLine, isGenerating, PICKUP_TIMEOUT_MS, POLL_MS, VoiceGenerate } from "./VoiceGenerate";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchVoiceGenerateStatus: vi.fn(), fetchVoiceProfile: vi.fn(), postAction: vi.fn() };
});

const job = (status: VoiceGenJob["status"], started: string, extra: Partial<VoiceGenJob> = {}): VoiceGenJob => ({
  status, started_at: started, finished_at: status === "running" ? null : started, error: null, message: null, profile_path: null, lost: false, ...extra,
});
const text = (zh: string, en: string) => en;

function renderEn() {
  return render(<LanguageContext.Provider value="en"><VoiceGenerate /></LanguageContext.Provider>);
}

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchVoiceGenerateStatus).mockReset();
  vi.mocked(fetchVoiceProfile).mockReset();
  vi.mocked(postAction).mockReset();
  vi.mocked(fetchVoiceProfile).mockResolvedValue({ enabled: true, private_path: "/h/state/voice-profile.md", private_exists: true, default_path: "/h/config/voice-profile.default.md", default_exists: true, effective_path: "/h/state/voice-profile.md" });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("VoiceGenerate", () => {
  it("renders the native button + helper; no run yet → no result line", async () => {
    vi.mocked(fetchVoiceGenerateStatus).mockResolvedValue({ job: null });
    renderEn();
    await flush();
    const button = screen.getByRole("button", { name: "Generate from my messages" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(screen.getByText("Requires the Slack connection; the existing profile is backed up automatically before generating.")).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
    expect(fetchVoiceGenerateStatus).toHaveBeenCalledTimes(1);
  });

  it("click → POST voice_generate, button flips to Generating… (disabled) on the same tick, busy sentence shows", async () => {
    vi.mocked(fetchVoiceGenerateStatus).mockResolvedValue({ job: null });
    vi.mocked(postAction).mockResolvedValue({ ok: true });
    renderEn();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Generate from my messages" }));
    const busy = screen.getByRole("button", { name: "Generating…" }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toBe("Generating… reads Slack messages you sent recently; this can take a few minutes.");
    await flush();
    expect(postAction).toHaveBeenCalledWith({ action: "voice_generate" });
  });

  it("polls every 3 s while busy; the new receipt takes over, done shows the tool's line and refreshes the profile row", async () => {
    vi.useFakeTimers();
    vi.mocked(fetchVoiceGenerateStatus).mockResolvedValue({ job: job("done", "2026-09-06T10:00:00Z", { message: "old" }) });
    vi.mocked(postAction).mockResolvedValue({ ok: true });
    renderEn();
    await flush();
    expect(screen.getByRole("status").textContent).toBe("old");
    fireEvent.click(screen.getByRole("button", { name: "Generate from my messages" }));
    await flush();
    // actd 接手：新 started_at、running → 交棒（按钮仍「生成中…」）
    vi.mocked(fetchVoiceGenerateStatus).mockResolvedValue({ job: job("running", "2026-09-06T12:00:00Z") });
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });
    expect(fetchVoiceGenerateStatus).toHaveBeenCalledTimes(2);
    expect((screen.getByRole("button", { name: "Generating…" }) as HTMLButtonElement).disabled).toBe(true);
    // 交棒之后 90 s 兜底不再触发（忙态由 job 承担）
    await act(async () => { await vi.advanceTimersByTimeAsync(PICKUP_TIMEOUT_MS); });
    expect(screen.getByRole("button", { name: "Generating…" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    // 子进程写完：done + 那一句 → 按钮解锁、绿字、「当前生效」行重拉
    const profileCalls = vi.mocked(fetchVoiceProfile).mock.calls.length;
    vi.mocked(fetchVoiceGenerateStatus).mockResolvedValue({ job: job("done", "2026-09-06T12:00:00Z", { message: "Voice profile generated: /h/state/voice-profile.md (previous profile backed up as voice-profile.md.bak-1)", profile_path: "/h/state/voice-profile.md" }) });
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });
    expect((screen.getByRole("button", { name: "Generate from my messages" }) as HTMLButtonElement).disabled).toBe(false);
    const line = screen.getByRole("status");
    expect(line.textContent).toContain("Voice profile generated:");
    expect(line.className).toBe("settings-helper is-ok");
    expect(vi.mocked(fetchVoiceProfile).mock.calls.length).toBe(profileCalls + 1);
    // 停了就不再轮询
    const polls = vi.mocked(fetchVoiceGenerateStatus).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 3); });
    expect(vi.mocked(fetchVoiceGenerateStatus).mock.calls.length).toBe(polls);
  });

  it("survives a reload: mounted while running → busy right away, keeps polling", async () => {
    vi.useFakeTimers();
    vi.mocked(fetchVoiceGenerateStatus).mockResolvedValue({ job: job("running", "2026-09-06T12:00:00Z") });
    renderEn();
    await flush();
    expect((screen.getByRole("button", { name: "Generating…" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 2); });
    expect(fetchVoiceGenerateStatus).toHaveBeenCalledTimes(3);
    expect(postAction).not.toHaveBeenCalled();
  });

  it("done without a stdout line falls back to Generated ✓; failed shows the error verbatim in orange", async () => {
    vi.mocked(fetchVoiceGenerateStatus).mockResolvedValue({ job: job("done", "2026-09-06T12:00:00Z") });
    renderEn();
    await flush();
    expect(screen.getByRole("status").textContent).toBe("Generated ✓");
    cleanup();
    vi.mocked(fetchVoiceGenerateStatus).mockResolvedValue({ job: job("failed", "2026-09-06T12:00:00Z", { error: "Generation failed: claude exited with an error (most common cause: the Slack MCP server is not connected). The old profile is untouched." }) });
    renderEn();
    await flush();
    const line = screen.getByRole("alert");
    expect(line.textContent).toContain("Generation failed: claude exited with an error");
    expect(line.className).toBe("settings-warning");
    expect((screen.getByRole("button", { name: "Generate from my messages" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("a lost run (running > 15 min, server says lost) unlocks the button with an honest sentence", async () => {
    vi.mocked(fetchVoiceGenerateStatus).mockResolvedValue({ job: job("running", "2026-09-06T10:00:00Z", { lost: true }) });
    renderEn();
    await flush();
    expect((screen.getByRole("button", { name: "Generate from my messages" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByRole("alert").textContent).toBe("No word from this run: no result written for over 15 minutes (see state/voice_gen/run.log)");
  });

  it("nothing picks the request up for 90 s → honest note + unlocked button", async () => {
    vi.useFakeTimers();
    vi.mocked(fetchVoiceGenerateStatus).mockResolvedValue({ job: null });
    vi.mocked(postAction).mockResolvedValue({ ok: true });
    renderEn();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Generate from my messages" }));
    await flush();
    await act(async () => { await vi.advanceTimersByTimeAsync(PICKUP_TIMEOUT_MS - 1); });
    expect(screen.getByRole("button", { name: "Generating…" })).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect((screen.getByRole("button", { name: "Generate from my messages" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByRole("alert").textContent).toBe("Nothing picked the request up: actd may not be running (see Pipeline liveness under Dependency check)");
  });

  it("a rejected POST unlocks the button and shows the server's error", async () => {
    vi.mocked(fetchVoiceGenerateStatus).mockResolvedValue({ job: null });
    vi.mocked(postAction).mockRejectedValue(new Error("state/inbox is not writable"));
    renderEn();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Generate from my messages" }));
    await flush();
    expect((screen.getByRole("button", { name: "Generate from my messages" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByRole("alert").textContent).toContain("state/inbox is not writable");
  });

  it("pure helpers follow the native voiceGenStatus table", () => {
    expect(isGenerating(null, false)).toBe(false);
    expect(isGenerating(null, true)).toBe(true);
    expect(isGenerating(job("running", "t"), false)).toBe(true);
    expect(isGenerating(job("running", "t", { lost: true }), false)).toBe(false);
    expect(generateStatusLine(null, false, text)).toBeNull();
    expect(generateStatusLine(job("done", "t"), false, text)).toEqual({ tone: "ok", message: "Generated ✓" });
    expect(generateStatusLine(job("done", "t", { message: "line" }), false, text)).toEqual({ tone: "ok", message: "line" });
    expect(generateStatusLine(job("failed", "t"), false, text)?.message).toBe("Generation failed with no further output (see state/voice_gen/run.log).");
    expect(generateStatusLine(job("failed", "t"), true, text)?.tone).toBe("busy");
  });
});
