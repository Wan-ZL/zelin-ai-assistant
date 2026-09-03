// 录制与数据接入页（CONTRACT §15 / §48 / §54.4；?page=ingest，左侧导航栏第四项）：原生 Pages.swift
// IngestView 的 web 版——「Screenpipe 录制」（模式三态 + 引擎状态一句 + 重启 / 去授权，经 §61 桥；
// 浏览器里打开如实说只在看板 app 里可控）、数据源健康（§48 radar_sources 投影，与设置页来源区同一行
// 组件）+ 到各接入设置区的深链、「最近活动」（state/actd.log 更新于 …，读诊断快照的日志清单）。
// 原生的「手动触发」（立即导出 / 立即 ingest = 起 ingest/ 下的 shell 脚本）没有 server 落点，本页不装假按钮。
import { useEffect, useState } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { RelativeTime } from "../components/board/cardChrome";
import { HealthLine } from "../components/settings/sourceHealth";
import { useI18n } from "../i18n";
import { buildAppUrl } from "../route";
import { callShell, hasShellBridge, useShellState, type ShellRecordingState } from "../shellBridge";
import { refreshDiagnostics, useAppState } from "../store";

type Text = (zh: string, en: string) => string;

/** 原生 IngestView.engineStatusText：页内状态用裸词（不带「录制：」前缀，那是顶栏按钮的） */
export function engineStatusText(rec: ShellRecordingState, text: Text): string {
  if (rec.mode === "off") return text("关", "Off");
  if (!rec.engine_running) return text("未在录制", "Not recording");
  return rec.mode === "screen_audio" ? text("屏幕+音频", "Screen + audio") : text("仅屏幕", "Screen only");
}

const MODES: Array<[string, string, string]> = [["off", "关", "Off"], ["screen", "仅屏幕", "Screen only"], ["screen_audio", "屏幕+音频", "Screen + audio"]];

export function IngestPage() {
  const { text } = useI18n();
  const { board, diagnostics } = useAppState();
  const shell = useShellState();
  const present = hasShellBridge();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!diagnostics) void refreshDiagnostics();
  }, [diagnostics]);

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

  const rec = shell?.recording;
  const health = board?.radar_sources;
  const actdLog = diagnostics?.logs.find((log) => log.name === "actd.log") ?? null;
  const settingsLink = (anchor: string, label: string) => {
    const url = buildAppUrl(window.location.href, "settings", null);
    url.searchParams.set("anchor", anchor);
    return <a key={anchor} className="settings-link" href={url.toString()}>{label}</a>;
  };

  return (
    <main className="settings-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>{text("← 返回看板", "← Back to board")}</a>
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("录制与数据接入", "Recording & Data Sources")}</h2>
        <button type="button" className="btn" onClick={() => { void refreshDiagnostics(true); if (present) void callShell("getState").catch(() => undefined); }}>
          {text("刷新", "Refresh")}
        </button>
      </div>

      <section className="settings-section" aria-labelledby="ingest-recording-title">
        <h3 id="ingest-recording-title" className="settings-section-title">{text("Screenpipe 录制", "Screenpipe Recording")}</h3>
        {present && rec ? (
          <>
            <div className="settings-field is-enum">
              <div className="settings-field-head"><span className="settings-knob-label">{text("模式", "Mode")}</span></div>
              <div className="settings-radio-row" role="radiogroup" aria-label={text("模式", "Mode")}>
                {MODES.map(([mode, zh, en]) => (
                  <label key={mode} className="settings-radio">
                    <input type="radio" name="ingest-recording-mode" value={mode} checked={rec.mode === mode} disabled={busy} onChange={() => void choose(mode)} />
                    {text(zh, en)}
                  </label>
                ))}
              </div>
            </div>
            <p className={`settings-helper${rec.mode !== "off" && !rec.engine_running ? " is-warning" : ""}`}>
              {text("引擎：", "Engine: ")}{engineStatusText(rec, text)}{rec.note && ` · ${rec.note}`}
            </p>
            <div className="settings-actions">
              <button type="button" className="btn" disabled={busy || rec.mode === "off"} onClick={() => void callShell("restartRecording").catch((e) => setError(String(e)))}>
                {text("重启引擎", "Restart engine")}
              </button>
              {!rec.screen_permission && (
                <button type="button" className="btn" onClick={() => void callShell("openScreenRecordingSettings").catch(() => undefined)}>
                  {text("去授权", "Grant…")}
                </button>
              )}
            </div>
            {error && <p className="settings-warning" role="alert">{error}</p>}
          </>
        ) : (
          <p className="settings-warning">
            {text("录制引擎只在看板 app（壳）里可控——这是浏览器里打开的看板，看不到引擎。", "The recording engine is only controllable inside the board app (shell) — this board is open in a browser and cannot see it.")}
          </p>
        )}
      </section>

      <section className="settings-section" aria-labelledby="ingest-sources-title">
        <h3 id="ingest-sources-title" className="settings-section-title">{text("数据源", "Data sources")}</h3>
        {health ? (
          <ul className="settings-health" aria-label={text("来源健康", "Source health")}>
            {(["gmail", "slack", "obsidian"] as const).map((src) => <HealthLine key={src} source={src} health={health[src]} />)}
          </ul>
        ) : <p className="settings-helper">{text("无数据", "No data")}</p>}
        <div className="settings-actions">
          {settingsLink("slack", text("Slack 接入", "Slack"))}
          {settingsLink("gmail", text("Gmail 接入", "Gmail"))}
          {settingsLink("obsidian", text("笔记库", "Notes vault"))}
        </div>
      </section>

      <section className="settings-section" aria-labelledby="ingest-activity-title">
        <h3 id="ingest-activity-title" className="settings-section-title">{text("最近活动", "Recent Activity")}</h3>
        <p className="settings-helper">
          {text("state/actd.log 更新于：", "state/actd.log updated: ")}
          {actdLog ? <RelativeTime epoch={actdLog.mtime} /> : text("无日志", "No log")}
        </p>
        <a className="settings-link" href={buildAppUrl(window.location.href, "deps", null).toString()}>{text("查看日志", "View log")}</a>
      </section>
    </main>
  );
}
