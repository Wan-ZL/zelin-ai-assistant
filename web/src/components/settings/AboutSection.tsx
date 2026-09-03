// 关于 + 看板 app（壳）区（§26 / §68.6 / §61.6）：版本、repo / home 路径、更新状态一行
// （新版本可下载 / 已是最新 + 上次检查 / 尚未检查 / 检查失败）+「立即检查」（§26 --force，
// 只打开 release 页，绝不自动下载）+「重新运行初始设置」（删 setup 标记）；壳在场时多出
// 登录时启动（SMAppService，经桥）、全局快速捕获快捷键提示、系统通知权限状态。
import { useEffect, useState } from "react";
import { postSetupStep, postUpdateCheck } from "../../api";
import { useI18n } from "../../i18n";
import { buildAppUrl, navigate } from "../../route";
import { callShell, hasShellBridge, useShellState } from "../../shellBridge";
import { refreshAbout, setSetup, useAppState } from "../../store";
import type { AboutInfo, UpdateCheckResult } from "../../types";
import { RelativeTime } from "../board/cardChrome";
import { errorMessage } from "./useToast";

type Text = (zh: string, en: string) => string;

/** 更新状态一行（原生关于页常驻行的四态） */
export function updateLine(about: AboutInfo, last: UpdateCheckResult | null, text: Text): { label: string; url: string | null; tone: "ok" | "warning" | "" } {
  if (last && !last.ok) return { label: text("检查失败：", "Check failed: ") + (last.error ?? ""), url: null, tone: "warning" };
  if (last && last.ok && last.enabled === false) return { label: text("自动检查已关闭（通用 → 自动检查新版本）", "Update check is off (General → Check for updates)"), url: null, tone: "" };
  const latest = (last?.ok && last.latest) || about.update_available?.latest || null;
  const url = (last?.ok && last.url) || about.update_available?.url || null;
  if (about.update_available || (last?.ok && last.update_available)) {
    return { label: text(`新版本 v${latest} 可用`, `Version v${latest} is available`), url, tone: "warning" };
  }
  const checked = last?.checked_at ?? about.update_check?.checked_at ?? null;
  if (!checked) return { label: text("尚未检查过", "Never checked"), url: null, tone: "" };
  return { label: text("已是最新", "Up to date"), url: null, tone: "ok" };
}

export function AboutSection() {
  const { text } = useI18n();
  const { about, pageErrors } = useAppState();
  const shell = useShellState();
  const present = hasShellBridge();
  const [last, setLast] = useState<UpdateCheckResult | null>(null);
  const [busy, setBusy] = useState<"check" | "setup" | "login" | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (!about) void refreshAbout();
  }, [about]);

  async function checkNow() {
    setBusy("check");
    setNote(null);
    try {
      setLast(await postUpdateCheck());
      await refreshAbout();
    } catch (err) {
      setNote(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function rerunSetup() {
    setBusy("setup");
    setNote(null);
    try {
      const receipt = await postSetupStep("reset");
      setSetup(receipt.setup);
      navigate(buildAppUrl(window.location.href, "setup", null));
    } catch (err) {
      setNote(errorMessage(err));
      setBusy(null);
    }
  }

  async function toggleLogin(on: boolean) {
    setBusy("login");
    setNote(null);
    try {
      await callShell("setLaunchAtLogin", { on });
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  const line = about ? updateLine(about, last, text) : null;
  const checkedAt = last?.checked_at ?? about?.update_check?.checked_at ?? null;

  return (
    <section className="settings-section" id="settings-about" aria-labelledby="settings-about-title">
      <h3 id="settings-about-title" className="settings-section-title">{text("关于 / 看板 app", "About / Board app")}</h3>
      {pageErrors.about && <p className="settings-error" role="alert">{pageErrors.about}</p>}
      {about && (
        <dl className="settings-meta">
          <div><dt>{text("版本", "Version")}</dt><dd><code>{about.version}</code></dd></div>
          <div><dt>{text("数据目录", "Home")}</dt><dd><code>{about.home}</code></dd></div>
          <div><dt>{text("代码仓库", "Repo")}</dt><dd><code>{about.repo}</code></dd></div>
          <div>
            <dt>{text("更新", "Updates")}</dt>
            <dd>
              {line && <span className={`settings-update-line${line.tone ? ` is-${line.tone}` : ""}`}>{line.label}</span>}
              {line?.url && <a className="settings-link" href={line.url} target="_blank" rel="noreferrer">{text("打开 release 页", "Open the release page")}</a>}
              {checkedAt && <RelativeTime iso={checkedAt} prefix={text(" · 上次检查 ", " · last checked ")} />}
            </dd>
          </div>
        </dl>
      )}
      <div className="settings-actions">
        <button type="button" className="btn" disabled={busy !== null} onClick={() => void checkNow()}>
          {busy === "check" ? text("检查中…", "Checking…") : text("立即检查更新", "Check for updates now")}
        </button>
        <button type="button" className="btn" disabled={busy !== null} onClick={() => void rerunSetup()}>
          {text("重新运行初始设置", "Re-run the setup wizard")}
        </button>
        <a className="settings-link" href={buildAppUrl(window.location.href, "diagnostics", null).toString()}>{text("诊断", "Diagnostics")}</a>
        <a className="settings-link" href={buildAppUrl(window.location.href, "permissions", null).toString()}>{text("权限体检", "Permissions checkup")}</a>
      </div>
      <p className="settings-helper">
        {text("owner 机器上合并即自动部署（§56）：这里的检查只告知是否有新版并给 release 页链接，绝不自动下载执行。设置与任务数据都在本机，升级后原样保留。", "The owner machine auto-deploys on merge (§56): this check only tells you whether a newer release exists and links to it — nothing is downloaded or executed automatically. Settings and task data stay on this Mac across upgrades.")}
      </p>
      {present && shell && (
        <>
          <div className="settings-subhead">{text("看板 app（壳）", "Board app (shell)")}</div>
          <div className="settings-field is-bool">
            <div className="settings-field-head">
              <label className="settings-knob-label" htmlFor="launch-at-login">{text("登录时启动", "Launch at login")}</label>
            </div>
            <div className="settings-knob-controls">
              <input id="launch-at-login" type="checkbox" role="switch" className="settings-switch" checked={shell.launch_at_login} disabled={busy !== null} onChange={(e) => void toggleLogin(e.target.checked)} />
              <span className="settings-helper">{text("没有它，开机后不会有系统通知、字幕与录制也不会自启。", "Without it there are no system notifications after a reboot, and captions / recording do not auto-start.")}</span>
            </div>
          </div>
          <p className="settings-helper">
            {text(`全局快速捕获：${shell.hotkey} 随时唤起看板并聚焦提案输入框。`, `Global quick capture: ${shell.hotkey} brings the board up and focuses the proposal composer.`)}
            {" "}
            {text("系统通知权限：", "Notification permission: ")}
            <strong>{shell.permissions.notifications}</strong>
            {" · "}
            <a className="settings-link" href={buildAppUrl(window.location.href, "permissions", null).toString()}>{text("权限体检", "Permissions checkup")}</a>
          </p>
        </>
      )}
      {note && <p className="settings-warning" role="alert">{note}</p>}
    </section>
  );
}
