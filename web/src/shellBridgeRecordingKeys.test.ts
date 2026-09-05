// 壳桥 add-only 键的镜像判例（§61.1 追记，parity batch shell-recording-bridge）：
// recording.self_heal_note / log_tail、captions.translation_note / translation_active /
// source_note / apple_engine_available、permissions.screen_requested 在 normalize 后永远在场
// （老壳缺席 → "" / false），壳给的值逐字落下；`refreshRecording` 在方法词表里、经 callShell 原样发出。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { callShell, normalizeShellState, resetShellBridgeForTests } from "./shellBridge";

describe("shellBridge · §61.1 追记 recording/captions/permissions add-only keys", () => {
  beforeEach(() => {
    resetShellBridgeForTests();
  });

  afterEach(() => {
    delete window.webkit;
  });

  it("老壳缺席 → 每个新键都补默认（'' / false），页面永不读到 undefined", () => {
    const s = normalizeShellState({ recording: { mode: "screen" }, captions: {}, permissions: {} });
    expect(s.recording.self_heal_note).toBe("");
    expect(s.recording.log_tail).toBe("");
    expect(s.captions.translation_note).toBe("");
    expect(s.captions.translation_active).toBe(false);
    expect(s.captions.source_note).toBe("");
    expect(s.captions.apple_engine_available).toBe(false);
    expect(s.permissions.screen_requested).toBe(false);
    // 整个快照都缺也一样
    const empty = normalizeShellState(null);
    expect(empty.recording.log_tail).toBe("");
    expect(empty.captions.translation_active).toBe(false);
    expect(empty.permissions.screen_requested).toBe(false);
  });

  it("壳给的值逐字落下；类型不对退回默认（LLM 式脏值不崩页面）", () => {
    const s = normalizeShellState({
      recording: {
        mode: "screen", diagnosis: "engine_crashed",
        self_heal_note: "屏幕权限已生效，录制引擎已自动重启",
        log_tail: "thread 'main' panicked\nno monitors",
      },
      captions: {
        translation_note: "还没有 Ark API Key——翻译要单独的 Ark 控制台 Key（和语音 Key 不是同一个）",
        translation_active: true,
        source_note: "缺屏幕录制权限，只在听麦克风",
        apple_engine_available: true,
      },
      permissions: { screen: "denied", screen_requested: true },
    });
    expect(s.recording.self_heal_note).toBe("屏幕权限已生效，录制引擎已自动重启");
    expect(s.recording.log_tail).toBe("thread 'main' panicked\nno monitors");
    expect(s.captions.translation_note).toContain("Ark API Key");
    expect(s.captions.translation_active).toBe(true);
    expect(s.captions.source_note).toBe("缺屏幕录制权限，只在听麦克风");
    expect(s.captions.apple_engine_available).toBe(true);
    expect(s.permissions.screen_requested).toBe(true);
    // 脏值：数字当句子、字串当布尔 → 默认
    const dirty = normalizeShellState({
      recording: { self_heal_note: 3, log_tail: null },
      captions: { translation_active: "yes", apple_engine_available: 1, source_note: [] },
      permissions: { screen_requested: "true" },
    });
    expect(dirty.recording.self_heal_note).toBe("");
    expect(dirty.recording.log_tail).toBe("");
    expect(dirty.captions.translation_active).toBe(false);
    expect(dirty.captions.apple_engine_available).toBe(false);
    expect(dirty.captions.source_note).toBe("");
    expect(dirty.permissions.screen_requested).toBe(false);
  });

  it("refreshRecording 在方法词表里：经 callShell 原样发出，回执照常落店", async () => {
    const postMessage = vi.fn(async (body: unknown) => {
      expect((body as { method: string }).method).toBe("refreshRecording");
      return { recording: { mode: "screen", engine_running: false, self_heal_note: "ok" }, captions: {}, permissions: { screen_requested: true } };
    });
    window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
    const state = await callShell("refreshRecording");
    expect(postMessage).toHaveBeenCalledWith({ method: "refreshRecording" });
    expect(state.recording.self_heal_note).toBe("ok");
    expect(state.permissions.screen_requested).toBe(true);
  });
});
