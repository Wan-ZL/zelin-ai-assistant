// 依赖检查页（原生 Pages.swift DepsView：依赖 快速行 + 雷达健康 + 诊断 三段 → §15.1 / §68.4；?page=deps，左侧导航栏第三项；
// ?page=diagnostics 是同一页的旧深链，横幅 / 关于区的链接仍指它）：
//   依赖快速行（components/settings/DepRows：doctor 行 + 壳 + 凭证 + cron 探针，每行一颗对症按钮）· 雷达健康（原生
//   radarDetail：最近成功 … / 从未成功：<原因> / 暂无数据）· doctor 表（--fast；「运行诊断」= fast=0 含活探针，会花 token；
//   零失败「全部通过 ✓」、「完整报告」折叠成文本）· web 自有的 管线活性（心跳 / 看板新鲜度 / 连崩）· 自动部署状态（§56
//   deploy_state 全字段）· 安装回执（§23）· 日志尾巴（只读、server size-cap；?log=<name> 深链直接翻开）。
//   让 AI 修在卡片上（§54.1 第 5 项），这里只在 doctor 有未通过项时出现。doctor 行带 §25 failure_id 时给原生 FailureCatalog
//   的对症一键（安装页 / 去设置 / 去授权… / 一键修复 / 显示文件…，components/settings/failureAction）。
import { useEffect, useState } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { fetchDoctor, fetchLogTail, postAiFixDoctor } from "../api";
import { DepRows, RadarHealthRows } from "../components/settings/DepRows";
import { FailureActionButton } from "../components/settings/failureAction";
import { errorMessage } from "../components/settings/useToast";
import { useI18n } from "../i18n";
import { buildAppUrl } from "../route";
import { refreshDiagnostics, useAppState } from "../store";
import type { DoctorReport, DoctorRow, LogTail } from "../types";

type Text = (zh: string, en: string) => string;

/** 原生 DepsView：零失败说「全部通过 ✓」，否则「N 项未通过——每条都有对应按钮」；计数（正常 / 警告）是 web 加的第二个节点 */
export function doctorSummary(report: DoctorReport, text: Text): { verdict: string; counts: string } {
  const fail = report.checks.filter((c) => c.status === "FAIL").length;
  const warn = report.checks.filter((c) => c.status === "WARN").length;
  const ok = report.checks.length - fail - warn;
  const verdict = fail === 0
    ? text("全部通过 ✓", "All checks passed ✓")
    : text(`${fail} 项未通过——每条都有对应按钮`, `${fail} check(s) failed — each has its own button`);
  return { verdict, counts: text(`（${ok} 正常 / ${warn} 警告）`, `(${ok} ok / ${warn} warn)`) };
}

/** 原生「完整报告」的文本形：[status] name: detail（+ fix 缩进一行） */
export function fullReportText(report: DoctorReport): string {
  return report.checks.map((row) => {
    const line = `[${String(row.status).toLowerCase()}] ${row.name}: ${row.detail}`;
    return row.fix && row.status !== "OK" ? `${line}\n    fix: ${row.fix}` : line;
  }).join("\n");
}

function DoctorTable({ rows }: { rows: DoctorRow[] }) {
  const { text } = useI18n();
  return (
    <table className="diag-table">
      <thead><tr><th>{text("状态", "Status")}</th><th>{text("检查", "Check")}</th><th>{text("说明 / 修法", "Detail / fix")}</th><th>{text("动作", "Action")}</th></tr></thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.name} data-status={row.status} data-failure={row.failure_id || undefined}>
            <td><span className={`chip chip-${row.status === "FAIL" ? "danger" : row.status === "WARN" ? "warning" : "success"}`}>{row.status}</span></td>
            <td>{row.name}</td>
            <td>
              <div>{row.detail}</div>
              {row.fix && row.status !== "OK" && <div className="settings-helper">{text("修法：", "Fix: ")}{row.fix}</div>}
            </td>
            <td>{row.status !== "OK" && <FailureActionButton failureId={row.failure_id} compact />}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function KeyValues({ obj }: { obj: Record<string, unknown> }) {
  return (
    <dl className="settings-meta">
      {Object.entries(obj).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => (
        <div key={k}><dt>{k}</dt><dd>{typeof v === "string" ? v : JSON.stringify(v)}</dd></div>
      ))}
    </dl>
  );
}

export function DiagnosticsPage() {
  const { text, language } = useI18n();
  const { diagnostics, pageErrors } = useAppState();
  const [full, setFull] = useState<DoctorReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [rechecking, setRechecking] = useState(false);
  const [aiFix, setAiFix] = useState<"idle" | "busy" | string>("idle");
  const [log, setLog] = useState<LogTail | null>(null);
  const [logName, setLogName] = useState("");
  const [logError, setLogError] = useState<string | null>(null);

  useEffect(() => {
    void refreshDiagnostics();
    // ?log=<name>：横幅「查看日志」深链——直接把该日志尾巴翻开（名字只认 server 同一白名单形）
    const wanted = new URLSearchParams(window.location.search).get("log") ?? "";
    if (/^[A-Za-z0-9._-]+\.log$/.test(wanted)) void openLog(wanted);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // 原生 DepsView「重新检查」：按钮与状态行都显示「检查中…」直到快照回来
  async function recheck() {
    setFull(null);
    setRechecking(true);
    try {
      await refreshDiagnostics(true);
    } finally {
      setRechecking(false);
    }
  }

  // 原生 DepsView「让 AI 修」：诊断有未通过项时出现；上下文由 server 从 doctor 报告推导（零客户端文本）
  async function fixWithAi() {
    setAiFix("busy");
    try {
      await postAiFixDoctor(language);
      setAiFix("idle");
    } catch (err) {
      setAiFix(text("让 AI 修启动失败：", "Fix with AI failed to launch: ") + errorMessage(err));
    }
  }

  async function runFull() {
    setBusy(true);
    try {
      setFull(await fetchDoctor(false, true));
    } catch (err) {
      setFull({ ok: false, checks: [], home: "", rc: -1, fast: false, ran_at: "", error: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  async function openLog(name: string) {
    setLogName(name);
    setLogError(null);
    setLog(null);
    if (!name) return;
    try {
      setLog(await fetchLogTail(name, 300));
    } catch (err) {
      setLogError(errorMessage(err));
    }
  }

  const report = full ?? diagnostics?.doctor ?? null;
  const summary = report ? doctorSummary(report, text) : null;
  const noRows = !rechecking && !busy && (report ? report.checks.length === 0 : Boolean(pageErrors.diagnostics));

  return (
    <main className="settings-page diag-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>{text("← 返回看板", "← Back to board")}</a>
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("依赖检查", "Dependencies")}</h2>
        <button type="button" className="btn" disabled={busy || rechecking} onClick={() => void recheck()}>{rechecking ? text("检查中…", "Checking…") : text("重新检查", "Re-check")}</button>
        <a className="settings-link" href={buildAppUrl(window.location.href, "permissions", null).toString()}>{text("权限体检", "Permissions Checkup")}</a>
      </div>
      <p className="settings-helper">{text("车跑之前轮子都得在。", "All wheels must be on before the car runs.")}</p>
      {pageErrors.diagnostics && <p className="settings-error" role="alert">{pageErrors.diagnostics}</p>}
      {rechecking && <p className="settings-helper">{text("检查中…", "Checking…")}</p>}
      {noRows && <p className="settings-helper">{text("点「重新检查」开始", "Click \"Re-check\" to start")}</p>}
      <DepRows report={report} probe={diagnostics?.cron_probe} />

      <section className="settings-section" aria-labelledby="diag-sources-title">
        <h3 id="diag-sources-title" className="settings-section-title">{text("雷达健康", "Radar Health")}</h3>
        <RadarHealthRows sources={diagnostics ? diagnostics.radar_sources : undefined} />
      </section>

      <section className="settings-section" aria-labelledby="diag-doctor-title">
        <h3 id="diag-doctor-title" className="settings-section-title">{text("诊断", "Diagnostics")}<span className="settings-helper"> · python -m act.doctor</span></h3>
        <div className="settings-actions">
          <button type="button" className="btn" disabled={busy} onClick={() => void runFull()}>{busy ? text("诊断中…", "Running…") : text("运行诊断", "Run diagnostics")}</button>
          {busy
            ? <span className="settings-helper">{text("最长约 1-2 分钟（含一次真实 claude 调用）", "up to ~1-2 min (includes one live claude call)")}</span>
            : summary && report?.ok && (
              <span className={`settings-helper diag-verdict${report.checks.some((c) => c.status === "FAIL") ? " is-warning" : " is-ok"}`}>
                <span>{summary.verdict}</span><span>{summary.counts}</span>
                <span>{report.fast ? text(" · 快速模式", " · fast mode") : ""}{report.ran_at ? ` · ${report.ran_at}` : ""}</span>
              </span>
            )}
          {report && report.ok && report.checks.some((c) => c.status === "FAIL") && (
            <>
              <button type="button" className="btn" disabled={aiFix === "busy"} onClick={() => void fixWithAi()}>
                {aiFix === "busy" ? text("正在准备诊断包…", "Preparing the diagnostic bundle…") : text("让 AI 修", "Fix with AI")}
              </button>
              {aiFix !== "idle" && aiFix !== "busy" && <span className="settings-warning" role="alert">{aiFix}</span>}
            </>
          )}
        </div>
        <p className="settings-helper">{text("发现异常时会自动运行一次快速诊断；这个按钮跑完整版（含一次真实 claude 调用）。让 AI 修会在终端开一个带诊断包的 claude 修复会话。", "A quick diagnostic auto-runs when something looks broken; this button runs the full version (one live claude call). Fix with AI opens a claude repair session in Terminal with the diagnostic bundle attached.")}</p>
        {report && !report.ok && <p className="settings-warning" role="alert">{text("doctor 没跑成：", "doctor did not run: ")}{report.error}</p>}
        {report && report.checks.length > 0 && <DoctorTable rows={report.checks} />}
        {report && report.checks.length > 0 && (
          <details className="diag-full-report">
            <summary>{text("完整报告", "Full report")}</summary>
            <pre className="diag-log">{fullReportText(report)}</pre>
          </details>
        )}
        {!report && !pageErrors.diagnostics && <p className="settings-helper">{text("检查中…", "Checking…")}</p>}
      </section>

      {diagnostics && (
        <>
          <section className="settings-section" aria-labelledby="diag-health-title">
            <h3 id="diag-health-title" className="settings-section-title">{text("管线活性（§47.4）", "Pipeline liveness (§47.4)")} · <span className={`chip chip-${diagnostics.health.verdict === "ok" ? "success" : diagnostics.health.verdict === "unknown" ? "quiet" : "danger"}`}>{diagnostics.health.verdict}</span></h3>
            <KeyValues obj={{
              heartbeat_age_s: diagnostics.health.heartbeat?.age_s,
              heartbeat_phase: diagnostics.health.heartbeat?.phase,
              heartbeat_pid: diagnostics.health.heartbeat?.pid,
              dashboard_age_s: diagnostics.health.dashboard?.age_s,
              consecutive_failures: diagnostics.health.loop_health.consecutive_failures,
              last_error: diagnostics.health.loop_health.last_error,
              registry_backend: diagnostics.registry_backend,
            }} />
          </section>

          <section className="settings-section" aria-labelledby="diag-deploy-title">
            <h3 id="diag-deploy-title" className="settings-section-title">{text("自动部署（§56）", "Auto-deploy (§56)")}</h3>
            {diagnostics.deploy_state ? <KeyValues obj={diagnostics.deploy_state} /> : <p className="settings-helper">{text("看板里没有 deploy_state（自动部署 agent 还没跑过）。", "No deploy_state in the board (the auto-deploy agent has not run yet).")}</p>}
          </section>

          <section className="settings-section" aria-labelledby="diag-install-title">
            <h3 id="diag-install-title" className="settings-section-title">{text("安装回执（§23）", "Install report (§23)")}{diagnostics.install_report?.version && <span className="settings-helper"> · v{diagnostics.install_report.version} · {diagnostics.install_report.generated_at}</span>}</h3>
            {diagnostics.install_report ? (
              <ul className="settings-list">
                {diagnostics.install_report.steps.map((step, i) => (
                  <li key={`${step.name}-${i}`} className="settings-list-row" data-status={step.status}>
                    <span className="settings-list-title"><span className={`chip chip-${step.status === "ok" ? "success" : step.status === "fail" ? "danger" : "warning"}`}>{step.status}</span> {step.name}</span>
                    {step.detail && <p className="settings-list-desc">{step.detail}</p>}
                  </li>
                ))}
              </ul>
            ) : <p className="settings-helper">{text("没有 install_report.json（还没跑过 install.sh）。", "No install_report.json (install.sh has not run yet).")}</p>}
          </section>

          <section className="settings-section" aria-labelledby="diag-logs-title">
            <h3 id="diag-logs-title" className="settings-section-title">{text("日志（只读，最后 300 行）", "Logs (read-only, last 300 lines)")}</h3>
            <div className="settings-knob-controls">
              <select className="settings-select" aria-label={text("选择日志", "Pick a log")} value={logName} onChange={(e) => void openLog(e.target.value)}>
                <option value="">{text("— 选一个日志文件 —", "— pick a log file —")}</option>
                {diagnostics.logs.map((entry) => (
                  <option key={entry.path} value={entry.name}>{entry.name} · {Math.round(entry.size / 1024)} KB</option>
                ))}
              </select>
              {logName && <button type="button" className="btn" onClick={() => void openLog(logName)}>{text("重读", "Reload")}</button>}
            </div>
            {logError && <p className="settings-warning" role="alert">{logError}</p>}
            {log && (
              <>
                <p className="settings-list-dim">{log.path} · {Math.round(log.size / 1024)} KB{log.truncated ? text(" · 只显示尾巴", " · tail only") : ""}</p>
                <pre className="diag-log diag-log-tail">{log.lines.join("\n")}</pre>
              </>
            )}
          </section>
        </>
      )}
    </main>
  );
}
