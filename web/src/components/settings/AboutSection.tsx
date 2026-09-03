// 关于 + 看板 app（壳）区（§26 / §68.6 / §61.6 / §54.4）：原生 Pages.swift AboutView 的 web 版——应用 / 版本 /
// 更新 / 仓库 / 用量报告 各一行（标签逐字镜像），更新状态一行（最新发布 v… / 已是最新 + 上次检查 / 正在检查… /
// 检查失败）+「检查更新」（§26 --force，只打开 release 页，绝不自动下载）+「重新运行初始设置」（删 setup 标记；
// 原生放在设置 → 通用「初始设置向导」行）；壳在场时多出登录时启动（SMAppService，经桥）、全局快速捕获快捷键
// 提示、系统通知权限状态。挂在 ?page=about（左侧导航栏「关于」，AboutPage）。「卸载…」= 原生 confirmUninstall：
// 确认弹窗 → POST /api/uninstall/terminal 在 Terminal 跑 uninstall.sh（脚本自己再问；server 不删任何东西）；
// 脚本缺席 / Terminal 打不开各有一句 + 手动命令。「一键更新」（Sparkle 式安装）没有 web 落点（§68.14）。
import { useEffect, useState } from "react";
import { ApiError, postSetupStep, postUninstallTerminal, postUpdateCheck } from "../../api";
import { ForkDialog } from "../board/ForkDialog";
import { ModalDialog } from "../board/ModalDialog";
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
  if (last && last.ok && last.enabled === false) return { label: text("自动检查新版本已关闭——到「设置」页可重新开启。", "Automatic update checks are off — re-enable them on the Settings page."), url: null, tone: "" };
  const latest = (last?.ok && last.latest) || about.update_available?.latest || null;
  const url = (last?.ok && last.url) || about.update_available?.url || null;
  if (about.update_available || (last?.ok && last.update_available)) {
    return { label: text(`最新发布：v${latest}`, `Latest release: v${latest}`), url, tone: "warning" };
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
  const [busy, setBusy] = useState<"check" | "setup" | "login" | "uninstall" | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [uninstall, setUninstall] = useState<"none" | "confirm" | { title: string; body: string }>("none");

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

  // 原生 confirmUninstall → 在 Terminal 中卸载…：404 = 找不到卸载脚本；open 失败 = 无法打开 Terminal + 手动命令
  async function uninstallInTerminal() {
    setBusy("uninstall");
    try {
      await postUninstallTerminal();
      setUninstall("none");
    } catch (err) {
      const manual = "cd <repo> && bash uninstall.sh";
      if (err instanceof ApiError && err.status === 404) {
        setUninstall({ title: text("找不到卸载脚本", "Uninstall script not found"), body: text(`请手动在 Terminal 里运行：${manual}`, `Run this in Terminal yourself: ${manual}`) });
      } else {
        setUninstall({ title: text("无法打开 Terminal", "Could not open Terminal"), body: text(`请手动在 Terminal 里运行：${manual}`, `Run this in Terminal yourself: ${manual}`) + ` (${errorMessage(err)})` });
      }
    } finally {
      setBusy(null);
    }
  }

  const line = about ? updateLine(about, last, text) : null;
  const checkedAt = last?.checked_at ?? about?.update_check?.checked_at ?? null;

  return (
    <section className="settings-section" id="settings-about" aria-labelledby="settings-about-title">
      <h3 id="settings-about-title" className="settings-section-title">{text("关于", "About")}</h3>
      {pageErrors.about && <p className="settings-error" role="alert">{pageErrors.about}</p>}
      {about && (
        <dl className="settings-meta">
          <div><dt>{text("应用", "App")}</dt><dd>Zelin's AI Assistant</dd></div>
          <div><dt>{text("版本", "Version")}</dt><dd><code>{about.version}</code></dd></div>
          <div>
            <dt>{text("更新", "Update")}</dt>
            <dd>
              {busy === "check"
                ? <span className="settings-update-line">{text("正在检查…", "Checking…")}</span>
                : line && <span className={`settings-update-line${line.tone ? ` is-${line.tone}` : ""}`}>{line.label}</span>}
              {line?.url && <a className="settings-link" href={line.url} target="_blank" rel="noreferrer">{text("打开 release 页", "Open the release page")}</a>}
              {checkedAt && <RelativeTime iso={checkedAt} prefix={text("（上次检查：", " (last checked: ")} className="settings-update-checked" />}
              {checkedAt && <span className="settings-update-checked">{text("）", ")")}</span>}
            </dd>
          </div>
          <div><dt>{text("仓库", "Repo")}</dt><dd><code>{about.repo}</code></dd></div>
          <div><dt>{text("数据目录", "Home")}</dt><dd><code>{about.home}</code></dd></div>
          <div>
            <dt>{text("用量报告", "Usage report")}</dt>
            <dd>
              <code>python -m act.report</code>
              <span className="settings-helper">{text("在 repo 目录下运行，查看功能使用频率与健康信号。", "Run in the repo directory to see feature usage and health signals.")}</span>
            </dd>
          </div>
          <div>
            <dt>{text("卸载", "Uninstall")}</dt>
            <dd>
              <button type="button" className="btn btn-danger btn-quiet" disabled={busy !== null} onClick={() => setUninstall("confirm")}>{text("卸载…", "Uninstall…")}</button>
              <span className="settings-helper">{text("停止全部后台服务并移除本产品；任务历史与密钥默认保留。", "Stops every background service and removes the product; task history and keys are kept by default.")}</span>
            </dd>
          </div>
        </dl>
      )}
      <div className="settings-actions">
        <button type="button" className="btn" disabled={busy !== null} onClick={() => void checkNow()}>
          {text("检查更新", "Check for updates")}
        </button>
        <button type="button" className="btn" disabled={busy !== null} onClick={() => void rerunSetup()}>
          {text("重新运行初始设置", "Re-run setup")}
        </button>
        <a className="settings-link" href={buildAppUrl(window.location.href, "deps", null).toString()}>{text("依赖检查", "Dependencies")}</a>
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
      {uninstall === "confirm" && (
        <ForkDialog
          title={text("卸载 Zelin's AI Assistant？", "Uninstall Zelin's AI Assistant?")}
          body={text("会在 Terminal 里运行 uninstall.sh：停止全部后台服务并移除本产品；脚本会再问一次，任务历史与密钥默认保留。", "Runs uninstall.sh in Terminal: stops every background service and removes the product; the script asks again, task history and keys are kept by default.")}
          choices={[{ label: text("在 Terminal 中卸载…", "Uninstall in Terminal…"), isDanger: true, onPick: () => void uninstallInTerminal() }]}
          onCancel={() => setUninstall("none")}
        />
      )}
      {typeof uninstall === "object" && (
        <ModalDialog title={uninstall.title} onCancel={() => setUninstall("none")}>
          <p className="dialog-body">{uninstall.body}</p>
          <div className="dialog-actions">
            <button type="button" className="btn btn-primary" onClick={() => setUninstall("none")}>{text("好", "OK")}</button>
          </div>
        </ModalDialog>
      )}
    </section>
  );
}
