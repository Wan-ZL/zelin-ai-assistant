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
/** 壳 → 页面的命令事件（§61.6）：detail = {command}; 今日词表 quick_capture（全局快捷键 ⌃⌥Space） */
export const SHELL_COMMAND_EVENT = "zai-shell-command";

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

/** 最近一次 BYO key「检测」（壳 CaptionKeyCheck；§68.2 追记）：running → done + verdict */
export interface ShellKeyProbe {
  name: string;                // "volcano-speech-key.txt" | "volcano-ark-key.txt"
  state: string;               // "running" | "done"
  verdict: string;             // done 时："ok" | "bad_key" | "resource_not_enabled" | "model_not_found" | "service_error" | "network"
  detail: string;              // bad_key / model_not_found / network 的原文
  code: string;                // resource_not_enabled / service_error 的服务端错误码
  message: string;             // 同上的服务端消息
}

/** 实时字幕快照（壳侧 LiveCaptionsController 的投影 + §68.2 偏好八键） */
export interface ShellCaptionsState {
  available: boolean;
  on: boolean;
  engine: string;              // "auto" | "doubao" | "apple"
  paused: boolean;
  engine_dead: boolean;
  status_text: string;
  status_is_error: boolean;
  source: string;              // "both" | "mic" | "system"
  translate: boolean;
  translate_direction: string; // "auto" | "zh2en" | "en2zh"
  apple_locale: string;        // "zh" | "en"
  ark_model: string;
  font_size: number;           // 14–40
  opacity: number;             // 0.2–1
  key_probe?: ShellKeyProbe | null;  // 最近一次 BYO key 检测（add-only）；老壳 / 从未检测 = null
}

/** TCC 三项（壳侧 PermissionsProbe；§68.3）："granted" | "denied" | "unknown" */
export type PermissionStatus = "granted" | "denied" | "unknown" | string;

export interface ShellPermissionsState {
  screen: PermissionStatus;
  microphone: PermissionStatus;
  notifications: PermissionStatus;
  vault: PermissionStatus;          // 笔记库（Documents）授权：壳的被动探针（vault_sync_mode=mirror 或 vaultAccessGranted）
}

export interface ShellState {
  recording: ShellRecordingState;
  captions: ShellCaptionsState;
  permissions: ShellPermissionsState;
  launch_at_login: boolean;
  hotkey: string;              // 全局快速捕获快捷键的人话（如 "⌃⌥Space"）
  language?: string;
}

/** 请求词表（壳侧 ShellBridge.handle；add-only） */
export type ShellMethod =
  | "getState"
  | "setRecording"
  | "restartRecording"
  | "openScreenRecordingSettings"
  | "setCaptions"
  | "setLanguage"
  | "getPermissions"
  | "requestPermission"
  | "openPane"
  | "setLaunchAtLogin"
  | "setCaptionPrefs"
  | "setBadge"
  | "chooseFolder"
  | "probeCaptionKey";

export const PERMISSION_KINDS = ["screen", "microphone", "notifications", "vault"] as const;
export const PANE_IDS = ["full_disk", "screen", "microphone", "notifications", "files_folders"] as const;

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
const asNumber = (v: unknown, fallback: number): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);
const asStatus = (v: unknown): PermissionStatus => (typeof v === "string" && v ? v : "unknown");

function normalizeKeyProbe(raw: unknown): ShellKeyProbe | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  const name = asString(obj.name);
  if (!name) return null;
  return {
    name,
    state: asString(obj.state, "done"),
    verdict: asString(obj.verdict),
    detail: asString(obj.detail),
    code: asString(obj.code),
    message: asString(obj.message),
  };
}

/** 壳快照 → 类型化状态；缺失字段取默认值（壳 add-only，页面永不因新/缺字段崩） */
export function normalizeShellState(raw: unknown): ShellState {
  const obj = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const rec = (obj.recording && typeof obj.recording === "object" ? obj.recording : {}) as Record<string, unknown>;
  const cap = (obj.captions && typeof obj.captions === "object" ? obj.captions : {}) as Record<string, unknown>;
  const perm = (obj.permissions && typeof obj.permissions === "object" ? obj.permissions : {}) as Record<string, unknown>;
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
      source: asString(cap.source, "both"),
      translate: asBool(cap.translate),
      translate_direction: asString(cap.translate_direction, "auto"),
      apple_locale: asString(cap.apple_locale, "zh"),
      ark_model: asString(cap.ark_model, "doubao-seed-1-6-flash"),
      font_size: asNumber(cap.font_size, 24),
      opacity: asNumber(cap.opacity, 0.7),
      key_probe: normalizeKeyProbe(cap.key_probe),
    },
    permissions: {
      screen: asStatus(perm.screen),
      microphone: asStatus(perm.microphone),
      notifications: asStatus(perm.notifications),
      vault: asStatus(perm.vault),
    },
    launch_at_login: asBool(obj.launch_at_login),
    hotkey: asString(obj.hotkey, "⌃⌥Space"),
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

/**
 * 监听壳发来的命令（全局快捷键 → quick_capture）。返回 stop；壳不在场 = no-op。
 * 页面侧处理：聚焦提案列 composer（app.tsx 接线）。
 */
export function onShellCommand(handler: (command: string) => void): () => void {
  if (!hasShellBridge()) return () => {};
  const listener = (event: Event) => {
    const detail = (event as CustomEvent).detail as { command?: unknown } | undefined;
    if (detail && typeof detail.command === "string") handler(detail.command);
  };
  window.addEventListener(SHELL_COMMAND_EVENT, listener);
  return () => window.removeEventListener(SHELL_COMMAND_EVENT, listener);
}

/** 文件对话框（§61.1 `chooseFolder`；§68.1 目录字段）：壳开 NSOpenPanel（只选目录、可新建，prompt =
 *  原生「选择」），回执 = 快照 + add-only `dialog.path`（取消 = null）。抛错的三种：`NO_BRIDGE`（浏览器）、
 *  `UNKNOWN_METHOD`（老壳）、`INVALID_ARGS`——调用方据此退化成路径文本框（FolderField）。 */
export async function chooseFolder(args: { current?: string; prompt?: string } = {}): Promise<string | null> {
  const handler = shellHandler();
  if (!handler) throw new Error("NO_BRIDGE");
  let reply: unknown;
  try {
    reply = await handler.postMessage({ method: "chooseFolder", ...args });
  } catch (err) {
    throw err instanceof Error ? err : new Error(String(err));
  }
  applyShellState(reply);
  const dialog = (reply && typeof reply === "object" ? (reply as Record<string, unknown>).dialog : null) as
    | Record<string, unknown>
    | null
    | undefined;
  const path = dialog && typeof dialog === "object" ? dialog.path : null;
  return typeof path === "string" && path ? path : null;
}

/** 桥的 reject 是否「这个壳不会 / 没有壳」——退化到浏览器路径的判据（真错误照抛） */
export function isBridgeUnavailable(err: unknown): boolean {
  const message = err instanceof Error ? err.message : String(err);
  return /^(NO_BRIDGE|UNKNOWN_METHOD)/.test(message);
}

/** Dock 徽章 = 等你动作的卡数（提案 + 需输入 + 待验收，原生 §15 v0.46 ②）；壳不在场 no-op */
export function pushBadge(count: number): void {
  if (!hasShellBridge()) return;
  void callShell("setBadge", { count: Math.max(0, Math.floor(count)) }).catch(() => {
    /* 老壳不认识 setBadge：徽章留空，不影响其它 */
  });
}

/** 测试用：清空快照与订阅者 */
export function resetShellBridgeForTests() {
  current = null;
  listeners.clear();
}
