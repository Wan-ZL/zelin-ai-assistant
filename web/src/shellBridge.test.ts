// 壳桥客户端判例（§61.1）：在场判定、快照 normalize 的 add-only 容错、callShell 的
// reject 透传、startShellBridge 的事件订阅与初始拉取。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  applyShellState,
  callShell,
  chooseFolder,
  getShellState,
  hasShellBridge,
  isBridgeUnavailable,
  normalizeShellState,
  resetShellBridgeForTests,
  SHELL_STATE_EVENT,
  startShellBridge,
  subscribeShellState,
} from "./shellBridge";

describe("shellBridge", () => {
  beforeEach(() => {
    resetShellBridgeForTests();
  });

  afterEach(() => {
    delete window.webkit;
  });

  it("hasShellBridge：只认 window.webkit.messageHandlers.zaiShell", () => {
    expect(hasShellBridge()).toBe(false);
    window.webkit = { messageHandlers: {} };
    expect(hasShellBridge()).toBe(false);
    window.webkit = { messageHandlers: { zaiShell: { postMessage: async () => ({}) } } };
    expect(hasShellBridge()).toBe(true);
  });

  it("normalize：缺字段取默认、未知字段忽略、on 从 mode 推导、diagnosis 非字符串 → null", () => {
    const s = normalizeShellState({ recording: { mode: "screen", diagnosis: null, future_key: 1 }, extra: true });
    expect(s.recording.mode).toBe("screen");
    expect(s.recording.on).toBe(true);
    expect(s.recording.engine_running).toBe(false);
    expect(s.recording.diagnosis).toBeNull();
    expect(s.recording.screen_permission).toBe(true);
    expect(s.recording.resume_mode).toBe("screen");
    expect(s.captions.on).toBe(false);
    expect(s.captions.engine).toBe("auto");
    expect(s.language).toBeUndefined();
    expect(normalizeShellState(null).recording.mode).toBe("off");
    expect(normalizeShellState("garbage").captions.available).toBe(false);
  });

  it("callShell：无桥 → NO_BRIDGE；壳 reject 字符串 → Error.message 原文；成功 → 快照落店", async () => {
    await expect(callShell("getState")).rejects.toThrow("NO_BRIDGE");
    const postMessage = vi.fn(async (body: unknown) => {
      if ((body as { method: string }).method === "setCaptions") throw "INVALID_ARGS: setCaptions needs on: bool";
      return { recording: { mode: "screen_audio", engine_running: true }, captions: { on: true }, language: "zh" };
    });
    window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
    await expect(callShell("setCaptions")).rejects.toThrow("INVALID_ARGS: setCaptions needs on: bool");
    const listener = vi.fn();
    subscribeShellState(listener);
    const state = await callShell("setRecording", { on: true, mode: "screen_audio" });
    expect(postMessage).toHaveBeenCalledWith({ method: "setRecording", on: true, mode: "screen_audio" });
    expect(state.recording.mode).toBe("screen_audio");
    expect(state.language).toBe("zh");
    expect(getShellState()).toEqual(state);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("chooseFolder：无桥 NO_BRIDGE；回执 dialog.path 落出（取消 → null）且快照照样落店；reject 原文透传", async () => {
    await expect(chooseFolder()).rejects.toThrow("NO_BRIDGE");
    const postMessage = vi.fn(async (body: unknown) => {
      const b = body as { method: string; current?: string };
      if (b.method !== "chooseFolder") throw "UNKNOWN_METHOD: " + b.method;
      return { recording: { mode: "screen" }, captions: {}, language: "en", dialog: { path: b.current ? "~/Picked" : null } };
    });
    window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
    expect(await chooseFolder({ current: "~/Notes", prompt: "选择" })).toBe("~/Picked");
    expect(postMessage).toHaveBeenCalledWith({ method: "chooseFolder", current: "~/Notes", prompt: "选择" });
    expect(getShellState()?.recording.mode).toBe("screen");
    expect(await chooseFolder({ current: "" })).toBeNull();
    // 老壳：reject 原文成为 Error.message；isBridgeUnavailable 只认 NO_BRIDGE / UNKNOWN_METHOD
    window.webkit = { messageHandlers: { zaiShell: { postMessage: async () => { throw "UNKNOWN_METHOD: chooseFolder"; } } } };
    await expect(chooseFolder()).rejects.toThrow("UNKNOWN_METHOD: chooseFolder");
    expect(isBridgeUnavailable(new Error("UNKNOWN_METHOD: chooseFolder"))).toBe(true);
    expect(isBridgeUnavailable(new Error("NO_BRIDGE"))).toBe(true);
    expect(isBridgeUnavailable(new Error("INVALID_ARGS: current must be a string"))).toBe(false);
    expect(isBridgeUnavailable("INTERNAL: x")).toBe(false);
  });

  it("startShellBridge：无桥 no-op；有桥 → 拉 getState + 订阅 zai-shell-state，stop 后不再收", async () => {
    expect(getShellState()).toBeNull();
    startShellBridge()(); // 无桥：返回 no-op stop
    const postMessage = vi.fn(async () => ({ recording: { mode: "off" }, captions: {} }));
    window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
    const stop = startShellBridge();
    expect(postMessage).toHaveBeenCalledWith({ method: "getState" });
    await Promise.resolve();
    window.dispatchEvent(new CustomEvent(SHELL_STATE_EVENT, {
      detail: { recording: { mode: "screen", engine_running: true }, captions: { on: true } },
    }));
    expect(getShellState()?.recording.mode).toBe("screen");
    expect(getShellState()?.captions.on).toBe(true);
    stop();
    window.dispatchEvent(new CustomEvent(SHELL_STATE_EVENT, { detail: { recording: { mode: "off" } } }));
    expect(getShellState()?.recording.mode).toBe("screen");
  });

  it("applyShellState 通知所有订阅者；退订后不再通知", () => {
    const a = vi.fn();
    const b = vi.fn();
    const offA = subscribeShellState(a);
    subscribeShellState(b);
    applyShellState({});
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
    offA();
    applyShellState({});
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(2);
  });
});
