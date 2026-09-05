// 依赖检查页的「依赖」快速行 + 「雷达健康」三行（原生 Pages.swift DepsModel.check / radarDetail 的 web 版；
// CONTRACT §15.1 / §68.4 追记）。每行 ✅ / ⚠️ + 名字 + 探针说明 + 一颗对症按钮（下载页 / 去录制页 / 去授权 / 显示 / 去设置）。
// 数据来源按行诚实：
//   · doctor 行（node/npx · claude CLI · gh CLI · daemon python = PyYAML · obsidian vault）——说明 = doctor 的 detail 原句；
//   · 壳（录制引擎 · 屏幕录制权限 · 麦克风权限）——浏览器里如实说只在看板 app 里可探；
//   · GET /api/secrets（Slack token · Gmail 应用密码 · Anthropic API key）：（App 内管理）/（App 内管理；未设置）；
//   · state/cron_probe.json（定时任务磁盘权限）：没探针 / 探针过期 / 能读 / 被挡 四态，原生 cronFDARow 同规则。
// 「显示」= POST /api/folders/open {key:"obsidian_raw"}（路径 server 读）；「去授权」壳里开系统设置面板，浏览器里退成权限体检页。
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { postFolderOpen } from "../../api";
import { useI18n } from "../../i18n";
import { buildAppUrl, type AppPage } from "../../route";
import { callShell, hasShellBridge, useShellState, type ShellRecordingState, type ShellState } from "../../shellBridge";
import { refreshFailures, refreshSecrets, useAppState } from "../../store";
import type { CronProbe, DoctorReport, DoctorRow, FailureCatalog, RadarSourceHealth, SecretsStatus } from "../../types";
import { RelativeTime } from "../board/cardChrome";
import { errorMessage } from "./useToast";

type Text = (zh: string, en: string) => string;

export interface DepRow {
  id: string;
  name: string;
  ok: boolean | null;          // null = 探不到（浏览器里的壳行 / doctor 还没回）
  detail: ReactNode;
  action: ReactNode;
}

/** 原生 CronProbe.read + cronFDARow：2 h 内的探针才算新鲜 */
export const CRON_PROBE_FRESH_MS = 2 * 3600 * 1000;

function pageHref(page: AppPage, anchor?: string): string {
  const url = buildAppUrl(window.location.href, page, null);
  if (anchor) url.searchParams.set("anchor", anchor);
  return url.toString();
}

function ExternalLink({ href, label }: { href: string; label: string }) {
  return <a className="btn btn-quiet" href={href} target="_blank" rel="noreferrer">{label}</a>;
}

/** 「去授权」：壳里开系统设置面板；浏览器里退成权限体检页深链（原生 DepAction.grant / cronFDA） */
function GrantButton({ pane }: { pane: "screen" | "microphone" | "full_disk" }) {
  const { text } = useI18n();
  const label = text("去授权", "Grant…");
  if (!hasShellBridge()) return <a className="btn btn-quiet" href={pageHref("permissions")}>{label}</a>;
  return <button type="button" className="btn btn-quiet" onClick={() => void callShell("openPane", { pane }).catch(() => undefined)}>{label}</button>;
}

/** 「显示」：原生 NSWorkspace.activateFileViewerSelecting(vault) → server 按已保存的 obsidian_raw 打开访达 */
function RevealButton() {
  const { text } = useI18n();
  const [note, setNote] = useState<string | null>(null);
  return (
    <>
      <button type="button" className="btn btn-quiet" onClick={() => void postFolderOpen("obsidian_raw").then(() => setNote(null)).catch((e) => setNote(errorMessage(e)))}>{text("显示", "Reveal")}</button>
      {note && <span className="settings-warning" role="alert">{note}</span>}
    </>
  );
}

function doctorRow(report: DoctorReport | null, name: string): DoctorRow | null {
  return report?.checks.find((c) => c.name === name) ?? null;
}

function fromDoctor(report: DoctorReport | null, doctorName: string, id: string, display: string, action: ReactNode, text: Text): DepRow {
  const row = doctorRow(report, doctorName);
  const detail = row ? row.detail : report ? "—" : text("检查中…", "Checking…");
  return { id, name: display, ok: row ? row.status === "ok" : null, detail: <code>{detail}</code>, action };   // §25 小写词表
}

function failureText(catalog: FailureCatalog | null, id: string, lang: "zh" | "en"): string | null {
  const entry = catalog?.failures[id];
  return entry ? entry[lang] : null;
}

/** 录制引擎行（原生：pgrep 存活 + 死因分类；首次 npm 下载不算错） */
function engineRow(rec: ShellRecordingState, catalog: FailureCatalog | null, lang: "zh" | "en", text: Text): DepRow {
  let ok = rec.engine_running;
  let detail: ReactNode = <><code>pgrep</code><span>{text("（引擎进程存活）", " (engine process alive)")}</span></>;
  if (rec.mode !== "off" && rec.diagnosis) {
    detail = failureText(catalog, rec.diagnosis, lang) ?? <code>{rec.diagnosis}</code>;
    ok = rec.engine_running && rec.diagnosis === "engine_npm_download";
  }
  return { id: "engine", name: text("录制引擎", "Recording engine"), ok, detail,
    action: <a className="btn btn-quiet" href={pageHref("ingest")}>{text("去录制页", "Open Recording")}</a> };
}

function screenRow(shell: ShellState, catalog: FailureCatalog | null, lang: "zh" | "en", text: Text): DepRow {
  const rec = shell.recording;
  const detail = rec.tcc_lost
    ? (failureText(catalog, "screen_tcc_lost", lang) ?? "screen_tcc_lost")
    : text("CGPreflightScreenCaptureAccess()（未授权时引擎启动即退出、录不到任何内容）", "CGPreflightScreenCaptureAccess() (without it the engine exits instantly, capturing nothing)");
  return { id: "screen_tcc", name: text("屏幕录制权限", "Screen Recording permission"), ok: rec.screen_permission && !rec.tcc_lost,
    detail, action: <GrantButton pane="screen" /> };
}

function micRow(shell: ShellState, text: Text): DepRow {
  const granted = shell.permissions.microphone === "granted";
  const needsMic = shell.recording.mode === "screen_audio";
  const detail = granted
    ? text("已授权（「屏幕+音频」转写可用）", "granted (Screen + Audio transcription available)")
    : needsMic
      ? text("未授权——「屏幕+音频」模式录不到语音", "not granted — Screen + Audio mode can't transcribe")
      : text("未授权（当前模式不需要；切「屏幕+音频」前先授权）", "not granted (not needed in the current mode; grant before switching to Screen + Audio)");
  return { id: "mic_tcc", name: text("麦克风权限", "Microphone permission"), ok: granted || !needsMic, detail, action: <GrantButton pane="microphone" /> };
}

/** 壳不在场（浏览器，shell = null）：三行如实说探不到 */
function shellRows(shell: ShellState | null, catalog: FailureCatalog | null, lang: "zh" | "en", text: Text): DepRow[] {
  if (!shell) {
    const detail = text("只在看板 app（壳）里可探——这是浏览器里打开的看板", "Only probeable inside the board app (shell) — this board is open in a browser");
    return [
      { id: "engine", name: text("录制引擎", "Recording engine"), ok: null, detail, action: <a className="btn btn-quiet" href={pageHref("ingest")}>{text("去录制页", "Open Recording")}</a> },
      { id: "screen_tcc", name: text("屏幕录制权限", "Screen Recording permission"), ok: null, detail, action: <GrantButton pane="screen" /> },
      { id: "mic_tcc", name: text("麦克风权限", "Microphone permission"), ok: null, detail, action: <GrantButton pane="microphone" /> },
    ];
  }
  return [engineRow(shell.recording, catalog, lang, text), screenRow(shell, catalog, lang, text), micRow(shell, text)];
}

const SECRET_ROWS: Array<[id: string, file: string, zh: string, en: string]> = [
  ["slack", "slack-user-token.txt", "Slack token", "Slack token"],
  ["gmail", "gmail-app-password.txt", "Gmail 应用密码", "Gmail app password"],
  ["anthropic", "anthropic-api-key.txt", "Anthropic API key", "Anthropic API key"],
];

/** 三把凭证（原生 credRow：secrets 文件在 = （App 内管理），否则（App 内管理；未设置）；旧路径回退在新架构里已退役） */
function secretRows(secrets: SecretsStatus | null, text: Text): DepRow[] {
  const action = <a className="btn btn-quiet" href={pageHref("settings", "credentials")}>{text("去设置", "Open Settings")}</a>;
  return SECRET_ROWS.map(([id, file, zh, en]) => {
    const entry = secrets?.secrets.find((s) => s.name === file) ?? null;
    const present = entry ? entry.present : null;
    const suffix = present === null ? "" : present ? text("（App 内管理）", " (managed in-app)") : text("（App 内管理；未设置）", " (managed in-app; not set)");
    return { id, name: text(zh, en), ok: present, action,
      detail: <><code>config/secrets/{file}</code>{suffix && <span>{suffix}</span>}</> };
  });
}

/** 原生 cronFDARow 四态：没探针 / 探针过期（>2 h）/ 能读 / 被挡 */
export function cronVerdict(probe: CronProbe | null | undefined, now: number, text: Text): { ok: boolean; detail: string } {
  if (!probe) {
    return { ok: false, detail: text("还没有探测数据——等下一次定时运行（约 30 分钟）；一直没有就重跑一遍安装（会更新定时任务）",
      "No probe data yet — wait for the next scheduled run (~30 min); if it never appears, rerun the installer (updates the cron line)") };
  }
  const ts = probe.ts ? Date.parse(probe.ts) : NaN;
  const age = Number.isNaN(ts) ? null : now - ts;
  if (age !== null && age > CRON_PROBE_FRESH_MS) {
    const hours = Math.floor(age / 3600_000);
    return { ok: false, detail: text(`最近一次探测在 ${hours} 小时前——定时任务可能停跑了（先查「诊断」）`, `Last probe ${hours}h ago — the scheduled jobs may have stopped (run Diagnostics)`) };
  }
  const path = probe.protected_path ?? "";
  if (probe.read_ok) return { ok: true, detail: text(`定时任务能读取 ${path}`, `cron can read ${path}`) };
  return { ok: false, detail: text(`macOS 挡住了定时任务读取 ${path}——屏幕记录不会变成笔记。点「去授权」按提示给 cron 开「完全磁盘访问」`,
    `macOS blocks the scheduled jobs from reading ${path} — captures never become notes. Click Grant and follow the steps`) };
}

function cronRow(probe: CronProbe | null | undefined, text: Text): DepRow {
  const verdict = cronVerdict(probe, Date.now(), text);
  return { id: "cron_fda", name: text("定时任务磁盘权限", "Cron disk access"), ok: verdict.ok, detail: verdict.detail, action: <GrantButton pane="full_disk" /> };
}

/** 原生 DepsModel.check 的 12 行，顺序照旧 */
export function buildDepRows(report: DoctorReport | null, shell: ShellState | null, secrets: SecretsStatus | null,
  catalog: FailureCatalog | null, probe: CronProbe | null | undefined, lang: "zh" | "en", text: Text): DepRow[] {
  const [engine, screen, mic] = shellRows(shell, catalog, lang, text);
  return [
    fromDoctor(report, "node/npx", "npx", "Node / npx", <ExternalLink href="https://nodejs.org" label={text("下载页", "Download")} />, text),
    engine, screen, mic,
    fromDoctor(report, "claude CLI", "claude", "claude CLI", <ExternalLink href="https://claude.com/claude-code" label={text("下载页", "Download")} />, text),
    fromDoctor(report, "gh CLI", "gh", "gh CLI", <ExternalLink href="https://cli.github.com" label={text("下载页", "Download")} />, text),
    fromDoctor(report, "daemon python", "pyyaml", "PyYAML", <ExternalLink href="https://pyyaml.org" label={text("下载页", "Download")} />, text),
    fromDoctor(report, "obsidian vault", "vault", "Obsidian vault", <RevealButton />, text),
    ...secretRows(secrets, text),
    cronRow(probe, text),
  ];
}

export function DepRows({ report, probe }: { report: DoctorReport | null; probe: CronProbe | null | undefined }) {
  const { text, language } = useI18n();
  const { secrets, failures } = useAppState();
  const shell = useShellState();
  useEffect(() => {
    if (!secrets) void refreshSecrets();
    if (!failures) void refreshFailures();
  }, [secrets, failures]);
  const rows = buildDepRows(report, hasShellBridge() ? shell : null, secrets, failures, probe, language === "zh" ? "zh" : "en", text);
  return (
    <ul className="settings-list dep-rows" aria-label={text("依赖", "Dependencies")}>
      {rows.map((row) => (
        <li key={row.id} className="settings-list-row dep-row" data-dep={row.id} data-ok={row.ok === null ? "unknown" : String(row.ok)}>
          <div className="dep-row-head">
            <span className="dep-row-mark" aria-hidden="true">{row.ok === true ? "✅" : row.ok === false ? "⚠️" : "…"}</span>
            <span className="settings-list-title">{row.name}</span>
            <span className="dep-row-action">{row.action}</span>
          </div>
          <p className="settings-list-desc dep-row-detail">{row.detail}</p>
        </li>
      ))}
    </ul>
  );
}

// ----- 雷达健康（原生 DepsView radarDetail / humanSkipReason；与设置页 sourceHealth 的句子是另一张原生表） ----- #

/** 原生 humanSkipReason：机器 skip_reason → 人话（词表外原样） */
export function depsSkipReasonLabel(reason: string, text: Text): string {
  switch (reason) {
    case "no_credentials": return text("未配置凭证", "no credentials");
    case "auth_failed": case "auth_error": case "invalid_credentials": return text("凭证无效", "invalid credentials");
    case "network_error": case "timeout": return text("网络错误", "network error");
    case "connect_failed": return text("连接失败", "connection failed");
    case "disabled": return text("已禁用", "disabled");
    case "vault_missing": return text("没指定 Obsidian 目录", "no Obsidian folder set");
    case "vault_empty": return text("目录里没有笔记（去开录制/授权）", "folder empty (start recording / grant access)");
    case "no_api_key": return text("定时任务没 API key（去填）", "scheduled job has no API key (add one)");
    case "extract_failed": return text("截图→笔记链报错（看依赖检查）", "capture→note chain erroring (see Dependencies)");
    case "mcp_not_configured": return text("Slack 兜底没连（去连接 Slack）", "Slack fallback not connected (connect Slack)");
    default: return reason;
  }
}

/** 原生 radarColor：绿 = 成功过；橙 = 从未、有已知原因；红 = 从未、原因不明；灰 = 文件里还没这个源 */
export function radarTone(entry: RadarSourceHealth | undefined): "quiet" | "success" | "warning" | "danger" {
  if (!entry) return "quiet";
  if (entry.last_ok) return "success";
  return entry.skip_reason || !entry.enabled ? "warning" : "danger";
}

const RADAR_ORDER: Array<[key: string, title: string]> = [["obsidian", "Obsidian"], ["gmail", "Gmail"], ["slack", "Slack"]];

export function RadarHealthRows({ sources }: { sources: Record<string, RadarSourceHealth> | null | undefined }) {
  const { text } = useI18n();
  if (!sources) return <p className="settings-helper">{text("暂无数据", "No data yet")}</p>;
  return (
    <ul className="settings-list radar-rows" aria-label={text("雷达健康", "Radar Health")}>
      {RADAR_ORDER.map(([key, title]) => {
        const entry = sources[key];
        // 投影里 enabled:false 且从未成功 = 原生 radar_health 的 skip_reason "disabled"
        const reason = entry ? (entry.skip_reason || (!entry.enabled ? "disabled" : null)) : null;
        return (
          <li key={key} className={`settings-list-row radar-row is-${radarTone(entry)}`} data-source={key}>
            <span className={`status-dot status-dot-${radarTone(entry)}`} aria-hidden="true" />
            <span className="settings-list-title">{title}</span>
            <span className="radar-detail">
              {!entry
                ? text("暂无数据", "No data yet")
                : entry.last_ok
                  ? <RelativeTime iso={entry.last_ok} prefix={text("最近成功 ", "last ok ")} />
                  : <><span>{text("从未成功", "never succeeded")}</span>{reason && <><span>{text("：", ": ")}</span><span>{depsSkipReasonLabel(reason, text)}</span></>}</>}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
