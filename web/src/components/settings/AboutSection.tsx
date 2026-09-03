// 关于 + 看板 app（壳）区（§26 / §68.6 / §61.6 / §54.4）：原生 Pages.swift AboutView 的 web 版——应用 / 版本 /
// 更新 / 仓库 / 用量报告 各一行（标签逐字镜像）。更新行 = 原生 updateSection + updateStatus 逐态：有新版 →
// 「新版本 v… 可用 — 一键更新」（= POST /api/update/install：提前 kickstart §56 自动部署 agent——merge 即上岗的那条路；
// 这台机器不走自动部署（409）→ 退回原生非 Sparkle 的兜底：打开 release 页手动装）+「上次检查：…」；没新版 →
// 「已是最新」/「最新发布：v…」+「（上次检查：…）」；正在检查… / 检查失败（限流 vs 网络两句）/ 自动检查已关闭 /
// 上次检查没有取得结果 / 尚未检查过。「立即检查」= 原生非 Sparkle 分支的按钮（§26 --force，只问不装）。
// 「重新运行初始设置」（删 setup 标记；原生放在设置 → 通用「初始设置向导」行）；壳在场时多出登录时启动（SMAppService，
// 经桥）、全局快速捕获快捷键提示、系统通知权限状态。挂在 ?page=about（左侧导航栏「关于」，AboutPage）。
// 「卸载…」= 原生 confirmUninstall：确认弹窗 → POST /api/uninstall/terminal 在 Terminal 跑 uninstall.sh（脚本自己再问；
// server 不删任何东西）；脚本缺席（404）→「找不到卸载脚本」、Terminal 打不开 →「无法打开 Terminal」，两个弹窗都附
// 「请手动在 Terminal 里运行：<server 给的命令>」+「好」。
import { useEffect, useState } from "react";
import { ApiError, postSetupStep, postUninstallTerminal, postUpdateCheck, postUpdateInstall } from "../../api";
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

/** 原生 UpdateCheckModel 的字段（reload 读 about 快照；checkNow 的回执优先） */
export interface UpdateView {
  current: string;
  latest: string | null;
  url: string | null;
  updateAvailable: boolean;
  checkedAt: string | null;
  failed: boolean;
  errorKind: string | null;
  enabled: boolean;
}

export function updateView(about: AboutInfo, last: UpdateCheckResult | null): UpdateView {
  const base: UpdateView = {
    current: about.version,
    latest: about.update_available?.latest ?? about.update_check?.latest ?? null,
    url: about.update_available?.url ?? about.update_check?.url ?? null,
    updateAvailable: Boolean(about.update_available),
    checkedAt: about.update_check?.checked_at ?? null,
    failed: false, errorKind: null, enabled: true,
  };
  if (!last) return base;
  return {
    current: last.current || base.current,
    latest: last.latest ?? null,
    url: last.url || base.url,
    updateAvailable: Boolean(last.update_available),
    checkedAt: last.checked_at ?? null,
    failed: !last.ok,
    errorKind: last.ok ? null : (last.error ?? null),
    enabled: last.enabled ?? true,
  };
}

/** 原生 updateStatus 的前几态（纯文案；带相对时间的两态由 UpdateStatus 组件渲染） */
export function updateStatusText(v: UpdateView, checking: boolean, text: Text): string | null {
  if (checking) return text("正在检查…", "Checking…");
  if (v.failed) {
    if (v.errorKind === "rate_limited") return text("检查失败——GitHub 接口暂时限流，约一小时内自动恢复；你的网络没有问题。", "Check failed — GitHub API rate limit hit; it resets within the hour. Your network is fine.");
    return text("检查失败——网络不可用，稍后再试。", "Check failed — network unavailable; try again later.");
  }
  if (!v.enabled) return text("自动检查新版本已关闭——到「设置」页可重新开启。", "Automatic update checks are off — re-enable them on the Settings page.");
  return null;
}

function UpdateStatus({ view, checking }: { view: UpdateView; checking: boolean }) {
  const { text } = useI18n();
  const plain = updateStatusText(view, checking, text);
  const cls = `settings-update-line${view.failed ? " is-warning" : ""}`;
  if (plain) return <span className={cls}>{plain}</span>;
  if (view.updateAvailable) {
    // 按钮已把话说完——只补时间戳
    return view.checkedAt ? <RelativeTime iso={view.checkedAt} prefix={text("上次检查：", "Last checked: ")} className="settings-update-checked" /> : null;
  }
  if (view.latest) {
    const same = view.latest === view.current;
    return (
      <span className={`${cls}${same ? " is-ok" : ""}`}>
        <span>{same ? text("已是最新", "Up to date") : text(`最新发布：v${view.latest}`, `Latest release: v${view.latest}`)}</span>
        {view.checkedAt && <RelativeTime iso={view.checkedAt} prefix={text("（上次检查：", " (last checked: ")} suffix={text("）", ")")} className="settings-update-checked" />}
      </span>
    );
  }
  if (view.checkedAt) {
    return <span className={cls}>{text("上次检查没有取得结果（", "The last check got no answer (")}<RelativeTime iso={view.checkedAt} />{text("）——点「立即检查」重试。", ") — hit Check now to retry.")}</span>;
  }
  return <span className={cls}>{text("尚未检查过。", "Not checked yet.")}</span>;
}

export function AboutSection() {
  const { text } = useI18n();
  const { about, pageErrors } = useAppState();
  const shell = useShellState();
  const present = hasShellBridge();
  const [last, setLast] = useState<UpdateCheckResult | null>(null);
  const [busy, setBusy] = useState<"check" | "install" | "setup" | "login" | "uninstall" | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [uninstall, setUninstall] = useState<"none" | "confirm" | { title: string; command: string; extra: string | null }>("none");

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

  // 原生 triggerUpdate：新架构 = 提前跑一轮 §56 自动部署；agent 不在（409）→ 原生非 Sparkle 兜底：打开 release 页
  async function installNow(url: string | null) {
    setBusy("install");
    setNote(null);
    try {
      await postUpdateInstall();
      setNote(text("已触发自动部署——几分钟后这里的版本会变；部署后 doctor 变红会自动回滚。", "Auto-deploy triggered — the version here changes in a few minutes; a red doctor after deploy rolls back automatically."));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && url) {
        window.open(url, "_blank", "noopener");
        setNote(text("这台机器不走自动部署——已打开 release 页，请手动下载安装。", "This machine is not auto-deployed — the release page is open; install it by hand."));
      } else {
        setNote(errorMessage(err));
      }
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

  // 原生 confirmUninstall → 在 Terminal 中卸载…：404 = 找不到卸载脚本；open 失败 = 无法打开 Terminal；两者都附手动命令
  async function uninstallInTerminal() {
    setBusy("uninstall");
    try {
      await postUninstallTerminal();
      setUninstall("none");
    } catch (err) {
      const details = err instanceof ApiError && err.details && typeof err.details === "object" ? err.details as Record<string, unknown> : {};
      const command = typeof details.command === "string" ? details.command : "cd <repo> && bash uninstall.sh";
      if (err instanceof ApiError && err.status === 404) {
        setUninstall({ title: text("找不到卸载脚本", "Uninstall script not found"), command, extra: typeof details.path === "string" ? text(`预期位置：${details.path}`, `Expected at: ${details.path}`) : null });
      } else {
        setUninstall({ title: text("无法打开 Terminal", "Could not open Terminal"), command, extra: errorMessage(err) });
      }
    } finally {
      setBusy(null);
    }
  }

  const view = about ? updateView(about, last) : null;

  return (
    <section className="settings-section" id="settings-about" aria-labelledby="settings-about-title">
      <h3 id="settings-about-title" className="settings-section-title">{text("关于", "About")}</h3>
      {pageErrors.about && <p className="settings-error" role="alert">{pageErrors.about}</p>}
      {about && view && (
        <dl className="settings-meta">
          <div><dt>{text("应用", "App")}</dt><dd>Zelin's AI Assistant</dd></div>
          <div><dt>{text("版本", "Version")}</dt><dd><code>{about.version}</code></dd></div>
          <div>
            <dt>{text("更新", "Update")}</dt>
            <dd className="settings-update">
              {view.updateAvailable && view.latest && (
                <button type="button" className="btn btn-primary" disabled={busy === "install" || busy === "check"} onClick={() => void installNow(view.url)}>
                  {text(`新版本 v${view.latest} 可用 — 一键更新`, `Update v${view.latest} available — install now`)}
                </button>
              )}
              <UpdateStatus view={view} checking={busy === "check"} />
              <span className="settings-actions">
                <button type="button" className="btn" title={text("检查更新", "Check for updates")} disabled={busy === "check" || busy === "install"} onClick={() => void checkNow()}>{text("立即检查", "Check now")}</button>
                {view.url && <a className="settings-link" href={view.url} target="_blank" rel="noreferrer">{text("打开 release 页", "Open the release page")}</a>}
              </span>
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
        <button type="button" className="btn" disabled={busy !== null} onClick={() => void rerunSetup()}>
          {text("重新运行初始设置", "Re-run setup")}
        </button>
        <a className="settings-link" href={buildAppUrl(window.location.href, "deps", null).toString()}>{text("依赖检查", "Dependencies")}</a>
        <a className="settings-link" href={buildAppUrl(window.location.href, "permissions", null).toString()}>{text("权限体检", "Permissions checkup")}</a>
      </div>
      <p className="settings-helper">
        {text("owner 机器上合并即自动部署（§56）：「一键更新」只是把下一轮自动部署提前——部署后 doctor 变红会自动回滚；没有自动部署的机器会打开 release 页手动装。设置与任务数据都在本机，升级后原样保留。", "The owner machine auto-deploys on merge (§56): “install now” only brings the next auto-deploy round forward — a red doctor after deploy rolls back automatically; machines without auto-deploy get the release page instead. Settings and task data stay on this Mac across upgrades.")}
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
          {uninstall.extra && <p className="dialog-body">{uninstall.extra}</p>}
          <p className="dialog-body">{text(`请手动在 Terminal 里运行：${uninstall.command}`, `Run this in Terminal yourself: ${uninstall.command}`)}</p>
          <div className="dialog-actions">
            <button type="button" className="btn btn-primary" onClick={() => setUninstall("none")}>{text("好", "OK")}</button>
          </div>
        </ModalDialog>
      )}
    </section>
  );
}
