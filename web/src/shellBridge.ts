// 壳桥客户端（CONTRACT §61.1）：看板跑在 "Zelin's AI Assistant" 壳（WKWebView，shell/）里时，
// `window.webkit.messageHandlers.zaiShell` 存在——header 的「录制」「实时字幕」两个
// 开关经它驱动壳内的原生引擎；壳在状态变化时 dispatch `zai-shell-state` window 事件
// 推回同一份快照。普通浏览器会话里没有这个 handler → hasShellBridge() 为 false，
// 开关整体不渲染。
//
// 这是 server 两文件契约之外的第二条 wire（壳 ⇄ 页面），所以类型住在这里而不是
// types.ts；同样 add-only：字段名逐字镜像壳侧 ShellBridge.stateSnapshot() 的
// snake_case 键（防腐 #10 前端镜像纪律），只加 optional、绝不改名。normalize 对
// 缺失/未知字段给默认值，壳先于页面升级时页面绝不崩。
//
// 状态存放：与 realtime.ts 同款的独立小店（useSyncExternalStore），不进 store.ts
// 的 AppState——这是壳进程的状态，不是 server 快照，两者来源与生命周期都不同。
import { useSyncExternalStore } from "react";

export const SHELL_STATE_EVENT = "zai-shell-state";
export const SHELL_HANDLER_NAME = "zaiShell";

/** 录制引擎快照（壳侧 RecordingController 的投影） */
export interface ShellRecordingState {
  available: boolean;
  on: boolean;                 // ⇔ mode !== "off"
  mode: string;                // "off" | "screen" | "screen_audio"（§15 冻结词表；未知值原样显示）
  engine_running: boolean;
  diagnosis: string | null;    // §25 failure id（engine_dead / engine_crashed / …）；健康或 off 为 null
  note: string;                // 拒绝/回滚一次切换后的 15s 说明（壳侧已本地化）
  tcc_lost: boolean;
  screen_permission: boolean;
  resume_mode: string;         // on:true 不带 mode 时壳会恢复到的模式
}

/** 实时字幕快照（壳侧 LiveCaptionsController 的投影） */
export interface ShellCaptionsState {
  available: boolean;
  on: boolean;
  engine: string;              // "auto" | "doubao" | "apple"
  paused: boolean;
  engine_dead: boolean;
  status_text: string;
  status_is_error: boolean;
}

export interface ShellState {
  recording: ShellRecordingState;
  captions: ShellCaptionsState;
  language?: string;
}

/** 请求词表（壳侧 ShellBridge.handle；add-only） */
export type ShellMethod =
  | "getState"
  | "setRecording"
  | "restartRecording"
  | "openScreenRecordingSettings"
  | "setCaptions"
  | "setLanguage";

interface ZaiShellHandler {
  postMessage(body: unknown): Promise<unknown>;
}

declare global {
  interface Window {
    webkit?: {
      messageHandlers?: {
        zaiShell?: ZaiShellHandler;
      };
    };
  }
}

export function shellHandler(): ZaiShellHandler | null {
  try {
    return window.webkit?.messageHandlers?.zaiShell ?? null;
  } catch {
    return null;
  }
}

/** 壳在场？（渲染开关的唯一判据） */
export function hasShellBridge(): boolean {
  return shellHandler() !== null;
}

const asBool = (v: unknown, fallback = false): boolean => (typeof v === "boolean" ? v : fallback);
const asString = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);

/** 壳快照 → 类型化状态；缺失字段取默认值（壳 add-only，页面永不因新/缺字段崩） */
export function normalizeShellState(raw: unknown): ShellState {
  const obj = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const rec = (obj.recording && typeof obj.recording === "object" ? obj.recording : {}) as Record<string, unknown>;
  const cap = (obj.captions && typeof obj.captions === "object" ? obj.captions : {}) as Record<string, unknown>;
  const mode = asString(rec.mode, "off");
  return {
    recording: {
      available: asBool(rec.available),
      on: asBool(rec.on, mode !== "off"),
      mode,
      engine_running: asBool(rec.engine_running),
      diagnosis: typeof rec.diagnosis === "string" ? rec.diagnosis : null,
      note: asString(rec.note),
      tcc_lost: asBool(rec.tcc_lost),
      screen_permission: asBool(rec.screen_permission, true),
      resume_mode: asString(rec.resume_mode, "screen"),
    },
    captions: {
      available: asBool(cap.available),
      on: asBool(cap.on),
      engine: asString(cap.engine, "auto"),
      paused: asBool(cap.paused),
      engine_dead: asBool(cap.engine_dead),
      status_text: asString(cap.status_text),
      status_is_error: asBool(cap.status_is_error),
    },
    ...(typeof obj.language === "string" ? { language: obj.language } : {}),
  };
}

// ----- 小店：当前快照 + 订阅 --------------------------------------------------- #

let current: ShellState | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

/** 快照落地（call 回执与 window 事件都经这里——两者都是壳说的真相） */
export function applyShellState(raw: unknown): ShellState {
  current = normalizeShellState(raw);
  emit();
  return current;
}

export function getShellState(): ShellState | null {
  return current;
}

export function subscribeShellState(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useShellState(): ShellState | null {
  return useSyncExternalStore(subscribeShellState, getShellState, getShellState);
}

/**
 * 调壳：Promise 解析为壳的最新快照（已 applyShellState）；壳 reject 的字符串
 * （`UNKNOWN_METHOD: …` / `INVALID_ARGS: …`）原样成为 Error.message，调用方回滚。
 */
export async function callShell(method: ShellMethod, args: Record<string, unknown> = {}): Promise<ShellState> {
  const handler = shellHandler();
  if (!handler) throw new Error("NO_BRIDGE");
  let reply: unknown;
  try {
    reply = await handler.postMessage({ method, ...args });
  } catch (err) {
    throw err instanceof Error ? err : new Error(String(err));
  }
  return applyShellState(reply);
}

/**
 * 开始监听壳推送 + 拉一次初始快照。返回 stop。壳不在场时什么都不做（返回 no-op）。
 * 事件里的 detail 就是快照本体（壳侧 CustomEvent(detail: state)）。
 */
export function startShellBridge(): () => void {
  if (!hasShellBridge()) return () => {};
  const onState = (event: Event) => {
    applyShellState((event as CustomEvent).detail);
  };
  window.addEventListener(SHELL_STATE_EVENT, onState);
  void callShell("getState").catch(() => {
    /* 壳在但首拉失败：等它主动推（页面加载完壳会推一次） */
  });
  return () => {
    window.removeEventListener(SHELL_STATE_EVENT, onState);
  };
}

/** 测试用：清空快照与订阅者 */
export function resetShellBridgeForTests() {
  current = null;
  listeners.clear();
}
