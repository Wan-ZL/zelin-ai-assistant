// 实时字幕开关（header 右上，只在壳里渲染——CONTRACT §61.2）：逐字镜像 mac
// RecordingMenuButton 里的字幕行——开 = ✓ 实时字幕；开但引擎致命出错 = ⚠ 实时字幕
// （出错，见悬浮窗）；开但已暂停 = ⏸ 实时字幕（已暂停）；关 = 实时字幕。字幕是独立
// Bool，刻意不是第四种录制模式（§36：recordingMode 词表冻结）。
//
// 乐观 UI：点击即翻转；壳 reject → 回滚并把原文挂在 title。setCaptions 在壳侧是同步
// 翻转，回执就是真相，追平即清乐观值；15s 兜底。
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../../i18n";
import { callShell, type ShellCaptionsState } from "../../shellBridge";

export interface CaptionsControlProps {
  state: ShellCaptionsState;
}

const OPTIMISTIC_TIMEOUT_MS = 15_000;

type Text = (zh: string, en: string) => string;

/** 按钮文案（与 Swift 字幕菜单行同表） */
export function captionsLabel(s: ShellCaptionsState, on: boolean, text: Text): string {
  if (on && s.engine_dead) return text("实时字幕（出错，见悬浮窗）", "Live captions (error — see overlay)");
  if (on && s.paused) return text("实时字幕（已暂停）", "Live captions (paused)");
  return text("实时字幕", "Live captions");
}

function CaptionsIcon({ on, isDead, isPaused }: { on: boolean; isDead: boolean; isPaused: boolean }) {
  if (on && isDead) {
    // exclamationmark.triangle 同义
    return (
      <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor">
        <path d="M12 2.8 22.6 21H1.4L12 2.8Zm0 6.2a1 1 0 0 0-1 1v4a1 1 0 1 0 2 0v-4a1 1 0 0 0-1-1Zm0 8a1.2 1.2 0 1 0 0 2.4 1.2 1.2 0 0 0 0-2.4Z" />
      </svg>
    );
  }
  if (on && isPaused) {
    // pause.circle 同义
    return (
      <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true" fill="none"
        stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M10 9v6M14 9v6" />
      </svg>
    );
  }
  if (on) {
    // checkmark 同义
    return (
      <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true" fill="none"
        stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 12.5l4.2 4.2L19 7.5" />
      </svg>
    );
  }
  // 关：字幕框轮廓
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <rect x="3" y="5" width="18" height="14" rx="2.5" />
      <path d="M7 14h5M14 14h3" />
    </svg>
  );
}

export function CaptionsControl({ state }: CaptionsControlProps) {
  const { text } = useI18n();
  const [optimisticOn, setOptimisticOn] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (optimisticOn === null) return;
    if (state.on === optimisticOn) setOptimisticOn(null);
  }, [state.on, optimisticOn]);

  useEffect(() => {
    if (optimisticOn === null) {
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
      return;
    }
    timer.current = setTimeout(() => setOptimisticOn(null), OPTIMISTIC_TIMEOUT_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [optimisticOn]);

  const toggle = async () => {
    const next = !(optimisticOn ?? state.on);
    setError(null);
    setOptimisticOn(next);
    try {
      await callShell("setCaptions", { on: next });
    } catch (err) {
      setOptimisticOn(null);
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const shownOn = optimisticOn ?? state.on;
  const tone = !shownOn ? "off" : state.engine_dead ? "warn" : "on";
  const label = captionsLabel(state, shownOn, text);
  const title = error ?? (state.status_text || label);

  return (
    <button
      type="button"
      className={`shell-cap-button is-${tone}`}
      aria-pressed={shownOn}
      aria-label={text("实时字幕", "Live captions")}
      title={title}
      onClick={() => void toggle()}
    >
      <CaptionsIcon on={shownOn} isDead={state.engine_dead} isPaused={state.paused} />
      <span className="shell-cap-label">{label}</span>
    </button>
  );
}
