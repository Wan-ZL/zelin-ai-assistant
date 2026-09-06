// 录制区（§15 录制三态 / §61 / §68.3 追记 / §61.7 录制日程）：默认录制模式三态单选 + 自动启动说明句 + consent-race
// 自愈的绿色 ✓ 句（`recording.self_heal_note`，原生 Settings.swift:709-721）+ 引擎状态 + 重启 + 权限深链 +
// 「仅在设定时间录制」日程块（开关 + 起止 HH:MM + 七个星期勾选，经桥 `setRecordingSchedule`，all-or-nothing）——
// 全部经 zaiShell 桥打到壳里的 RecordingController / RecordingSchedule（screenpipe 是壳的直接子进程）。
// 普通浏览器会话没有桥：如实说明「只在看板 app 里可控」，不装按钮。
// 状态词 / 死因句复用 header RecordingControl 的同一张表（recordingStateWord / recordingDeadReason）。
import { useEffect, useState } from "react";
import { useI18n } from "../../i18n";
import { callShell, hasShellBridge, schedulePaused, useShellState, type ShellRecordingSchedule } from "../../shellBridge";
import { recordingDeadReason, recordingStateWord } from "../shell/RecordingControl";
import { scheduledPauseSentence, scheduleSummary, WEEKDAYS } from "../shell/recordingSchedule";

// 原生 Settings.swift:701–703 的三档写法（与顶栏按钮 DashboardView 的「屏幕+音频」差一个空格——两处各自逐字）
const MODES: Array<[string, string, string]> = [["off", "关", "Off"], ["screen", "仅屏幕", "Screen Only"], ["screen_audio", "屏幕 + 音频", "Screen + Audio"]];

/** 严格 "HH:MM"（与壳 RecordingScheduleSpec.minutes 同一形状；<input type="time"> 给的就是它，文本回退也按它收） */
export function isClock(value: string): boolean {
  const m = /^(\d{2}):(\d{2})$/.exec(value);
  if (!m) return false;
  const h = Number(m[1]);
  const min = Number(m[2]);
  return h >= 0 && h <= 23 && min >= 0 && min <= 59;
}

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
  const paused = rec ? schedulePaused(rec) : false;
  return (
    <section className="settings-section" aria-labelledby="settings-recording-title">
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
          <p className={`settings-helper${rec.mode !== "off" && !rec.engine_running && !paused ? " is-warning" : ""}`} data-paused={paused ? "true" : undefined}>
            {text("引擎：", "Engine: ")}
            {rec.mode === "off" ? recordingStateWord(rec, rec.mode, text)
              : paused && rec.schedule ? scheduledPauseSentence(rec.schedule, text)
              : rec.engine_running ? text("正在录制", "Recording") : recordingDeadReason(rec, text)}
            {rec.note && ` · ${rec.note}`}
          </p>
          <div className="settings-actions">
            <button type="button" className="btn" disabled={busy || rec.mode === "off" || paused} onClick={() => void callShell("restartRecording").catch((e) => setError(String(e)))}>
              {text("重启录制引擎", "Restart engine")}
            </button>
            {!rec.screen_permission && (
              <button type="button" className="btn" onClick={() => void callShell("openScreenRecordingSettings").catch(() => undefined)}>
                {text("打开系统设置 → 屏幕录制", "Open System Settings → Screen Recording")}
              </button>
            )}
          </div>
          {error && <p className="settings-warning" role="alert">{error}</p>}
          <RecordingScheduleBlock schedule={rec.schedule} />
        </>
      )}
    </section>
  );
}

/**
 * 录制日程块（§61.7）：开关 + 起止时刻 + 七个星期勾选。开关与勾选即改即发；时刻在失焦 / 回车时发（半截输入不打扰壳）。
 * 每次只发改动的键（桥合并进现有日程、整份校验；坏值 = 整个请求拒绝、零写入——拒绝原文照印）。
 * 老壳（UNKNOWN_METHOD）：说明句代替控件。
 */
export function RecordingScheduleBlock({ schedule }: { schedule: ShellRecordingSchedule | undefined }) {
  const { text } = useI18n();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [unsupported, setUnsupported] = useState(false);
  const s = schedule;
  const [start, setStart] = useState(s?.start ?? "09:00");
  const [end, setEnd] = useState(s?.end ?? "19:00");
  // 回滚 = 把两个时刻输入重挂一次（key 变）：受控 <input type="time"> 在 state 拨回同一个值时 React 不一定重写 DOM
  // （jsdom 与 WebKit 都见过清空后的框一直空着），重挂最直接
  const [rollbackGen, setRollbackGen] = useState(0);
  // 壳的真相到了（别的入口改了日程 / 拒绝回滚）→ 本地草稿跟着走
  useEffect(() => { if (s) setStart(s.start); }, [s?.start]);
  useEffect(() => { if (s) setEnd(s.end); }, [s?.end]);
  if (!s) return null;

  async function send(args: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await callShell("setRecordingSchedule", args);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (/^UNKNOWN_METHOD/.test(message)) setUnsupported(true);
      else setError(message);
      // 时刻输入回滚到壳的真相
      setStart(s!.start);
      setEnd(s!.end);
      setRollbackGen((g) => g + 1);
    } finally {
      setBusy(false);
    }
  }

  /** 失焦 / 回车：与壳真相相同不发；坏值（type=time 被清空成 "" / 文本回退乱写）页面先拦并把草稿拨回真相。 */
  function commitClock(key: "start" | "end", value: string) {
    if (value === s![key]) return;
    if (!isClock(value)) {
      setError(text("时间要写成 HH:MM（例如 09:00）", "Time must be HH:MM (for example 09:00)"));
      if (key === "start") setStart(s!.start); else setEnd(s!.end);
      setRollbackGen((g) => g + 1);
      return;
    }
    void send({ [key]: value });
  }

  function toggleDay(id: number, on: boolean) {
    const next = on ? [...s!.days, id] : s!.days.filter((d) => d !== id);
    if (next.length === 0) {
      setError(text("至少留一天", "Keep at least one day"));
      return;
    }
    void send({ days: next });
  }

  const summary = scheduleSummary(s, text);
  return (
    <div className="settings-field recording-schedule" data-enabled={s.enabled ? "true" : "false"} data-paused={s.paused ? "true" : "false"}>
      <div className="settings-field-head">
        <label className="settings-knob-label" htmlFor="recording-schedule-enabled">{text("仅在设定时间录制", "Record only between…")}</label>
      </div>
      <div className="settings-knob-controls">
        <input id="recording-schedule-enabled" type="checkbox" role="switch" className="settings-switch" checked={s.enabled} disabled={busy || unsupported}
          onChange={(e) => void send({ enabled: e.target.checked })} />
        <span className="settings-helper">
          {s.enabled
            ? text(`只在 ${summary} 录制；其余时间引擎停着，到点自动恢复。`, `Recording only ${summary}; the engine stays off outside that window and resumes on its own.`)
            : text("关 = 只要录制开着就一直录（现状）。", "Off = records whenever recording is on (current behavior).")}
        </span>
      </div>
      {unsupported ? (
        <p className="settings-helper">{text("这个版本的看板 app 还不认识录制日程——升级壳后再来。", "This board app build does not know recording schedules yet — update the shell and come back.")}</p>
      ) : (
        <fieldset className="recording-schedule-window" disabled={busy || !s.enabled} aria-label={text("录制时间窗", "Recording window")}>
          <div className="settings-actions recording-schedule-times">
            <label className="settings-knob-label" htmlFor="recording-schedule-start">{text("从", "From")}</label>
            <input key={`start-${rollbackGen}`} id="recording-schedule-start" className="settings-input settings-input-short" type="time" step={60} value={start}
              onChange={(e) => setStart(e.target.value)}
              onBlur={(e) => commitClock("start", e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") commitClock("start", (e.target as HTMLInputElement).value); }} />
            <label className="settings-knob-label" htmlFor="recording-schedule-end">{text("到", "to")}</label>
            <input key={`end-${rollbackGen}`} id="recording-schedule-end" className="settings-input settings-input-short" type="time" step={60} value={end}
              onChange={(e) => setEnd(e.target.value)}
              onBlur={(e) => commitClock("end", e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") commitClock("end", (e.target as HTMLInputElement).value); }} />
            {s.start > s.end && <span className="settings-helper">{text("跨午夜：到次日", "Overnight: ends the next day")}</span>}
          </div>
          <div className="settings-radio-row recording-schedule-days" role="group" aria-label={text("星期", "Days")}>
            {WEEKDAYS.map((d) => (
              <label key={d.id} className="settings-checkbox settings-radio">
                <input type="checkbox" checked={s.days.includes(d.id)} onChange={(e) => toggleDay(d.id, e.target.checked)} />
                {text(`周${d.zh}`, d.en)}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      {error && <p className="settings-warning" role="alert">{error}</p>}
    </div>
  );
}
