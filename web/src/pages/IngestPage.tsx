// 录制与数据接入页（CONTRACT §15.2 / §48 / §54.4；?page=ingest，左侧导航栏第四项）：原生 Pages.swift
// IngestView 的 web 版——「Screenpipe 录制」（模式三态 + 引擎状态一句 + screenpipe db「最近写入 HH:mm」+ 刷新 + 重启 /
// 去授权，经 §61 桥；TCC 被收回的横幅、还没授权的原因句、引擎死因行 EngineDiagnosisRow（§25 句子来自 GET /api/failures，
// ffmpeg 缺失 = 安装 ffmpeg + 装好了，重启引擎；崩了 = 查看引擎日志）；浏览器里打开如实说只在看板 app 里可控）、
// 「手动触发」（立即导出 / 立即 ingest = POST /api/ingest/{export,run}：server 在后台线程起 ingest/ 下同一条脚本、同一套
// 退出码，页面拿 job id 每 2 s 轮询 GET /api/ingest/jobs/{id}——运行中… / 完成 ✓（2.5 s 后淡出）/ 已有 ingest 在运行，本次跳过 /
// 失败 (exit N) + 尾巴 + 查看日志）、数据源健康（§48
// radar_sources 投影）+ 到各接入设置区的深链、「最近活动」（vault「1 - unprocessed」最新文件 / state/actd.log 更新于，
// 时间戳 = GET /api/diagnostics 的 add-only `activity`；server 永不读 ~/Documents，读不到时如实说）。
import { useEffect, useRef, useState } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { fetchIngestJob, postIngestExport, postIngestRun } from "../api";
import { FailureActionButton } from "../components/settings/failureAction";
import { HealthLine } from "../components/settings/sourceHealth";
import { errorMessage } from "../components/settings/useToast";
import { useI18n } from "../i18n";
import { buildAppUrl, buildSettingsUrl, DEPS_ANCHOR } from "../route";
import { callShell, hasShellBridge, useShellState, type ShellRecordingState } from "../shellBridge";
import { refreshDiagnostics, refreshFailures, useAppState } from "../store";
import type { IngestJob, IngestJobStart } from "../types";

type Text = (zh: string, en: string) => string;

/** 原生 IngestView.engineStatusText：页内状态用裸词（不带「录制：」前缀，那是顶栏按钮的） */
export function engineStatusText(rec: ShellRecordingState, text: Text): string {
  if (rec.mode === "off") return text("关", "Off");
  if (!rec.engine_running) return text("未在录制", "Not recording");
  return rec.mode === "screen_audio" ? text("屏幕+音频", "Screen + audio") : text("仅屏幕", "Screen only");
}

const pad = (n: number) => String(n).padStart(2, "0");

/** 原生 IngestModel.fmt / hm：本地时间 "yyyy-MM-dd HH:mm" / "HH:mm" */
export function stamp(epoch: number, withDate: boolean): string {
  const d = new Date(epoch * 1000);
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return withDate ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}` : hm;
}

const MODES: Array<[string, string, string]> = [["off", "关", "Off"], ["screen", "仅屏幕", "Screen only"], ["screen_audio", "屏幕+音频", "Screen + audio"]];

type RunState = { kind: "idle" } | { kind: "running" } | { kind: "done" } | { kind: "skipped" } | { kind: "failed"; rc: number; tail: string } | { kind: "error"; message: string };

export const JOB_POLL_MS = 2000;

/** 原生 runExport / runIngest 的判词：0 完成 ✓ · ingest 的 3 跳过 · 其它 失败 (exit N) + 尾巴 */
export function verdictOf(job: IngestJob, skipRc: number | null): RunState {
  if (job.ok) return { kind: "done" };
  if (job.rc === skipRc) return { kind: "skipped" };
  return { kind: "failed", rc: job.rc ?? -1, tail: job.tail ?? "" };
}

/** 一颗手动触发按钮 + 它的状态句：POST 拿 job → 轮询到 done */
function TriggerRow({ label, run, skipRc, logHref }: { label: string; run: () => Promise<IngestJobStart>; skipRc: number | null; logHref: string }) {
  const { text } = useI18n();
  const [state, setState] = useState<RunState>({ kind: "idle" });
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  function settle(next: RunState) {
    setState(next);
    // 原生 item 6：成功 / 跳过的反馈 2.5 s 后淡出，失败句留着让尾巴可读
    if (next.kind === "done" || next.kind === "skipped") timer.current = window.setTimeout(() => setState({ kind: "idle" }), 2500);
  }

  async function poll(id: string) {
    try {
      const job = await fetchIngestJob(id);
      if (job.state === "done") settle(verdictOf(job, skipRc));
      else timer.current = window.setTimeout(() => void poll(id), JOB_POLL_MS);
    } catch (err) {
      setState({ kind: "error", message: errorMessage(err) });
    }
  }

  async function go() {
    if (timer.current) window.clearTimeout(timer.current);
    setState({ kind: "running" });
    try {
      const started = await run();
      await poll(started.job);
    } catch (err) {
      setState({ kind: "error", message: errorMessage(err) });
    }
  }

  return (
    <div className="settings-actions ingest-trigger">
      <button type="button" className="btn" disabled={state.kind === "running"} onClick={() => void go()}>{label}</button>
      {state.kind === "running" && <span className="settings-helper">{text("运行中…", "Running…")}</span>}
      {state.kind === "done" && <span className="settings-helper is-ok">{text("完成 ✓", "Done ✓")}</span>}
      {state.kind === "skipped" && <span className="settings-helper">{text("已有 ingest 在运行，本次跳过", "Already running — skipped")}</span>}
      {state.kind === "failed" && (
        <>
          <span className="settings-warning" role="alert" title={state.tail}><span>{text(`失败 (exit ${state.rc}) `, `Failed (exit ${state.rc}) `)}</span><span>{state.tail.slice(-120)}</span></span>
          <a className="settings-link" href={logHref}>{text("查看日志", "View log")}</a>
        </>
      )}
      {state.kind === "error" && <span className="settings-warning" role="alert">{state.message}</span>}
    </div>
  );
}

/** 原生 EngineDiagnosisRow：一句死因 + 一颗动作（ffmpeg 缺失另给「装好了，重启引擎」；崩了给「查看引擎日志」） */
function EngineDiagnosisRow({ failureId, message, onRestart }: { failureId: string; message: string; onRestart: () => void }) {
  const { text } = useI18n();
  const url = buildSettingsUrl(window.location.href, DEPS_ANCHOR); // 依赖检查区（D30）的日志尾巴
  url.searchParams.set("log", "engine.log");
  const downloading = failureId === "engine_npm_download";
  return (
    <div className={`settings-actions engine-diagnosis${downloading ? "" : " is-warning"}`} data-failure={failureId}>
      <span className={downloading ? "settings-helper" : "settings-warning"}>{message}</span>
      {failureId === "engine_ffmpeg_missing" && (
        <>
          <FailureActionButton failureId={failureId} compact />
          <button type="button" className="btn btn-quiet" onClick={onRestart}>{text("装好了，重启引擎", "Installed — restart engine")}</button>
        </>
      )}
      {(failureId === "engine_crashed" || failureId === "engine_dead") && <a className="btn btn-quiet" href={url.toString()}>{text("查看引擎日志", "View engine log")}</a>}
      {failureId === "engine_npm_download" && <FailureActionButton failureId={failureId} compact />}
    </div>
  );
}

export function IngestPage() {
  const { text, language } = useI18n();
  const { board, diagnostics, failures } = useAppState();
  const shell = useShellState();
  const present = hasShellBridge();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!diagnostics) void refreshDiagnostics();
    if (!failures) void refreshFailures();
  }, [diagnostics, failures]);

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

  const restart = () => void callShell("restartRecording").catch((e) => setError(String(e)));
  const rec = shell?.recording;
  const health = board?.radar_sources;
  const activity = diagnostics?.activity ?? null;
  const dbStamp = activity?.screenpipe_db.mtime ?? null;
  const actdStamp = activity ? activity.actd_log.mtime : (diagnostics?.logs.find((log) => log.name === "actd.log")?.mtime ?? null);
  const failureText = (id: string) => failures?.failures[id]?.[language === "zh" ? "zh" : "en"] ?? id;
  const depsHref = buildSettingsUrl(window.location.href, DEPS_ANCHOR).toString();
  const settingsLink = (anchor: string, label: string) => {
    const url = buildAppUrl(window.location.href, "settings", null);
    url.searchParams.set("anchor", anchor);
    return <a key={anchor} className="settings-link" href={url.toString()}>{label}</a>;
  };
  const refresh = () => { void refreshDiagnostics(true); if (present) void callShell("getState").catch(() => undefined); };

  return (
    <main className="settings-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>{text("← 返回看板", "← Back to board")}</a>
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("录制与数据接入", "Recording & Data Sources")}</h2>
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
            <div className="settings-actions engine-status">
              <span className={`status-dot status-dot-${rec.engine_running ? "success" : rec.mode !== "off" ? "warning" : "quiet"}`} aria-hidden="true" />
              <span className={`settings-helper${rec.mode !== "off" && !rec.engine_running ? " is-warning" : ""}`}>{engineStatusText(rec, text)}</span>
              <span className="settings-helper ingest-db-stamp">
                {dbStamp !== null ? <><span>{text("最近写入 ", "Last write ")}</span><span>{stamp(dbStamp, false)}</span></> : text("无数据", "No data")}
              </span>
              <button type="button" className="btn btn-quiet" onClick={refresh}>{text("刷新", "Refresh")}</button>
              <button type="button" className="btn btn-quiet" disabled={busy || rec.mode === "off"} onClick={restart}>{text("重启引擎", "Restart engine")}</button>
            </div>
            {rec.note && <p className="settings-helper is-warning">{rec.note}</p>}
            {rec.mode !== "off" && rec.tcc_lost ? (
              <div className="settings-warning-list tcc-lost" role="alert">
                <p className="settings-warning">{failureText("screen_tcc_lost")}</p>
                <p className="settings-helper">{text("macOS 按应用签名识别授权；系统更新或重装应用会改变签名，旧授权就静默失效——不是你操作错了。重新授权后录制会自动恢复。", "macOS ties this permission to the app's signature; an OS update or reinstall changes it, so the old grant silently stops working — nothing you did wrong. Recording resumes automatically once re-granted.")}</p>
                <FailureActionButton failureId="screen_tcc_lost" />
              </div>
            ) : !rec.engine_running && rec.mode !== "off" && !rec.screen_permission ? (
              <div className="settings-actions">
                <span className="settings-warning">{text("原因：macOS 还没把「屏幕录制」权限授给本 App（授权一次即可，之后开 App 自动录制）。", "Cause: macOS hasn't granted this app Screen Recording yet (grant once; recording then auto-starts with the app).")}</span>
                <button type="button" className="btn" onClick={() => void callShell("openScreenRecordingSettings").catch(() => undefined)}>{text("去授权", "Grant…")}</button>
              </div>
            ) : rec.mode !== "off" && rec.diagnosis ? (
              <EngineDiagnosisRow failureId={rec.diagnosis} message={failureText(rec.diagnosis)} onRestart={restart} />
            ) : null}
            {error && <p className="settings-warning" role="alert">{error}</p>}
            <p className="settings-helper">{text("顶栏右上角的录制按钮可随时切换。", "The recording button at the top-right of the board can switch modes anytime.")}</p>
          </>
        ) : (
          <p className="settings-warning">
            {text("录制引擎只在看板 app（壳）里可控——这是浏览器里打开的看板，看不到引擎。", "The recording engine is only controllable inside the board app (shell) — this board is open in a browser and cannot see it.")}
          </p>
        )}
      </section>

      <section className="settings-section" aria-labelledby="ingest-triggers-title">
        <h3 id="ingest-triggers-title" className="settings-section-title">{text("手动触发", "Manual Triggers")}</h3>
        <TriggerRow label={text("立即导出", "Export Now")} run={postIngestExport} skipRc={null} logHref={depsHref} />
        <TriggerRow label={text("立即 ingest", "Ingest Now")} run={postIngestRun} skipRc={3} logHref={depsHref} />
        <p className="settings-helper">{text("与定时任务跑的是同一条脚本（ingest/screenpipe-export.sh · ingest/process-screenpipe.sh）；ingest 含一次 headless claude，可能要几分钟。", "The same scripts the scheduled jobs run (ingest/screenpipe-export.sh · ingest/process-screenpipe.sh); ingest includes one headless claude call and can take minutes.")}</p>
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
          <span>{text("vault「1 - unprocessed」最新文件：", "Newest file in vault \"1 - unprocessed\": ")}</span>
          {activity?.unprocessed.mtime != null
            ? <code>{stamp(activity.unprocessed.mtime, true)}</code>
            : activity && !activity.unprocessed.readable
              ? <span title={activity.unprocessed.path}>{text("看板服务读不到这个目录（Documents 授权只给壳与定时任务的助手）", "The board server cannot read that folder (the Documents grant belongs to the shell and the scheduled helper)")}</span>
              : <span>{text("无文件", "No files")}</span>}
        </p>
        <p className="settings-helper">
          <span>{text("state/actd.log 更新于：", "state/actd.log updated: ")}</span>
          {actdStamp !== null ? <code>{stamp(actdStamp, true)}</code> : <span>{text("无日志", "No log")}</span>}
        </p>
        <div className="settings-actions">
          <button type="button" className="btn btn-quiet" onClick={refresh}>{text("刷新", "Refresh")}</button>
          <a className="settings-link" href={depsHref}>{text("查看日志", "View log")}</a>
        </div>
      </section>
    </main>
  );
}
