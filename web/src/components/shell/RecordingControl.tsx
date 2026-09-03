// 录制控制（header 右上，只在壳里渲染——CONTRACT §61.2）：逐字镜像 mac/Sources/
// DashboardView.swift RecordingMenuButton 的标签/颜色/状态——按钮文案 `录制：` +
// 状态词（关 / 未在录制 / 仅屏幕 / 屏幕+音频），颜色 关=次级、引擎在录=红、开了
// 没录上=橙；菜单 = 状态行（引擎死了时说真实原因）+ 拒绝/回滚说明 + 三态单选 +
// 重启录制引擎 + 缺权限时的系统设置深链。
//
// 乐观 UI：点选即显示目标模式；壳 reject → 回滚并把 reject 原文挂在按钮 title；
// 壳的真相（call 回执 / zai-shell-state 推送）一到就替换乐观值——`屏幕+音频` 要先过
// ffmpeg 预检才提交，回执里 mode 可能还是旧值，所以乐观值保留到真相追平、或壳发出
// 拒绝说明（note 非空）、或 15s 兜底超时。
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../../i18n";
import { callShell, type ShellRecordingState } from "../../shellBridge";

export interface RecordingControlProps {
  state: ShellRecordingState;
}

const MODES = ["off", "screen", "screen_audio"] as const;
const OPTIMISTIC_TIMEOUT_MS = 15_000;
const RESTARTING_FLASH_MS = 3_000;

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
  const [isOpen, setOpen] = useState(false);
  const [optimisticMode, setOptimisticMode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRestarting, setRestarting] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
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

  // 点外面 / Esc 关菜单
  useEffect(() => {
    if (!isOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [isOpen]);

  const flashRestarting = () => {
    restartToken.current += 1;
    const token = restartToken.current;
    setRestarting(true);
    setTimeout(() => {
      if (restartToken.current === token) setRestarting(false);
    }, RESTARTING_FLASH_MS);
  };

  const pickMode = async (mode: string) => {
    setOpen(false);
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
    setOpen(false);
    setError(null);
    flashRestarting();
    try {
      await callShell("restartRecording");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const openSettings = () => {
    setOpen(false);
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

  return (
    <div className="shell-rec" ref={rootRef}>
      <button
        type="button"
        className={`shell-rec-button is-${tone}`}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-label={text("录制控制", "Recording controls")}
        title={error ?? (state.note || text("录制控制", "Recording controls"))}
        onClick={() => setOpen((v) => !v)}
      >
        <ModeIcon mode={shownMode} isRunning={runningForColor} />
        <span className="shell-rec-label"><span>{text("录制：", "Rec: ")}</span><span>{stateWord}</span></span>
        {isRestarting && <span className="shell-rec-restarting">{text("重启中…", "restarting…")}</span>}
      </button>
      {isOpen && (
        <div className="shell-menu" role="menu" aria-label={text("录制控制", "Recording controls")}>
          <div className="shell-menu-note">
            {isDead ? recordingDeadReason(state, text) : <><span>{text("录制：", "Recording: ")}</span><span>{stateWord}</span></>}
          </div>
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
        </div>
      )}
    </div>
  );
}
