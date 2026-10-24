// 向导末步「最后检查」（原生 SetupWizard.swift finaleStep 健康检查的 web 版，§68.5）：
//   屏幕录制权限（壳探针）· AI 引擎（GET /api/setup/engine）· 定时任务磁盘权限（doctor `cron disk access` 行，经
//   GET /api/permissions）· 录制引擎（壳录制状态，只在录制开着时出行）。每个红行带一颗修复按钮：去授权… / 去配置… /
//   去授权…（cron：原生 CronFDA.beginGrant = 复制 /usr/sbin/cron + 开面板，failureAction.grantCronFda 唯一实现；浏览器里
//   复制 + 权限体检页深链）· 查看诊断 / 启动引擎。
// 「—」= 中性（没选录制 / 定时任务还没跑过），不算红；全绿才出「🎉 一切就绪!」。文案逐字镜像 SetupWizard.swift:1014–1242。
// v2.0.0 退役（全文见 tag full）：后台服务（actd 心跳）行与它的「启动后台服务」、首次数据（dashboard.json）行与「立即生成一次」、
// 「导入 Claude Code 已有工作…」入口。
import { useState, type ReactNode } from "react";
import { useI18n } from "../../i18n";
import { buildAppUrl, buildSettingsUrl, DEPS_ANCHOR } from "../../route";
import { callShell, hasShellBridge, useShellState, schedulePaused } from "../../shellBridge";
import { useAppState } from "../../store";
import type { DoctorRow, SetupEngine } from "../../types";
import { grantCronFda } from "../settings/failureAction";
import { errorMessage } from "../settings/useToast";
import { authLabel } from "./EngineStep";

type Text = (zh: string, en: string) => string;
type HealthState = "checking" | "ok" | "fail" | "neutral";

interface RowSpec {
  key: string;
  state: HealthState;
  name: string;
  detail: ReactNode;
  /** href = 深链 <a>（可带 onClick 顺手做事，不拦导航）；否则 <button> */
  fix?: { label: string; onClick?: () => void; href?: string };
}

/** 定时任务磁盘权限：doctor `cron disk access` 行 → ok / fail(blocked | stale) / neutral(无探针) */
export function cronVerdict(rows: DoctorRow[] | undefined): "ok" | "blocked" | "stale" | "neutral" | "checking" {
  if (rows === undefined) return "checking";
  const row = rows.find((r) => r.name === "cron disk access");
  if (!row) return "neutral";
  if (row.status === "ok") return "ok";   // §25 小写词表
  if (row.failure_id === "cron_fda_blocked") return "blocked";
  if (row.failure_id === "cron_missing") return "stale";
  return "neutral";
}

function Row({ spec }: { spec: RowSpec }) {
  const mark = spec.state === "checking" ? <span className="shell-spinner" aria-label="…" /> : spec.state === "ok" ? "✅" : spec.state === "fail" ? "❌" : "—";
  return (
    <li className={`setup-health-row is-${spec.state}`} data-row={spec.key}>
      <span className="setup-health-mark">{mark}</span>
      <span>
        <div className="setup-health-name">{spec.name}</div>
        <div className="setup-health-detail">{spec.detail}</div>
      </span>
      <span>
        {spec.fix && (spec.fix.href
          ? <a className="btn" href={spec.fix.href} onClick={spec.fix.onClick}>{spec.fix.label}</a>
          : <button type="button" className="btn" onClick={spec.fix.onClick}>{spec.fix.label}</button>)}
      </span>
    </li>
  );
}

export interface FinaleStepProps {
  engine: SetupEngine | null;
  engineChecking: boolean;
  goEngine: () => void;
}

export function FinaleStep({ engine, engineChecking, goEngine }: FinaleStepProps) {
  const { text } = useI18n();
  const { permissions } = useAppState();
  const shell = useShellState();
  const present = hasShellBridge();
  // 原生 fixNote = 前缀 + 原因；按行各存一条（互不覆盖）
  const [notes, setNotes] = useState<Record<string, { prefix: string; detail: string }>>({});
  const setFixNote = (key: string, note: { prefix: string; detail: string } | null) =>
    setNotes((prev) => { const next = { ...prev }; if (note) next[key] = note; else delete next[key]; return next; });

  const rec = present ? shell?.recording ?? null : null;
  const recOn = Boolean(rec && rec.mode !== "off");
  const screen = present ? shell?.permissions.screen ?? "unknown" : "unknown";
  const cron = cronVerdict(permissions?.doctor);

  const shellCall = (method: "requestPermission" | "openPane" | "restartRecording", args: Record<string, unknown> = {}) => {
    setFixNote("shell", null);
    void callShell(method, args).catch((err) => setFixNote("shell", { prefix: "", detail: errorMessage(err) }));
  };

  const permissionsHref = buildAppUrl(window.location.href, "permissions").toString();
  const depsHref = buildSettingsUrl(window.location.href, DEPS_ANCHOR).toString(); // 依赖检查 = 设置页一区（D30）

  const rows: RowSpec[] = [];

  // 屏幕录制权限
  if (!recOn) {
    rows.push({ key: "screen", state: "neutral", name: text("屏幕录制权限", "Screen Recording permission"),
      detail: text("已选择暂不录制——之后可回上一步或在 设置 → 录制 打开", "You chose not to record — enable later in the previous step or Settings → Recording") });
  } else if (screen === "granted") {
    rows.push({ key: "screen", state: "ok", name: text("屏幕录制权限", "Screen Recording permission"), detail: text("已授权", "Granted") });
  } else {
    rows.push({ key: "screen", state: "fail", name: text("屏幕录制权限", "Screen Recording permission"),
      detail: text("未授权——没有它录不到任何内容", "Not granted — nothing can be captured without it"),
      fix: { label: text("去授权", "Grant…"), onClick: () => shellCall("requestPermission", { kind: "screen" }) } });
  }

  // AI 引擎
  rows.push(engineChecking || !engine
    ? { key: "engine", state: "checking", name: text("AI 引擎", "AI engine"), detail: text("检测中…", "Detecting…") }
    : engine.ready
      ? { key: "engine", state: "ok", name: text("AI 引擎", "AI engine"), detail: authLabel(engine.auth, text) }
      : { key: "engine", state: "fail", name: text("AI 引擎", "AI engine"), detail: text("没有 AI 引擎,会议纪要与笔记整理都不会运行", "Without an AI engine, neither meeting recaps nor note processing will run"),
        fix: { label: text("去配置", "Configure…"), onClick: goEngine } });

  // 定时任务磁盘权限（§25 cron FDA 探针，真相 = 真 cron 跑出来的 state/cron_probe.json）
  const cronName = text("定时任务磁盘权限", "Cron disk access");
  if (cron === "checking") rows.push({ key: "cron", state: "checking", name: cronName, detail: text("检测中…", "Checking…") });
  else if (cron === "ok") rows.push({ key: "cron", state: "ok", name: cronName, detail: text("定时任务能读取你的数据", "The scheduled jobs can read your data") });
  else if (cron === "blocked") {
    // 原生 cronFDAHealthRow fix = CronFDA.beginGrant()：复制 /usr/sbin/cron + 开面板；浏览器里复制照做、面板换成权限体检页深链
    const grant = () => { setFixNote("shell", null); void grantCronFda().catch((err) => setFixNote("shell", { prefix: "", detail: errorMessage(err) })); };
    rows.push({ key: "cron", state: "fail", name: cronName,
      detail: text("定时任务被 macOS 挡住了（缺「完全磁盘访问」）——笔记会静默丢失", "macOS is blocking the scheduled jobs (no Full Disk Access) — notes are silently lost"),
      fix: present ? { label: text("去授权", "Grant…"), onClick: grant } : { label: text("去授权", "Grant…"), href: permissionsHref, onClick: grant } });
  } else if (cron === "stale") {
    const row = permissions?.doctor.find((r) => r.name === "cron disk access");
    rows.push({ key: "cron", state: "fail", name: cronName, detail: row?.detail ?? text("定时任务可能停跑了", "The jobs may have stopped"),
      fix: { label: text("查看诊断", "Diagnostics"), href: depsHref } });
  } else {
    rows.push({ key: "cron", state: "neutral", name: cronName,
      detail: text("还没有数据——定时任务首次运行（约 30 分钟内）后自动出现，现在可以先点「完成」", "No data yet — appears after the first scheduled run (within ~30 min); you can finish now") });
  }

  // 录制引擎（只在录制开着时）
  if (rec && recOn) {
    const downloading = rec.diagnosis === "engine_npm_download";
    rows.push(schedulePaused(rec)
      // §61.7：按日程暂停不是失败——中性行，到点自动开
      ? { key: "capture", state: "neutral", name: text("录制引擎", "Capture engine"), detail: text("按日程暂停中——到设定时间自动开始录制", "Paused by schedule — recording starts automatically at the scheduled time") }
      : downloading
      ? { key: "capture", state: "checking", name: text("录制引擎", "Capture engine"), detail: text("录制引擎首次下载中（约 1-3 分钟）——不用做任何事，下载完会自动开始录制", "The recording engine is downloading for the first time (~1-3 min) — nothing to do; recording starts automatically when it finishes") }
      : rec.engine_running
        ? { key: "capture", state: "ok", name: text("录制引擎", "Capture engine"), detail: rec.mode === "screen_audio" ? text("录制中(屏幕+音频)", "Recording (screen + audio)") : text("录制中(仅屏幕)", "Recording (screen only)") }
        : { key: "capture", state: "fail", name: text("录制引擎", "Capture engine"), detail: text("未在录制——首次启动要下载引擎,可能需要几分钟", "Not recording — the first start downloads the engine and can take a few minutes"),
          fix: { label: text("启动引擎", "Start engine"), onClick: () => { if (!rec.screen_permission) shellCall("requestPermission", { kind: "screen" }); shellCall("restartRecording"); } } });
  }

  const allGreen = rows.every((r) => r.state === "ok" || r.state === "neutral");

  return (
    <>
      <ul className="setup-health">
        {rows.map((spec) => <Row key={spec.key} spec={spec} />)}
      </ul>
      {Object.entries(notes).map(([key, note]) => (
        <p key={key} className="settings-warning" role="alert"><span>{note.prefix}</span><span>{note.detail}</span></p>
      ))}
      {allGreen && (
        <div className="setup-card">
          <h4 className="setup-card-title">{text("🎉 一切就绪!", "🎉 All set!")}</h4>
          <p className="settings-helper">{text("点「完成」后 app 不再回到向导；关于页里可以重跑。", "After Done the app stops returning here; re-run it from the About page.")}</p>
        </div>
      )}
    </>
  );
}
