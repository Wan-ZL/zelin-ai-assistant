// 录制区（§15 录制三态 / §61 / §68）：默认录制模式三态单选 + 引擎状态 + 重启 + 权限深链——
// 全部经 zaiShell 桥打到壳里的 RecordingController（screenpipe 是壳的直接子进程）。
// 普通浏览器会话没有桥：如实说明「只在看板 app 里可控」，不装按钮。
// 状态词 / 死因句复用 header RecordingControl 的同一张表（recordingStateWord / recordingDeadReason）。
import { useState } from "react";
import { useI18n } from "../../i18n";
import { callShell, hasShellBridge, useShellState } from "../../shellBridge";
import { recordingDeadReason, recordingModeLabel, recordingStateWord } from "../shell/RecordingControl";

const MODES = ["off", "screen", "screen_audio"] as const;

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
            {MODES.map((mode) => (
              <label key={mode} className="settings-radio">
                <input type="radio" name="recording-mode" value={mode} checked={rec.mode === mode} disabled={busy} onChange={() => void choose(mode)} />
                {recordingModeLabel(mode, text)}
              </label>
            ))}
          </div>
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
