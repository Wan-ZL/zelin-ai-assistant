// 关于区（§26 / §68.6 / §79 / §54.4）：原生 Pages.swift AboutView 的 web 版——应用 / 版本 / 更新 / 仓库 / 数据目录 / 卸载
// 各一行（标签逐字镜像）。更新行 = 原生 updateSection + updateStatus 逐态：有新版 →「新版本 v… 可用 — 立即升级」
// （= POST /api/update/install：§79 升级器 scripts/upgrade.sh——ff-only 到带 tag 的 origin/main + install.sh，永不碰录制引擎；
// 跨大版本 → 409 manual_upgrade、这个 checkout 还没有升级器 → 501，两者都给 server 算好的手动命令 `details.fix` + 复制键）+
// 「上次检查：…」；没新版 →「已是最新」/「最新发布：v…」+「（上次检查：…）」；正在检查… / 检查失败（限流 vs 网络两句）/
// 自动检查已关闭 / 上次检查没有取得结果 / 尚未检查过。「立即检查」= §26 --force（只问不装）；三道守卫同原生
// （Pages.swift UpdateCheckModel）：正在检查 / 每次尝试后 10 s 冷却（不论成败，不许连点砸 GitHub 接口）/ 自动检查关着
// （about.check_enabled，§68.6 追记——一进页就知道，不必等第一次点击的回执）。快照里 update_available.manual_upgrade = true
// （新的大版本）时不给按钮，直接给手动命令。版本行按 §56.1 追记只显示 tag，`+N` 降级成一句辅助说明。
// 「重新运行初始设置」（删 setup 标记）；壳在场时多出登录时启动（SMAppService，经桥）与系统通知权限状态。挂在 ?page=about。
// 「卸载…」（§9 第 10 条默认）= 复制命令对话框：正文说清 uninstall.sh 会做什么、默认保留什么，给一条 `cd <repo> && bash uninstall.sh`
// 和「复制」——页面不替你开 Terminal（v1 的 POST /api/uninstall/terminal 随 Terminal 启动器退役）。
// v2.0.0 退役（全文见 tag full）：§56 自动部署的 kickstart 与 deploy_state 判决话术（#309）、「用量报告 python -m act.report」、
// 全局快速捕获快捷键提示。
import { useEffect, useState } from "react";
import { ApiError, postSetupStep, postUpdateCheck, postUpdateInstall } from "../../api";
import { copyText } from "../../copyText";
import { useI18n } from "../../i18n";
import { buildAppUrl, buildSettingsUrl, DEPS_ANCHOR, navigate } from "../../route";
import { hasShellBridge, useShellState } from "../../shellBridge";
import { refreshAbout, setSetup, useAppState } from "../../store";
import type { AboutInfo, UpdateCheckResult, UpdateInstallReceipt } from "../../types";
import { aheadNote, releaseVersion } from "../../version";
import { CopiedAnnouncer, RelativeTime } from "../shell/RelativeTime";
import { ModalDialog } from "../shell/ModalDialog";
import { LaunchAtLoginRow } from "./LaunchAtLoginRow";
import { errorMessage } from "./useToast";

type Text = (zh: string, en: string) => string;

/** 原生 finish()：每次「立即检查」落地后按钮冷却 10 s——不论成败，永不连点砸 API */
export const CHECK_COOLDOWN_MS = 10_000;

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
    // 原生 reload：override → config → on；server 把 effective 值放进 about.check_enabled（旧 server 缺席 = on）
    failed: false, errorKind: null, enabled: about.check_enabled ?? true,
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
    enabled: last.enabled ?? base.enabled,
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

// ----- §79 升级：回执 / 409 / 501 → 一句话 + （需要时）手动命令 ----------------------------------------------- #

/** 升级的结果：一句话 + 可选的手动命令（server 的 `details.fix`，逐字给出、带复制键） */
export interface UpgradeNote {
  tone: "ok" | "warning";
  line: string;
  command: string | null;
}

/** 手动升级命令：server 给的 `fix` 优先；没有就按 §79 过渡条款拼一条（与 server/about.manual_fix 同形） */
export function manualCommand(fix: unknown, latest: string | null): string {
  if (typeof fix === "string" && fix) return fix;
  return `git fetch --tags origin && git checkout ${latest ? `v${latest}` : "v<latest>"} && bash install.sh`;
}

/** POST /api/update/install 成功：升级器已分离起跑——它自己 ff + install.sh，然后重开 app；录制不受影响 */
export function upgradeStartedNote(receipt: UpdateInstallReceipt, text: Text): UpgradeNote {
  const target = receipt.latest ? `v${receipt.latest}` : text("新版本", "the new version");
  return {
    tone: "ok",
    line: text(`已开始升级到 ${target}：完成后 app 会自己重开，录制不会被打断。进度在 ${receipt.log}。`,
               `Upgrading to ${target}: the app reopens by itself when it is done, and recording is never interrupted. Progress: ${receipt.log}.`),
    command: null,
  };
}

/** 409 manual_upgrade / 501 upgrader_missing → 一句原因 + 手动命令；别的错误 → null（调用方说 server 原句） */
export function upgradeRefusal(err: unknown, latest: string | null, text: Text): UpgradeNote | null {
  if (!(err instanceof ApiError)) return null;
  const details = err.details && typeof err.details === "object" ? err.details as Record<string, unknown> : {};
  if (err.status === 409 && details.reason === "manual_upgrade") {
    const to = typeof details.latest === "string" ? details.latest : latest;
    return {
      tone: "warning",
      line: text(`v${to ?? "?"} 是新的大版本：大版本之间从不自动升级，请在 repo 里手动运行：`,
                 `v${to ?? "?"} is a new major version: upgrades never cross a major version by themselves. Run this in the repo:`),
      command: manualCommand(details.fix, to ?? null),
    };
  }
  if (err.status === 501) {
    return {
      tone: "warning",
      line: text("这个安装还没有一键升级器（scripts/upgrade.sh）。请在 repo 里手动运行：",
                 "This install has no one-click upgrader yet (scripts/upgrade.sh). Run this in the repo:"),
      command: manualCommand(details.fix, latest),
    };
  }
  return null;
}

/** 一条可复制的命令（升级 / 卸载对话框共用）：<code> + 「复制」+ 读屏播报 */
function CommandLine({ command }: { command: string }) {
  const { text } = useI18n();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);
  return (
    <span className="settings-command">
      <code>{command}</code>
      <button type="button" className="btn btn-quiet" onClick={() => void copyText(command).then(setCopied)}>
        {copied ? text("已复制", "Copied") : text("复制", "Copy")}
      </button>
      <CopiedAnnouncer copied={copied} />
    </span>
  );
}

/** 卸载命令（about.repo 在场就带上真实路径） */
export function uninstallCommand(about: AboutInfo | null): string {
  return `cd ${about?.repo || "<repo>"} && bash uninstall.sh`;
}

/** 卸载对话框正文：uninstall.sh 会做的事 + 默认保留什么（脚本在 Terminal 里会再逐条显示、再确认一次） */
export function UninstallBody() {
  const { text } = useI18n();
  return (
    <div className="dialog-body">
      <p>{text("在 Terminal 里运行下面这条命令。脚本会逐条显示要做的事，动手前再确认一次：", "Run the command below in Terminal. The script lists each step and asks once more before doing anything:")}</p>
      <ul className="dialog-list">
        <li>{text("移除后台服务（本机 server 的 LaunchAgent）与 crontab 里本产品的那一行（你的其他行原样保留）", "Remove the background service (the local server's LaunchAgent) and this product's crontab line (all your other lines kept)")}</li>
        <li>{text("删除 /Applications 里的 Zelin's AI Assistant.app 与 app 自己的隔离目录", "Delete Zelin's AI Assistant.app in /Applications and the app's own isolated directory")}</li>
        <li>{text("录制引擎默认留着继续录（脚本会打印停止它的命令）", "The recording engine keeps running by default (the script prints the command that stops it)")}</li>
      </ul>
      <p>{text("默认保留：state/（会议纪要与笔记整理记录）、API 密钥、Obsidian vault、屏幕录像——每一项都会附上删除命令。", "Kept by default: state/ (recaps and note-processing records), API keys, your Obsidian vault, screen recordings — each listed with its removal command.")}</p>
    </div>
  );
}

export function AboutSection() {
  const { text } = useI18n();
  const { about, pageErrors } = useAppState();
  const shell = useShellState();
  const present = hasShellBridge();
  const [last, setLast] = useState<UpdateCheckResult | null>(null);
  const [busy, setBusy] = useState<"check" | "install" | "setup" | null>(null);
  const [cooldownUntil, setCooldownUntil] = useState(0);   // 0 = 没在冷却
  const [note, setNote] = useState<UpgradeNote | null>(null);
  const [uninstallOpen, setUninstallOpen] = useState(false);

  // 一进页拉一次（store.loadPage 把并发 refresh 合成一个在途请求）
  useEffect(() => {
    void refreshAbout();
  }, []);

  // 冷却到点自动解锁；期中卸载组件就清 timer
  useEffect(() => {
    if (!cooldownUntil) return;
    const timer = window.setTimeout(() => setCooldownUntil(0), Math.max(0, cooldownUntil - Date.now()));
    return () => window.clearTimeout(timer);
  }, [cooldownUntil]);

  async function checkNow() {
    setBusy("check");
    setNote(null);
    try {
      setLast(await postUpdateCheck());
      await refreshAbout();
    } catch (err) {
      setNote({ tone: "warning", line: errorMessage(err), command: null });
    } finally {
      setBusy(null);
      setCooldownUntil(Date.now() + CHECK_COOLDOWN_MS);   // 原生 finish()：不论成败都冷却，永不连点砸 API
    }
  }

  // §79：分离起 scripts/upgrade.sh；跨大版本 409 / 还没有升级器 501 → 手动命令（绝不打开 release 页让人装别的东西）
  async function installNow(latest: string | null) {
    setBusy("install");
    setNote(null);
    try {
      setNote(upgradeStartedNote(await postUpdateInstall(), text));
    } catch (err) {
      setNote(upgradeRefusal(err, latest, text) ?? { tone: "warning", line: errorMessage(err), command: null });
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
      navigate(buildAppUrl(window.location.href, "setup"));
    } catch (err) {
      setNote({ tone: "warning", line: errorMessage(err), command: null });
      setBusy(null);
    }
  }

  const view = about ? updateView(about, last) : null;
  // 原生 .disabled(upd.checking || upd.cooldown || !upd.enabled)
  const checkDisabled = busy === "check" || busy === "install" || cooldownUntil > 0 || view?.enabled === false;
  const manualOnly = about?.update_available?.manual_upgrade === true;
  const ahead = about ? aheadNote(about.version, text) : null;

  return (
    <section className="settings-section" id="settings-about" aria-labelledby="settings-about-title">
      <h3 id="settings-about-title" className="settings-section-title">{text("关于", "About")}</h3>
      {pageErrors.about && <p className="settings-error" role="alert">{pageErrors.about}</p>}
      {about && view && (
        <dl className="settings-meta">
          <div><dt>{text("应用", "App")}</dt><dd>Zelin's AI Assistant</dd></div>
          <div>
            <dt>{text("版本", "Version")}</dt>
            <dd>
              <code>v{releaseVersion(about.version)}</code>
              {ahead && <span className="settings-helper">{ahead}</span>}
            </dd>
          </div>
          <div>
            <dt>{text("更新", "Update")}</dt>
            <dd className="settings-update">
              {view.updateAvailable && view.latest && !manualOnly && (
                <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={() => void installNow(view.latest)}>
                  {text(`新版本 v${view.latest} 可用 — 立即升级`, `Update v${view.latest} available — upgrade now`)}
                </button>
              )}
              {view.updateAvailable && view.latest && manualOnly && (
                <span className="settings-update-line is-warning" role="status">
                  <span>{text(`v${view.latest} 是新的大版本：大版本之间从不自动升级，请在 repo 里手动运行：`, `v${view.latest} is a new major version: upgrades never cross a major version by themselves. Run this in the repo:`)}</span>
                  <CommandLine command={manualCommand(null, view.latest)} />
                </span>
              )}
              <UpdateStatus view={view} checking={busy === "check"} />
              <span className="settings-actions">
                <button type="button" className="btn" title={text("检查更新", "Check for updates")} disabled={checkDisabled} onClick={() => void checkNow()}>{text("立即检查", "Check now")}</button>
                {view.url && <a className="settings-link" href={view.url} target="_blank" rel="noreferrer">{text("打开 release 页", "Open the release page")}</a>}
              </span>
            </dd>
          </div>
          <div><dt>{text("仓库", "Repo")}</dt><dd><code>{about.repo}</code></dd></div>
          <div><dt>{text("数据目录", "Home")}</dt><dd><code>{about.home}</code></dd></div>
          <div>
            <dt>{text("卸载", "Uninstall")}</dt>
            <dd>
              <button type="button" className="btn btn-danger btn-quiet" disabled={busy !== null} onClick={() => setUninstallOpen(true)}>{text("卸载…", "Uninstall…")}</button>
              <span className="settings-helper">{text("给你一条在 Terminal 里运行的卸载命令；会议纪要、笔记与密钥默认保留。", "Gives you an uninstall command to run in Terminal; recaps, notes and keys are kept by default.")}</span>
            </dd>
          </div>
        </dl>
      )}
      <div className="settings-actions">
        <button type="button" className="btn" disabled={busy !== null} onClick={() => void rerunSetup()}>
          {text("重新运行初始设置", "Re-run setup")}
        </button>
        <a className="settings-link" href={buildSettingsUrl(window.location.href, DEPS_ANCHOR).toString()}>{text("依赖检查", "Dependencies")}</a>
        <a className="settings-link" href={buildAppUrl(window.location.href, "permissions").toString()}>{text("权限体检", "Permissions checkup")}</a>
      </div>
      <p className="settings-helper">
        {text("「立即升级」只快进到 GitHub 上带版本号的 main，再跑一遍 install.sh；本地有改动或分叉时它停下并告诉你手动命令，绝不重置。录制不会被打断，设置与会议纪要都在本机、升级后原样保留。", "\"Upgrade now\" only fast-forwards to a tagged main on GitHub and reruns install.sh; with local changes or a diverged checkout it stops and gives you the manual command, never resets. Recording is never interrupted, and settings and recaps stay on this Mac across upgrades.")}
      </p>
      {present && shell && (
        <>
          <div className="settings-subhead">{text("app（壳）", "App (shell)")}</div>
          <LaunchAtLoginRow id="launch-at-login-about" helper={text("没有它，开机后不会有系统通知，录制也不会自启。", "Without it there are no system notifications after a reboot, and recording does not auto-start.")} />
          <p className="settings-helper">
            {text("系统通知权限：", "Notification permission: ")}
            <strong>{shell.permissions.notifications}</strong>
            {" · "}
            <a className="settings-link" href={buildAppUrl(window.location.href, "permissions").toString()}>{text("权限体检", "Permissions checkup")}</a>
          </p>
        </>
      )}
      {note && (
        <p className={note.tone === "ok" ? "settings-helper is-ok" : "settings-warning"} role={note.tone === "ok" ? "status" : "alert"}>
          <span>{note.line}</span>
          {note.command && <> <CommandLine command={note.command} /></>}
        </p>
      )}
      {uninstallOpen && (
        <ModalDialog title={text("卸载 Zelin's AI Assistant", "Uninstall Zelin's AI Assistant")} onCancel={() => setUninstallOpen(false)}>
          <UninstallBody />
          <p className="dialog-body"><CommandLine command={uninstallCommand(about)} /></p>
          <div className="dialog-actions">
            <button type="button" className="btn btn-primary" onClick={() => setUninstallOpen(false)}>{text("好", "OK")}</button>
          </div>
        </ModalDialog>
      )}
    </section>
  );
}
