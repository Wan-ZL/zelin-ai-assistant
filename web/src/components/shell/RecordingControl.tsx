// 录制控制（header 右上，只在壳里渲染——CONTRACT §61.2）：逐字镜像 mac/Sources/
// DashboardView.swift RecordingMenuButton 的标签/颜色/状态——按钮文案 `录制：` +
// 状态词（关 / 未在录制 / 仅屏幕 / 屏幕+音频），颜色 关=次级、引擎在录=红、开了
// 没录上=橙；菜单 = 状态行（引擎死了时说真实原因）+ 拒绝/回滚说明 + 三态单选 +
// 重启录制引擎 + 缺权限时的系统设置深链 + 缺 ffmpeg 时的「安装 ffmpeg…」（诊断 = engine_ffmpeg_missing
// 或拒绝说明点名 ffmpeg；原生 FailureCatalog.perform 开 ffmpeg 下载页，web 同一外链）。
//
// 乐观 UI：点选即显示目标模式；壳 reject → 回滚并把 reject 原文挂在按钮 title；
// 壳的真相（call 回执 / zai-shell-state 推送）一到就替换乐观值——`屏幕+音频` 要先过
// ffmpeg 预检才提交，回执里 mode 可能还是旧值，所以乐观值保留到真相追平、或壳发出
// 拒绝说明（note 非空）、或 15s 兜底超时。
//
// 顶栏 tight 档（§49 追记 2026-09-04）：按钮只留图标（文字由 shell.css 按 data-density 收起），
// 「录制：」+ 状态词改挂 title——颜色三态照旧在图标上。
//
// 键盘（§68.3 追记，parity 批 `recording-consent-header-ui`；原生 SwiftUI Menu → NSMenu，DashboardView.swift:27-110）：
// 打开即把焦点放到勾着的那一档（menuitemradio）；↑ / ↓ 在可用项间循环、Home / End 到两端、Enter / Space 激活是
// button 原生语义；Esc 关菜单并把焦点还给触发按钮，点选一项也还；Tab 关菜单让焦点自然走。roving-focus 手法同
// chrome/TaskPropertyPicker。菜单首行下另有 consent-race 自愈的成功句（`recording.self_heal_note`，绿），排在拒绝说明之前——
// 与其余说明行一样是无 role 的 div（role=menu 的子元素只准 menuitem* / group / separator，不能挂 role=status）。
import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useI18n } from "../../i18n";
import { callShell, type ShellRecordingState } from "../../shellBridge";
import { useHeaderDensity } from "./headerDensity";

export interface RecordingControlProps {
  state: ShellRecordingState;
}

const MODES = ["off", "screen", "screen_audio"] as const;
const OPTIMISTIC_TIMEOUT_MS = 15_000;
const RESTARTING_FLASH_MS = 3_000;
/** 原生 FailureCatalog.perform("engine_ffmpeg_missing") 打开的下载页（failureAction.tsx EXTERNAL 同址） */
export const FFMPEG_INSTALL_URL = "https://ffmpeg.org/download.html";

/** 原生 RecordingMenuButton：诊断是缺 ffmpeg、或 15 s 的拒绝说明点名了 ffmpeg（两种语言都含这个词）→ 给修法 */
export function offersFfmpegInstall(s: ShellRecordingState): boolean {
  return s.diagnosis === "engine_ffmpeg_missing" || (s.note ?? "").includes("ffmpeg");
}

type Text = (zh: string, en: string) => string;

/** 契约4 录制词：header 按钮的状态词（与 Swift stateWord 同表） */
export function recordingStateWord(s: ShellRecordingState, mode: string, text: Text): string {
  if (mode === "off") return text("关", "Off");
  if (!s.engine_running) return text("未在录制", "Not recording");
  return mode === "screen_audio" ? text("屏幕+音频", "Screen + audio") : text("仅屏幕", "Screen only");
}

/** 三态单选的标签（与 Swift modes 表同） */
export function recordingModeLabel(mode: string, text: Text): string {
  switch (mode) {
    case "off":
      return text("关", "Off");
    case "screen":
      return text("仅屏幕", "Screen only");
    case "screen_audio":
      return text("屏幕+音频", "Screen + audio");
    default:
      return mode;
  }
}

/** 引擎死了时菜单首行说的真实原因（与 Swift deadReason 同表：权限优先，再看 §25 id） */
export function recordingDeadReason(s: ShellRecordingState, text: Text): string {
  if (!s.screen_permission) {
    return text("未在录制 — 缺「屏幕录制」权限", "Not recording — missing Screen Recording permission");
  }
  switch (s.diagnosis) {
    case "engine_ffmpeg_missing":
      return text("未在录制 — 缺 ffmpeg（「屏幕+音频」需要）", "Not recording — ffmpeg is missing (Screen + Audio needs it)");
    case "node_missing":
      return text("未在录制 — 缺 Node.js", "Not recording — Node.js is missing");
    case "engine_npm_download":
      return text("引擎首次下载中…", "Engine downloading (first run)…");
    case "engine_crashed":
      return text("未在录制 — 引擎意外停了（详见录制页）", "Not recording — the engine stopped unexpectedly (see Recording page)");
    default:
      return text("未在录制", "Not recording");
  }
}

function ModeIcon({ mode, isRunning }: { mode: string; isRunning: boolean }) {
  if (mode === "screen_audio") {
    // waveform.circle.fill 同义
    return (
      <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true" fill="none"
        stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <circle cx="12" cy="12" r="10" fill="currentColor" stroke="none" opacity={isRunning ? 1 : 0.35} />
        <path d="M7 12v1M10 8v8M13 10v4M16 7v10" stroke="var(--surface)" />
      </svg>
    );
  }
  // record.circle（关=空心）/ record.circle.fill（开=实心）同义
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
      {mode !== "off" && <circle cx="12" cy="12" r="5" fill="currentColor" />}
    </svg>
  );
}

export function RecordingControl({ state }: RecordingControlProps) {
  const { text } = useI18n();
  const density = useHeaderDensity();
  const [isOpen, setOpen] = useState(false);
  const [optimisticMode, setOptimisticMode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRestarting, setRestarting] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const restartToken = useRef(0);
  const optimisticTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 点选时刻的 note：只有「新出现」的拒绝/回滚说明才退场乐观值，上一次拒绝残留的
  // 15s 说明不能把这次点选打回去
  const noteAtClick = useRef("");

  // 真相追平乐观值、或壳发出新的拒绝/回滚说明 → 乐观值退场
  useEffect(() => {
    if (optimisticMode === null) return;
    if (state.mode === optimisticMode || (state.note && state.note !== noteAtClick.current)) {
      setOptimisticMode(null);
    }
  }, [state.mode, state.note, optimisticMode]);

  useEffect(() => {
    if (optimisticMode === null) {
      if (optimisticTimer.current) clearTimeout(optimisticTimer.current);
      optimisticTimer.current = null;
      return;
    }
    optimisticTimer.current = setTimeout(() => setOptimisticMode(null), OPTIMISTIC_TIMEOUT_MS);
    return () => {
      if (optimisticTimer.current) clearTimeout(optimisticTimer.current);
    };
  }, [optimisticMode]);

  /** 菜单里当前可用的项（禁用的「重启录制引擎」跳过），DOM 顺序 = 视觉顺序 */
  const menuItems = (): HTMLElement[] =>
    Array.from(menuRef.current?.querySelectorAll<HTMLElement>("[role='menuitem'], [role='menuitemradio']") ?? [])
      .filter((el) => !(el as HTMLButtonElement).disabled);

  /** 关菜单；`restoreFocus` = 焦点还给触发按钮（Esc / 点选——原生 NSMenu 收起后焦点回到按钮） */
  const close = (restoreFocus: boolean) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  };

  // 打开即把焦点放到勾着的那一档（没有勾着的 → 第一项）
  useEffect(() => {
    if (!isOpen) return;
    const items = menuItems();
    (items.find((el) => el.getAttribute("aria-checked") === "true") ?? items[0])?.focus();
  }, [isOpen]);

  // 点外面 / Esc 关菜单（Esc 还焦点）
  useEffect(() => {
    if (!isOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(true);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [isOpen]);

  // ↑ / ↓ 循环、Home / End 两端；Tab 关菜单让焦点自然走（不 preventDefault）；Enter / Space 是 button 原生激活
  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Tab") {
      setOpen(false);
      return;
    }
    const items = menuItems();
    if (items.length === 0) return;
    const current = items.indexOf(document.activeElement as HTMLElement);
    let next: number;
    switch (event.key) {
      case "ArrowDown": next = current < 0 ? 0 : (current + 1) % items.length; break;
      case "ArrowUp": next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length; break;
      case "Home": next = 0; break;
      case "End": next = items.length - 1; break;
      default: return;
    }
    event.preventDefault();
    items[next]?.focus();
  };

  const flashRestarting = () => {
    restartToken.current += 1;
    const token = restartToken.current;
    setRestarting(true);
    setTimeout(() => {
      if (restartToken.current === token) setRestarting(false);
    }, RESTARTING_FLASH_MS);
  };

  const pickMode = async (mode: string) => {
    close(true);
    setError(null);
    if (mode === state.mode && (mode === "off" || state.engine_running)) return;
    noteAtClick.current = state.note;
    setOptimisticMode(mode);
    if (mode !== "off") flashRestarting(); // "off" 只是停，没有引擎要等
    try {
      await callShell("setRecording", mode === "off" ? { on: false } : { on: true, mode });
    } catch (err) {
      setOptimisticMode(null);
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const restart = async () => {
    close(true);
    setError(null);
    flashRestarting();
    try {
      await callShell("restartRecording");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const openSettings = () => {
    close(true);
    void callShell("openScreenRecordingSettings").catch(() => {});
  };

  const shownMode = optimisticMode ?? state.mode;
  // 乐观切到某个非 off 模式时按「引擎在起」渲染（橙），真相到了再由 engine_running 决定
  const runningForColor = optimisticMode !== null ? false : state.engine_running;
  const stateWord = recordingStateWord(
    { ...state, engine_running: optimisticMode !== null ? true : state.engine_running },
    shownMode,
    text,
  );
  const tone = shownMode === "off" ? "off" : runningForColor ? "live" : "warn";
  const isDead = state.mode !== "off" && !state.engine_running && optimisticMode === null;
  const note = error ?? (state.note || "");
  // tight：文字收起，tooltip 得把「录制：状态词」说全（有拒绝说明时接在后面）
  const title = density === "tight"
    ? [text("录制：", "Rec: ") + stateWord, note].filter(Boolean).join(" — ")
    : note || text("录制控制", "Recording controls");

  return (
    <div className="shell-rec" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`shell-rec-button is-${tone}`}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-label={text("录制控制", "Recording controls")}
        title={title}
        onClick={() => setOpen((v) => !v)}
      >
        <ModeIcon mode={shownMode} isRunning={runningForColor} />
        <span className="shell-rec-label"><span>{text("录制：", "Rec: ")}</span><span>{stateWord}</span></span>
        {isRestarting && <span className="shell-rec-restarting">{text("重启中…", "restarting…")}</span>}
      </button>
      {isOpen && (
        <div className="shell-menu" role="menu" aria-label={text("录制控制", "Recording controls")} ref={menuRef} onKeyDown={onMenuKeyDown}>
          <div className="shell-menu-note">
            {isDead ? recordingDeadReason(state, text) : <><span>{text("录制：", "Recording: ")}</span><span>{stateWord}</span></>}
          </div>
          {/* 不挂 role=status：role=menu 只准 own menuitem* / group / separator（axe aria-required-children），与旁边说明行一样是无 role 的 div */}
          {state.self_heal_note && <div className="shell-menu-note is-ok">{state.self_heal_note}</div>}
          {state.note && <div className="shell-menu-note is-warn">{state.note}</div>}
          {error && <div className="shell-menu-note is-warn">{error}</div>}
          <div className="shell-menu-divider" role="separator" />
          {MODES.map((m) => (
            <button
              key={m}
              type="button"
              role="menuitemradio"
              aria-checked={shownMode === m}
              className="shell-menu-item"
              onClick={() => void pickMode(m)}
            >
              <span className="shell-menu-check" aria-hidden="true">{shownMode === m ? "✓" : ""}</span>
              {recordingModeLabel(m, text)}
            </button>
          ))}
          <div className="shell-menu-divider" role="separator" />
          <button
            type="button"
            role="menuitem"
            className="shell-menu-item"
            disabled={state.mode === "off"}
            onClick={() => void restart()}
          >
            <span className="shell-menu-check" aria-hidden="true" />
            {text("重启录制引擎", "Restart recording engine")}
          </button>
          {!state.screen_permission && (
            <>
              <div className="shell-menu-divider" role="separator" />
              <button type="button" role="menuitem" className="shell-menu-item" onClick={openSettings}>
                <span className="shell-menu-check" aria-hidden="true" />
                {text("打开系统设置 → 屏幕录制", "Open System Settings → Screen Recording")}
              </button>
            </>
          )}
          {offersFfmpegInstall(state) && (
            <>
              <div className="shell-menu-divider" role="separator" />
              <a role="menuitem" className="shell-menu-item" href={FFMPEG_INSTALL_URL} target="_blank" rel="noreferrer" onClick={() => close(true)}>
                <span className="shell-menu-check" aria-hidden="true" />
                {text("安装 ffmpeg…", "Install ffmpeg…")}
              </a>
            </>
          )}
        </div>
      )}
    </div>
  );
}
