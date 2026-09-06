// 录制区（§15 录制三态 / §61 / §68.3 追记）：默认录制模式三态单选 + 自动启动说明句 + consent-race 自愈的绿色 ✓ 句
// （`recording.self_heal_note`，原生 Settings.swift:709-721）+ 引擎状态 + 重启 + 权限深链——
// 全部经 zaiShell 桥打到壳里的 RecordingController（screenpipe 是壳的直接子进程）。
// 普通浏览器会话没有桥：如实说明「只在看板 app 里可控」，不装按钮。
// 状态词 / 死因句复用 header RecordingControl 的同一张表（recordingStateWord / recordingDeadReason）。
import { useState } from "react";
import { useI18n } from "../../i18n";
import { callShell, hasShellBridge, useShellState } from "../../shellBridge";
import { recordingDeadReason, recordingStateWord } from "../shell/RecordingControl";

// 原生 Settings.swift:701–703 的三档写法（与顶栏按钮 DashboardView 的「屏幕+音频」差一个空格——两处各自逐字）
const MODES: Array<[string, string, string]> = [["off", "关", "Off"], ["screen", "仅屏幕", "Screen Only"], ["screen_audio", "屏幕 + 音频", "Screen + Audio"]];

export function RecordingSection() {
  const { text } = useI18n();
  const state = useShellState();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const present = hasShellBridge();

  async function choose(mode: string) {
    setBusy(true);
    setError(null);
    try {
      if (mode === "off") await callShell("setRecording", { on: false });
      else await callShell("setRecording", { on: true, mode });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const rec = state?.recording;
  return (
    <section className="settings-section" id="settings-recording" aria-labelledby="settings-recording-title">
      <h3 id="settings-recording-title" className="settings-section-title">{text("录制", "Recording")}</h3>
      <p className="settings-helper">
        {text(
          "屏幕（可选 + 音频）持续录制到本机 ~/.screenpipe，每 30 分钟 ingest 成笔记再进雷达。敏感 app 排除词表在 config.yaml recording.ignored_apps。",
          "Continuous screen (optionally + audio) capture into local ~/.screenpipe, ingested into notes every 30 min for the radar. The sensitive-app exclusion list lives in config.yaml recording.ignored_apps.",
        )}
      </p>
      {!present || !rec ? (
        <p className="settings-warning">
          {text("录制引擎只在看板 app（壳）里可控——这是浏览器里打开的看板，看不到引擎。", "The recording engine is only controllable inside the board app (shell) — this board is open in a browser and cannot see it.")}
        </p>
      ) : (
        <>
          <div className="settings-radio-row" role="radiogroup" aria-label={text("默认录制模式", "Default recording mode")}>
            {MODES.map(([mode, zh, en]) => (
              <label key={mode} className="settings-radio">
                <input type="radio" name="recording-mode" value={mode} checked={rec.mode === mode} disabled={busy} onChange={() => void choose(mode)} />
                {text(zh, en)}
              </label>
            ))}
          </div>
          {/* 原生 Settings.swift:709-712：三档下面那句「打开 App 时自动按此模式…」（10pt 次要色） */}
          <p className="settings-helper">{text("打开 App 时自动按此模式启动 Screenpipe 持续录制。", "On app launch, Screenpipe recording starts automatically in this mode.")}</p>
          {rec.self_heal_note && (
            // 原生 Settings.swift:713-721（audit 2.2）：consent-race 自愈刚触发——checkmark.circle.fill + 绿字，壳侧 15 s 后自己清空；
            // 与下面的引擎行 / 拒绝说明各自独立（原生是两个并列的 if，不是 else-if）
            <p className="settings-helper is-ok self-heal-note" role="status"><span aria-hidden="true">✓ </span><span>{rec.self_heal_note}</span></p>
          )}
          <p className={`settings-helper${rec.mode !== "off" && !rec.engine_running ? " is-warning" : ""}`}>
            {text("引擎：", "Engine: ")}
            {rec.mode === "off" ? recordingStateWord(rec, rec.mode, text) : rec.engine_running ? text("正在录制", "Recording") : recordingDeadReason(rec, text)}
            {rec.note && ` · ${rec.note}`}
          </p>
          <div className="settings-actions">
            <button type="button" className="btn" disabled={busy || rec.mode === "off"} onClick={() => void callShell("restartRecording").catch((e) => setError(String(e)))}>
              {text("重启录制引擎", "Restart engine")}
            </button>
            {!rec.screen_permission && (
              <button type="button" className="btn" onClick={() => void callShell("openScreenRecordingSettings").catch(() => undefined)}>
                {text("打开系统设置 → 屏幕录制", "Open System Settings → Screen Recording")}
              </button>
            )}
          </div>
          {error && <p className="settings-warning" role="alert">{error}</p>}
        </>
      )}
    </section>
  );
}
