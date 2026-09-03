// 权限体检页（§15 v0.13 权限体检窗 → §68.3 web 版；?page=permissions）。两半拼一页：
//   1. GUI 四项（屏幕录制 / 麦克风 / 通知 / 笔记库 Documents）——真相只有壳知道（TCC 探针是原生 API）：桥在场时
//      读 shell.permissions、按钮 = requestPermission / openPane；浏览器里如实说「只在看板 app 里可探」。
//      笔记库行 = 原生 vaultRow：授权落在壳的 bundle 身份上，vault-sync-helper 从 cron 里永远复用（§68.13）；
//      被拒后的「打开系统设置」深链「文件与文件夹」面板。
//   2. 完全磁盘访问（D20 家族的一半原生窗从不管的事）：server 列出要授权的可执行文件（守护
//      python / claude / node / 壳 app）+ 可复制的绝对路径 + 系统设置深链 + TCC 相关 doctor 行。
// 授权步骤原样写在页面上（系统设置 → 隐私与安全性 → 完全磁盘访问 → + → ⌘⇧G 粘路径）。
import { useEffect, useState } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { pickText } from "../components/settings/catalogText";
import { copyText } from "../components/detail/copyText";
import { useI18n } from "../i18n";
import { buildAppUrl } from "../route";
import { callShell, hasShellBridge, PANE_IDS, PERMISSION_KINDS, useShellState, type PermissionStatus } from "../shellBridge";
import { refreshPermissions, useAppState } from "../store";
import type { DoctorRow, FdaExecutable } from "../types";

type Text = (zh: string, en: string) => string;

export function statusLabel(status: PermissionStatus, text: Text): string {
  switch (status) {
    case "granted": return text("已授权", "Granted");
    case "denied": return text("未授权", "Denied");
    default: return text("未知 / 尚未询问", "Unknown / not asked yet");
  }
}

/** 每项授权的系统设置面板（原生：屏幕 / 麦克风 / 通知各自的面板；笔记库被拒后 = 文件与文件夹） */
export function paneFor(kind: string): (typeof PANE_IDS)[number] {
  if (kind === "vault") return "files_folders";
  return (PANE_IDS as readonly string[]).includes(kind) ? (kind as (typeof PANE_IDS)[number]) : "full_disk";
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

export function PermissionsPage() {
  const { text } = useI18n();
  const { permissions, pageErrors } = useAppState();
  const shell = useShellState();
  const present = hasShellBridge();
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    void refreshPermissions();
  }, []);

  // 桥在场：2 s 轮询一次探针（原生 PermissionsModel.startPolling 的节拍——用户正在系统设置里翻开关）
  useEffect(() => {
    if (!present) return undefined;
    const tick = () => void callShell("getPermissions").catch(() => undefined);
    tick();
    const timer = window.setInterval(tick, 2000);
    return () => window.clearInterval(timer);
  }, [present]);

  const act = (method: "requestPermission" | "openPane", args: Record<string, unknown>) => {
    setNote(null);
    void callShell(method, args).catch((err) => setNote(err instanceof Error ? err.message : String(err)));
  };

  const kindLabel = (kind: string) => {
    if (kind === "screen") return text("屏幕录制", "Screen Recording");
    if (kind === "microphone") return text("麦克风", "Microphone");
    if (kind === "vault") return text("笔记库访问（Documents）", "Notes vault access (Documents)"); // 原生 vaultRow 逐字
    return text("通知", "Notifications");
  };
  const kindWhy = (kind: string) => {
    if (kind === "screen") return text("录制引擎读屏幕与系统声音都靠它；没有它录制一直「未在录制」。", "The recording engine reads the screen and system audio through it; without it recording stays \"Not recording\".");
    if (kind === "microphone") return text("实时字幕的麦克风转写。", "Live-captions microphone transcription.");
    if (kind === "vault") return text("授权一次，后台管线就永远经由 App 的稳定身份读写 Obsidian 笔记库——此后 claude/python 升级不会再弹任何权限窗口。", "Grant once and the background pipeline reaches your Obsidian vault through the app's stable identity forever — claude/python updates can never trigger new permission prompts again.");
    return text("卡片进待验收 / 引擎自愈这类系统通知由看板 app 投递；没有它通知静默丢弃。", "System notifications (card ready for review, engine self-heal) are posted by the board app; without it they are dropped silently.");
  };

  return (
    <main className="settings-page perm-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>{text("← 返回看板", "← Back to board")}</a>
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("权限体检", "Permissions checkup")}</h2>
        <button type="button" className="btn" onClick={() => void refreshPermissions(true)}>{text("重新探测", "Re-probe")}</button>
      </div>

      <section className="settings-section" aria-labelledby="perm-gui-title">
        <h3 id="perm-gui-title" className="settings-section-title">{text("看板 app 的四项授权", "The board app's four grants")}</h3>
        {!present || !shell ? (
          <p className="settings-warning">{text("屏幕录制 / 麦克风 / 通知 / 笔记库访问的真相只有看板 app（壳）自己探得到——请在 app 里打开本页。", "Screen Recording / Microphone / Notifications / Notes vault access can only be probed by the board app itself — open this page inside the app.")}</p>
        ) : (
          <ul className="settings-list">
            {PERMISSION_KINDS.map((kind) => {
              const status = shell.permissions[kind];
              return (
                <li key={kind} className="settings-list-row" data-kind={kind} data-status={status}>
                  <span className="settings-list-title">
                    <span className={`chip chip-${status === "granted" ? "success" : status === "denied" ? "danger" : "warning"}`}>{statusLabel(status, text)}</span> {kindLabel(kind)}
                  </span>
                  <p className="settings-list-desc">{kindWhy(kind)}</p>
                  <span className="settings-list-meta">
                    {status !== "granted" && <button type="button" className="btn btn-primary" onClick={() => act("requestPermission", { kind })}>{text("授权", "Grant")}</button>}
                    <button type="button" className="btn" onClick={() => act("openPane", { pane: paneFor(kind) })}>{text("打开系统设置", "Open System Settings")}</button>
                  </span>
                </li>
              );
            })}
          </ul>
        )}
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
                {present && <button type="button" className="btn" onClick={() => act("openPane", { pane: PANE_IDS[0] })}>{text("打开", "Open")}</button>}
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
    </main>
  );
}
