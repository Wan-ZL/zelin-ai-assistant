// 权限体检页（§15 v0.13 权限体检窗 → §68.3 web 版；?page=permissions）。原生 PermissionsView 的顺序原样：
//   抬头「权限体检」+ 一句说明 → 屏幕记录（一次性同意块 / 实时状态行，RecordingConsentSection）→
//   「系统权限」三行（屏幕录制 / 笔记库访问（Documents） / 通知 + web 多一行麦克风，CapabilityRows）→
//   telemetry 披露块（TelemetryBlock）→ 页脚「打开依赖检查」…「完成」（首启：回向导）/「关闭」（体检：回看板）。
// GUI 项的真相只有壳知道（TCC 探针是原生 API）：桥在场时读 shell.permissions、按钮 = requestPermission /
// openPane，2 s 轮询一次；浏览器里如实说「只在看板 app 里可探」。
// web 多出的下半：完全磁盘访问（D20 家族的一半原生窗从不管的事）——server 列出要授权的可执行文件
// （守护 python / claude / node / 壳 app）+ 可复制的绝对路径 + 系统设置深链 + TCC 相关 doctor 行，
// 授权步骤原样写在页面上（系统设置 → 隐私与安全性 → 完全磁盘访问 → + → ⌘⇧G 粘路径）。
import { useEffect, useState } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import "../components/permissions/permissions.css";
import { CapabilityRows } from "../components/permissions/CapabilityRows";
import { RecordingConsentSection } from "../components/permissions/RecordingConsentSection";
import { TelemetryBlock } from "../components/permissions/TelemetryBlock";
import { pickText } from "../components/settings/catalogText";
import { copyText } from "../components/detail/copyText";
import { useI18n } from "../i18n";
import { buildAppUrl, buildSettingsUrl, DEPS_ANCHOR, navigate } from "../route";
import { callShell, hasShellBridge, PANE_IDS, type PermissionStatus } from "../shellBridge";
import { refreshPermissions, refreshSetup, useAppState } from "../store";
import type { DoctorRow, FdaExecutable } from "../types";

type Text = (zh: string, en: string) => string;

export function statusLabel(status: PermissionStatus, text: Text): string {
  switch (status) {
    case "granted": return text("已授权", "Granted");
    case "denied": return text("未授权", "Denied");
    default: return text("未知 / 尚未询问", "Unknown / not asked yet");
  }
}

function CopyPath({ path }: { path: string }) {
  const { text } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <span className="perm-path">
      <code>{path}</code>
      <button type="button" className="zai-detail-copy" onClick={() => void copyText(path).then((ok) => {
        setCopied(ok);
        if (ok) window.setTimeout(() => setCopied(false), 1500);
      })}>
        {copied ? text("已复制", "Copied") : text("复制路径", "Copy path")}
      </button>
    </span>
  );
}

function ExecutableRow({ exe }: { exe: FdaExecutable }) {
  const { text, language } = useI18n();
  return (
    <li className={`perm-exec${exe.exists ? "" : " is-missing"}`} data-role={exe.role}>
      <div className="perm-exec-head">
        <strong>{exe.role}</strong>
        <span className="settings-source-chip">{exe.exists ? text("在", "present") : text("不存在", "missing")}</span>
      </div>
      {exe.path ? <CopyPath path={exe.path} /> : <span className="settings-helper">{text("（未解析到路径）", "(path not resolved)")}</span>}
      {exe.realpath && exe.realpath !== exe.path && (
        <span className="settings-helper">{text("真实路径（TCC 按它记账）：", "Real path (what TCC keys on): ")}<code>{exe.realpath}</code></span>
      )}
      <p className="settings-helper">{pickText(exe.note, language)}</p>
    </li>
  );
}

function DoctorRows({ rows }: { rows: DoctorRow[] }) {
  const { text } = useI18n();
  if (rows.length === 0) return <p className="settings-helper is-ok">{text("doctor 没有 TCC 相关的告警。", "doctor reports no TCC-related issues.")}</p>;
  return (
    <ul className="settings-list">
      {rows.map((row) => (
        <li key={row.name} className="settings-list-row" data-status={row.status}>
          <span className="settings-list-title"><span className={`chip chip-${row.status === "FAIL" ? "danger" : row.status === "WARN" ? "warning" : "success"}`}>{row.status}</span> {row.name}</span>
          <p className="settings-list-desc">{row.detail}</p>
          {row.fix && <p className="settings-helper">{text("修法：", "Fix: ")}{row.fix}</p>}
        </li>
      ))}
    </ul>
  );
}

/** 桥在场：2 s 轮询一次探针（原生 PermissionsModel.startPolling 的节拍——用户正在系统设置里翻开关） */
export function usePermissionPolling(present: boolean) {
  useEffect(() => {
    if (!present) return undefined;
    const tick = () => void callShell("getPermissions").catch(() => undefined);
    tick();
    const timer = window.setInterval(tick, 2000);
    return () => window.clearInterval(timer);
  }, [present]);
}

export function PermissionsPage() {
  const { text } = useI18n();
  const { permissions, pageErrors, setup } = useAppState();
  const present = hasShellBridge();
  const [note, setNote] = useState<string | null>(null);
  // 原生 firstRun：向导还没走完 → 页脚是「完成」（回向导继续）；否则「关闭」（回看板）
  const firstRun = Boolean(setup?.needed);

  useEffect(() => {
    void refreshPermissions();
    void refreshSetup();
  }, []);
  usePermissionPolling(present);

  const openPane = (pane: (typeof PANE_IDS)[number]) => {
    setNote(null);
    void callShell("openPane", { pane }).catch((err) => setNote(err instanceof Error ? err.message : String(err)));
  };

  return (
    <main className="settings-page perm-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>{text("← 返回看板", "← Back to board")}</a>
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("权限体检", "Permissions Checkup")}</h2>
        <button type="button" className="btn" onClick={() => { void refreshPermissions(true); if (present) void callShell("getPermissions").catch(() => undefined); }}>{text("重新探测", "Re-probe")}</button>
      </div>
      <p className="settings-helper">{text("这一页帮你把需要的系统授权一次配齐;状态实时刷新,之后随时可从菜单「权限体检」再打开。", "This page sets up the system permissions in one place; statuses refresh live, and you can reopen it anytime from the menu (\"Permissions Checkup\").")}</p>

      <section className="settings-section" aria-labelledby="perm-recording-title">
        <h3 id="perm-recording-title" className="settings-section-title">{text("屏幕记录", "Screen recording")}</h3>
        <RecordingConsentSection />
      </section>

      <section className="settings-section" aria-labelledby="perm-gui-title">
        <h3 id="perm-gui-title" className="settings-section-title">{text("系统权限", "System permissions")}</h3>
        <CapabilityRows onError={setNote} />
        <TelemetryBlock />
        {note && <p className="settings-warning" role="alert">{note}</p>}
      </section>

      <section className="settings-section" aria-labelledby="perm-fda-title">
        <h3 id="perm-fda-title" className="settings-section-title">{text("完全磁盘访问（后台进程）", "Full Disk Access (background processes)")}</h3>
        {pageErrors.permissions && <p className="settings-error" role="alert">{pageErrors.permissions}</p>}
        {permissions && (
          <>
            <p className={permissions.fda.needed ? "settings-warning" : "settings-helper"}>
              {permissions.fda.needed
                ? text(`数据目录 ${permissions.home} 在 macOS 保护的位置（外置卷 / Documents / Desktop / Downloads）：launchd 会话里的每个可执行文件都要单独授「完全磁盘访问」，终端里跑通不算数。`, `The home ${permissions.home} sits in a location macOS protects (removable volume / Documents / Desktop / Downloads): every executable in a launchd session needs its own Full Disk Access grant — passing in a terminal does not count.`)
                : text(`数据目录 ${permissions.home} 不在 TCC 保护的位置；下面的授权通常不需要，除非 doctor 行另有告警。`, `The home ${permissions.home} is not in a TCC-protected location; the grants below are usually unnecessary unless a doctor row says otherwise.`)}
            </p>
            <ol className="perm-steps">
              <li>{text("系统设置 → 隐私与安全性 → 完全磁盘访问", "System Settings → Privacy & Security → Full Disk Access")}
                {present && <button type="button" className="btn" onClick={() => openPane("full_disk")}>{text("打开", "Open")}</button>}
                {!present && <code className="perm-inline">{permissions.fda.pane}</code>}
              </li>
              <li>{text("点「+」→ 按 ⌘⇧G → 粘贴下面某一行路径 → 打开 → 开关拨开", "Click \"+\" → press ⌘⇧G → paste one of the paths below → Open → flip the switch on")}</li>
              <li>{text("每个可执行文件重复一次；claude 每次更新换了路径要重做。", "Repeat for each executable; redo claude after an update moves its binary.")}</li>
              <li>{text("等下一轮 timer / 雷达自己跑（终端里 kickstart 会借终端的授权，绿了不算）。", "Wait for the next timer / radar pass on its own (a kickstart typed in a terminal borrows the terminal's grant and proves nothing).")}</li>
            </ol>
            <ul className="settings-list perm-execs">
              {permissions.fda.executables.map((exe) => <ExecutableRow key={exe.role} exe={exe} />)}
            </ul>
            <div className="settings-subhead">{text("doctor 的 TCC 相关行", "TCC-related doctor rows")} {permissions.doctor_ran_at && <span className="settings-list-dim">{permissions.doctor_ran_at}</span>}</div>
            {!permissions.doctor_ok && <p className="settings-warning">{text("doctor 没跑成——看诊断页。", "doctor did not run — see Diagnostics.")}</p>}
            <DoctorRows rows={permissions.doctor} />
          </>
        )}
        {!permissions && !pageErrors.permissions && <p className="settings-helper">{text("探测中…", "Probing…")}</p>}
      </section>

      <div className="settings-actions perm-footer">
        <a className="settings-link" href={buildSettingsUrl(window.location.href, DEPS_ANCHOR).toString()}>{text("打开依赖检查", "Open dependency check")}</a>
        <button type="button" className="btn btn-primary" onClick={() => navigate(buildAppUrl(window.location.href, firstRun ? "setup" : "board", null))}>
          {firstRun ? text("完成", "Done") : text("关闭", "Close")}
        </button>
      </div>
    </main>
  );
}
