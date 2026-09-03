// 诊断页（原生 依赖检查 / 录制与 ingest / 关于 三页的只读部分 → §68.4；?page=diagnostics）：
//   doctor 表（--fast；「完整体检」= fast=0 含活探针，会花 token）· 管线活性（心跳 / 看板新鲜度 /
//   连崩）· 自动部署状态（§56 deploy_state 全字段）· 安装回执（§23）· 源健康（§48）· 日志尾巴
//   （只读、server size-cap；选一个文件看最后 N 行）。让 AI 修在卡片上（§54.1 第 5 项），这里不重复。
import { useEffect, useState } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { fetchDoctor, fetchLogTail } from "../api";
import { RelativeTime } from "../components/board/cardChrome";
import { errorMessage } from "../components/settings/useToast";
import { useI18n } from "../i18n";
import { buildAppUrl } from "../route";
import { refreshDiagnostics, useAppState } from "../store";
import type { DoctorReport, DoctorRow, LogTail } from "../types";

type Text = (zh: string, en: string) => string;

export function doctorSummary(report: DoctorReport, text: Text): string {
  const fail = report.checks.filter((c) => c.status === "FAIL").length;
  const warn = report.checks.filter((c) => c.status === "WARN").length;
  const ok = report.checks.length - fail - warn;
  return text(`${ok} 正常 / ${warn} 警告 / ${fail} 失败`, `${ok} ok / ${warn} warn / ${fail} fail`);
}

function DoctorTable({ rows }: { rows: DoctorRow[] }) {
  const { text } = useI18n();
  return (
    <table className="diag-table">
      <thead><tr><th>{text("状态", "Status")}</th><th>{text("检查", "Check")}</th><th>{text("说明 / 修法", "Detail / fix")}</th></tr></thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.name} data-status={row.status}>
            <td><span className={`chip chip-${row.status === "FAIL" ? "danger" : row.status === "WARN" ? "warning" : "success"}`}>{row.status}</span></td>
            <td>{row.name}</td>
            <td>
              <div>{row.detail}</div>
              {row.fix && row.status !== "OK" && <div className="settings-helper">{text("修法：", "Fix: ")}{row.fix}</div>}
            </td>
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
  const { text } = useI18n();
  const { diagnostics, pageErrors } = useAppState();
  const [full, setFull] = useState<DoctorReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<LogTail | null>(null);
  const [logName, setLogName] = useState("");
  const [logError, setLogError] = useState<string | null>(null);

  useEffect(() => {
    void refreshDiagnostics();
  }, []);

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

  return (
    <main className="settings-page diag-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>{text("← 返回看板", "← Back to board")}</a>
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("诊断", "Diagnostics")}</h2>
        <button type="button" className="btn" disabled={busy} onClick={() => { setFull(null); void refreshDiagnostics(true); }}>{text("刷新", "Refresh")}</button>
        <button type="button" className="btn" disabled={busy} onClick={() => void runFull()}>{busy ? text("体检中…", "Running…") : text("完整体检（含活探针）", "Full checkup (live probes)")}</button>
        <a className="settings-link" href={buildAppUrl(window.location.href, "permissions", null).toString()}>{text("权限体检", "Permissions checkup")}</a>
      </div>
      {pageErrors.diagnostics && <p className="settings-error" role="alert">{pageErrors.diagnostics}</p>}

      <section className="settings-section" aria-labelledby="diag-doctor-title">
        <h3 id="diag-doctor-title" className="settings-section-title">
          {text("依赖检查（python -m act.doctor）", "Dependency check (python -m act.doctor)")}
          {report && <span className="settings-helper"> · {doctorSummary(report, text)}{report.fast ? text(" · 快速模式", " · fast mode") : ""}{report.ran_at ? ` · ${report.ran_at}` : ""}</span>}
        </h3>
        {report && !report.ok && <p className="settings-warning" role="alert">{text("doctor 没跑成：", "doctor did not run: ")}{report.error}</p>}
        {report && report.checks.length > 0 && <DoctorTable rows={report.checks} />}
        {!report && !pageErrors.diagnostics && <p className="settings-helper">{text("体检中…", "Running…")}</p>}
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

          <section className="settings-section" aria-labelledby="diag-sources-title">
            <h3 id="diag-sources-title" className="settings-section-title">{text("来源健康（§48）", "Source health (§48)")}</h3>
            {diagnostics.radar_sources ? (
              <ul className="settings-list">
                {Object.entries(diagnostics.radar_sources).map(([src, h]) => (
                  <li key={src} className="settings-list-row">
                    <span className="settings-list-title"><span className={`chip chip-${!h.enabled ? "quiet" : h.skip_reason ? "warning" : h.stale ? "danger" : "success"}`}>{!h.enabled ? text("关", "off") : h.skip_reason ?? (h.stale ? text("久未成功", "stale") : "ok")}</span> {src}</span>
                    {h.last_ok && <span className="settings-list-meta"><RelativeTime iso={h.last_ok} prefix={text("上次成功 ", "last ok ")} /></span>}
                  </li>
                ))}
              </ul>
            ) : <p className="settings-helper">{text("看板里没有 radar_sources 投影。", "No radar_sources projection in the board.")}</p>}
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
                <pre className="diag-log">{log.lines.join("\n")}</pre>
              </>
            )}
          </section>
        </>
      )}
    </main>
  );
}
