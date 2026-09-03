// 向导末步「最后检查」（原生 SetupWizard.swift finaleStep 六行健康检查的 web 版，§68.5）：
//   屏幕录制权限（壳探针）· AI 引擎（GET /api/setup/engine）· 后台服务（GET /api/health 心跳）· 首次数据
//   （health.dashboard）· 定时任务磁盘权限（doctor `cron disk access` 行，经 GET /api/permissions）· 录制引擎
//   （壳录制状态，只在录制开着时出行）。每个红行带一颗修复按钮：去授权… / 去配置… / 启动后台服务（POST
//   /api/repair/actd）/ 立即生成一次（POST /api/setup/seed-dashboard）/ 去授权… · 查看诊断 / 启动引擎。
// 「—」= 中性（没选录制 / 定时任务还没跑过），不算红；全绿才出「🎉 一切就绪!」。文案逐字镜像 SetupWizard.swift:1014–1242。
import { useState, type ReactNode } from "react";
import { postRepairActd, postSeedDashboard } from "../../api";
import { useI18n } from "../../i18n";
import { buildAppUrl } from "../../route";
import { callShell, hasShellBridge, useShellState } from "../../shellBridge";
import { refreshBoard, refreshHealth, useAppState } from "../../store";
import type { DoctorRow, HealthSnapshot, SetupEngine } from "../../types";
import { RelativeTime } from "../board/cardChrome";
import { errorMessage } from "../settings/useToast";
import { authLabel } from "./EngineStep";

type Text = (zh: string, en: string) => string;
type HealthState = "checking" | "ok" | "fail" | "neutral";

interface RowSpec {
  key: string;
  state: HealthState;
  name: string;
  detail: ReactNode;
  fix?: { label: string; onClick?: () => void; href?: string };
}

/** 后台服务：心跳在且没停 → 在跑；没心跳 / 卡住 → 没在跑（server verdict 同一判据，§47.4） */
export function daemonRunning(health: HealthSnapshot | null): boolean | null {
  if (!health) return null;
  return Boolean(health.heartbeat && !health.heartbeat.stale);
}

/** 定时任务磁盘权限：doctor `cron disk access` 行 → ok / fail(blocked | stale) / neutral(无探针) */
export function cronVerdict(rows: DoctorRow[] | undefined): "ok" | "blocked" | "stale" | "neutral" | "checking" {
  if (rows === undefined) return "checking";
  const row = rows.find((r) => r.name === "cron disk access");
  if (!row) return "neutral";
  if (row.status === "OK") return "ok";
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
          ? <a className="btn" href={spec.fix.href}>{spec.fix.label}</a>
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
  const { health, permissions } = useAppState();
  const shell = useShellState();
  const present = hasShellBridge();
  const [fixingDaemon, setFixingDaemon] = useState(false);
  const [seeding, setSeeding] = useState(false);
  // 原生 fixNote = 前缀 + 原因；按行各存一条（启动与生成可以先后都失败，互不覆盖）
  const [notes, setNotes] = useState<Record<string, { prefix: string; detail: string }>>({});
  const setFixNote = (key: string, note: { prefix: string; detail: string } | null) =>
    setNotes((prev) => { const next = { ...prev }; if (note) next[key] = note; else delete next[key]; return next; });

  const rec = present ? shell?.recording ?? null : null;
  const recOn = Boolean(rec && rec.mode !== "off");
  const screen = present ? shell?.permissions.screen ?? "unknown" : "unknown";
  const running = daemonRunning(health);
  const dashboard = health ? health.dashboard : undefined;
  const cron = cronVerdict(permissions?.doctor);

  async function startDaemon() {
    setFixingDaemon(true);
    setFixNote("daemon", null);
    try {
      await postRepairActd();
      window.setTimeout(() => void refreshHealth(), 3000);
    } catch (err) {
      setFixNote("daemon", { prefix: text("启动失败: ", "Start failed: "), detail: errorMessage(err) });
    } finally {
      setFixingDaemon(false);
    }
  }

  async function seed() {
    setSeeding(true);
    setFixNote("data", null);
    try {
      const receipt = await postSeedDashboard();
      if (!receipt.ok) setFixNote("data", { prefix: text("生成失败: ", "Seeding failed: "), detail: receipt.error ?? "" });
      else await Promise.all([refreshHealth(), refreshBoard()]);
    } catch (err) {
      setFixNote("data", { prefix: text("生成失败: ", "Seeding failed: "), detail: errorMessage(err) });
    } finally {
      setSeeding(false);
    }
  }

  const shellCall = (method: "requestPermission" | "openPane" | "restartRecording", args: Record<string, unknown> = {}) => {
    setFixNote("shell", null);
    void callShell(method, args).catch((err) => setFixNote("shell", { prefix: "", detail: errorMessage(err) }));
  };

  const permissionsHref = buildAppUrl(window.location.href, "permissions", null).toString();
  const depsHref = buildAppUrl(window.location.href, "deps", null).toString();

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
      : { key: "engine", state: "fail", name: text("AI 引擎", "AI engine"), detail: text("没有 AI 引擎,提案永远不会被执行", "Without an AI engine, proposals will never be executed"),
        fix: { label: text("去配置", "Configure…"), onClick: goEngine } });

  // 后台服务
  rows.push(running === null
    ? { key: "daemon", state: "checking", name: text("后台服务", "Background service"), detail: text("检测中…", "Checking…") }
    : running
      ? { key: "daemon", state: "ok", name: text("后台服务", "Background service"), detail: text("在后台运行,几秒内处理你的每个操作", "Running in the background, handling your actions within seconds") }
      : { key: "daemon", state: "fail", name: text("后台服务", "Background service"), detail: text("没有运行——批准的卡片不会被执行", "Not running — approved cards won't execute"),
        fix: { label: fixingDaemon ? text("启动中…", "Starting…") : text("启动后台服务", "Start it"), onClick: () => { if (!fixingDaemon) void startDaemon(); } } });

  // 首次数据
  rows.push(dashboard === undefined
    ? { key: "data", state: "checking", name: text("首次数据", "First data"), detail: text("检测中…", "Checking…") }
    : dashboard
      ? { key: "data", state: "ok", name: text("首次数据", "First data"),
        detail: typeof dashboard.age_s === "number"
          ? <><span>{text("已生成(", "Generated (")}</span><RelativeTime epoch={Date.now() / 1000 - dashboard.age_s} /><span>)</span></>
          : text("已生成", "Generated") }
      : { key: "data", state: "fail", name: text("首次数据", "First data"), detail: text("还没有——后台服务启动后约 10 秒自动生成", "Not yet — appears ~10 s after the background service starts"),
        fix: { label: seeding ? text("生成中…", "Seeding…") : text("立即生成一次", "Generate now"), onClick: () => { if (!seeding) void seed(); } } });

  // 定时任务磁盘权限（§25 cron FDA 探针，真相 = 真 cron 跑出来的 state/cron_probe.json）
  const cronName = text("定时任务磁盘权限", "Cron disk access");
  if (cron === "checking") rows.push({ key: "cron", state: "checking", name: cronName, detail: text("检测中…", "Checking…") });
  else if (cron === "ok") rows.push({ key: "cron", state: "ok", name: cronName, detail: text("定时任务能读取你的数据", "The scheduled jobs can read your data") });
  else if (cron === "blocked") {
    rows.push({ key: "cron", state: "fail", name: cronName,
      detail: text("定时任务被 macOS 挡住了（缺「完全磁盘访问」）——笔记会静默丢失", "macOS is blocking the scheduled jobs (no Full Disk Access) — notes are silently lost"),
      fix: present ? { label: text("去授权", "Grant…"), onClick: () => shellCall("openPane", { pane: "full_disk" }) } : { label: text("去授权", "Grant…"), href: permissionsHref } });
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
    rows.push(downloading
      ? { key: "capture", state: "checking", name: text("录制引擎", "Capture engine"), detail: text("录制引擎首次下载中（约 1-3 分钟）——不用做任何事，下载完会自动开始录制", "The recording engine is downloading for the first time (~1-3 min) — nothing to do; recording starts automatically when it finishes") }
      : rec.engine_running
        ? { key: "capture", state: "ok", name: text("录制引擎", "Capture engine"), detail: rec.mode === "screen_audio" ? text("录制中(屏幕+音频)", "Recording (screen + audio)") : text("录制中(仅屏幕)", "Recording (screen only)") }
        : { key: "capture", state: "fail", name: text("录制引擎", "Capture engine"), detail: text("未在录制——首次启动要下载引擎,可能需要几分钟", "Not recording — the first start downloads the engine and can take a few minutes"),
          fix: { label: text("启动引擎", "Start engine"), onClick: () => { if (!rec.screen_permission) shellCall("requestPermission", { kind: "screen" }); shellCall("restartRecording"); } } });
  }

  const allGreen = rows.every((r) => r.state === "ok" || r.state === "neutral");
  const importUrl = buildAppUrl(window.location.href, "settings", null);
  importUrl.searchParams.set("anchor", "claude_import");

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
          <p className="settings-helper">{text("点「完成」后看板不再回到向导；设置 → 关于 里可以重跑。", "After Done the board stops returning here; re-run from Settings → About.")}</p>
        </div>
      )}
      <div className="settings-actions">
        <a className="btn" href={importUrl.toString()}>{text("导入 Claude Code 已有工作…", "Import existing Claude Code work…")}</a>
        <span className="settings-helper">{text("(可选:把你在终端里跑过的 Claude 会话变成卡片)", "(optional: turn Claude sessions you ran in Terminal into cards)")}</span>
      </div>
    </>
  );
}
