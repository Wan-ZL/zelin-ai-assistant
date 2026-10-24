// 壳桥客户端（CONTRACT §61.1）：看板跑在 "Zelin's AI Assistant" 壳（WKWebView，shell/）里时，
// `window.webkit.messageHandlers.zaiShell` 存在——header 的「录制」开关经它驱动壳内的原生
// 引擎；壳在状态变化时 dispatch `zai-shell-state` window 事件
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
/** 壳 → 页面的命令事件（§61.6）：detail = {command, ...args}; 词表 open_page {page, anchor?}（D40：壳菜单 关于 / 设置… /
 *  权限体检… 在页面已载入时让 SPA 自己换页，不重载）。v2.0.0：quick_capture（全局快捷键聚焦提案框）随看板退役（§61.1 tombstone） */
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
  // §61.1 追记 add-only（normalize 补默认 "" ——老壳缺席也在；类型上 optional 只为不逼既有 fixture 改字）
  self_heal_note?: string;     // consent-race 自愈后的 15s 成功句（壳侧已本地化，原生 selfHealNote）
  log_tail?: string;           // 引擎死因的日志尾（原生 diagnosis.logTail；只在 engine_crashed / engine_ffmpeg_missing 非空）
  // §61.7 add-only：录制日程（normalize 补默认 = 关 / 09:00–19:00 / 周一至周五 / 未暂停——老壳缺席也在）
  schedule?: ShellRecordingSchedule;
}

/** 录制日程（§61.7；壳侧 RecordingSchedule 的投影）：只在设定时间窗内录，默认关 = always-on */
export interface ShellRecordingSchedule {
  enabled: boolean;
  start: string;               // "HH:MM"（24 h，本地时间）
  end: string;                 // "HH:MM"；start > end = 跨午夜窗口，归开始那天
  days: number[];              // Calendar weekday：1 = 周日 … 7 = 周六（已排序去重）
  paused: boolean;             // 派生：日程开 ∧ mode != off ∧ 现在在窗外 ⇒ 引擎被壳按日程停着（不是错、不是关）
}

export const DEFAULT_RECORDING_SCHEDULE: ShellRecordingSchedule = Object.freeze({
  enabled: false, start: "09:00", end: "19:00", days: [2, 3, 4, 5, 6], paused: false,
}) as ShellRecordingSchedule;

/** 「按日程暂停中」——header 状态词 / 设置区 / 录制页共用的唯一判据（mode off 时壳恒给 false） */
export function schedulePaused(rec: Pick<ShellRecordingState, "mode" | "schedule">): boolean {
  return rec.mode !== "off" && rec.schedule?.paused === true;
}

/** TCC 三项（壳侧 PermissionsProbe；§68.3）："granted" | "denied" | "unknown" */
export type PermissionStatus = "granted" | "denied" | "unknown" | string;

export interface ShellPermissionsState {
  screen: PermissionStatus;
  microphone: PermissionStatus;
  notifications: PermissionStatus;
  vault: PermissionStatus;          // 笔记库（Documents）授权：壳的被动探针（vault_sync_mode=mirror 或 vaultAccessGranted）
  // §61.1 追记 add-only（normalize 补默认 false）：屏幕录制的系统提示已弹过一次 → 原生 screenRow 按钮
  // 「去授权」改「打开系统设置」的判据（壳 UserDefaults screenPermissionRequested）
  screen_requested?: boolean;
}

export interface ShellState {
  recording: ShellRecordingState;
  permissions: ShellPermissionsState;
  launch_at_login: boolean;
  // §28 追记 add-only（D39）：向导终章「登录时自动启动」行能不能提供 / 动手 = 壳是 /Applications（或 ~/Applications）里的
  // 正式安装 ∧ 有 bundle id；开发版 / bare binary 为 false（登录项会钉住临时路径）。三态：老壳（D39 前的 bundle）没给这个键
  // → null——「不知道」不能被补成「不是正式安装」（页面据此说「app 需要更新」而不是「开发版」）。
  // 只管向导那一下默认勾选；关于区的手动开关（LaunchAtLoginRow）不看它，与原生一致。
  launch_at_login_available?: boolean | null;
  language?: string;
}

/** 请求词表（壳侧 ShellBridge.handle；add-only）。
 *  `setRecording {on:true}` 在壳里先补屏幕录制的系统提示（缺才弹，原生 consent / 权限状态行两处「开启」同款）再 setMode——页面不用管 TCC；
 *  `restartRecording` 不带这句（原生 restart 按钮都不带；向导终章「启动引擎」的守卫在 FinaleStep 自己那边）；
 *  `refreshRecording` = 壳跑一次 5 s tick 的两步（TCC 自愈判定 + pgrep 活性），回执是起跑后的快照、活性随后以事件推回；
 *  `getState` 保持纯读（startShellBridge 连上就拉它）。
 *  v2.0.0 退役（§61.1 tombstone，art.6 v2.0 条款）：setCaptions / setCaptionPrefs / probeCaptionKey（实时字幕）与 setBadge（Dock 徽章 = 看板待办数）。 */
export type ShellMethod =
  | "getState"
  | "setRecording"
  | "refreshRecording"
  | "restartRecording"
  | "openScreenRecordingSettings"
  | "setLanguage"
  | "getPermissions"
  | "requestPermission"
  | "openPane"
  | "setLaunchAtLogin"
  | "setRecordingSchedule"
  | "chooseFolder";

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
const asStatus = (v: unknown): PermissionStatus => (typeof v === "string" && v ? v : "unknown");
/** 三态布尔：壳没给（老壳）或给的不是布尔 → null——「不知道」不能被补成一个事实 */
const asBoolOrNull = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);

function normalizeSchedule(raw: unknown): ShellRecordingSchedule {
  const d = DEFAULT_RECORDING_SCHEDULE;
  if (!raw || typeof raw !== "object") return { ...d, days: [...d.days] };
  const obj = raw as Record<string, unknown>;
  const clock = (v: unknown, fallback: string) => (typeof v === "string" && /^\d{2}:\d{2}$/.test(v) ? v : fallback);
  const days = Array.isArray(obj.days)
    ? Array.from(new Set(obj.days.filter((x): x is number => typeof x === "number" && Number.isInteger(x) && x >= 1 && x <= 7))).sort((a, b) => a - b)
    : [];
  return {
    enabled: asBool(obj.enabled),
    start: clock(obj.start, d.start),
    end: clock(obj.end, d.end),
    days: days.length ? days : [...d.days],
    paused: asBool(obj.paused),
  };
}

/** 壳快照 → 类型化状态；缺失字段取默认值（壳 add-only，页面永不因新/缺字段崩） */
export function normalizeShellState(raw: unknown): ShellState {
  const obj = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const rec = (obj.recording && typeof obj.recording === "object" ? obj.recording : {}) as Record<string, unknown>;
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
      self_heal_note: asString(rec.self_heal_note),
      log_tail: asString(rec.log_tail),
      schedule: normalizeSchedule(rec.schedule),
    },
    permissions: {
      screen: asStatus(perm.screen),
      microphone: asStatus(perm.microphone),
      notifications: asStatus(perm.notifications),
      vault: asStatus(perm.vault),
      screen_requested: asBool(perm.screen_requested),
    },
    launch_at_login: asBool(obj.launch_at_login),
    launch_at_login_available: asBoolOrNull(obj.launch_at_login_available),
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
 * 监听壳发来的命令（菜单 → open_page）。返回 stop；壳不在场 = no-op。
 * 页面侧处理在 app.tsx 接线：open_page → route.navigate（不重载）。
 * handler 的第二个参数 = detail 里 command 之外的键（add-only；老壳只发 {command}，那就是 {}）。
 * 回执：接到就 `preventDefault`——壳发的是 cancelable 事件，`dispatchEvent` 回 false = 页面接了；没人接（文档还在加载 /
 * React 树没起来 / 老 web 构建）壳据此退回整页加载深链（ShellBridge.pushCommand completion）。老壳发的不 cancelable，
 * preventDefault 是 no-op。
 */
export function onShellCommand(handler: (command: string, args: Record<string, unknown>) => void): () => void {
  if (!hasShellBridge()) return () => {};
  const listener = (event: Event) => {
    const detail = (event as CustomEvent).detail as { command?: unknown } | undefined;
    if (detail && typeof detail.command === "string") {
      const { command, ...args } = detail as { command: string } & Record<string, unknown>;
      event.preventDefault();
      handler(command, args);
    }
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

/** 测试用：清空快照与订阅者 */
export function resetShellBridgeForTests() {
  current = null;
  listeners.clear();
}
