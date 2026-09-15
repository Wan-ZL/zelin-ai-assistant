// 关于 + 看板 app（壳）区（§26 / §68.6 / §61.6 / §54.4）：原生 Pages.swift AboutView 的 web 版——应用 / 版本 /
// 更新 / 仓库 / 用量报告 各一行（标签逐字镜像）。更新行 = 原生 updateSection + updateStatus 逐态：有新版 →
// 「新版本 v… 可用 — 一键更新」（= POST /api/update/install：提前 kickstart §56 自动部署 agent——merge 即上岗的那条路；
// 这台机器不走自动部署（409）→ 退回原生非 Sparkle 的兜底：打开 release 页手动装）+「上次检查：…」；没新版 →
// 「已是最新」/「最新发布：v…」+「（上次检查：…）」；正在检查… / 检查失败（限流 vs 网络两句）/ 自动检查已关闭 /
// 上次检查没有取得结果 / 尚未检查过。「立即检查」= 原生非 Sparkle 分支的按钮（§26 --force，只问不装）；三道守卫同原生
// （Pages.swift UpdateCheckModel）：正在检查 / 每次尝试后 10 s 冷却（不论成败，不许连点砸 GitHub 接口）/ 自动检查关着
// （about.check_enabled，§68.6 追记——一进页就知道，不必等第一次点击的回执）。看板投影 update_available 变了
// （actd 一个 pass 落了新投影：刚手动检查完 / 日检发现新版 / 部署完成投影消失）→ 丢掉上次回执、重拉 /api/about，
// 行按快照重推（原生 onChange(dashboard.update_available) → reload() 重写全部字段，finish() 的回执不再压着行）。
// 「重新运行初始设置」（删 setup 标记；原生放在设置 → 通用「初始设置向导」行）；壳在场时多出登录时启动（SMAppService，
// 经桥）、全局快速捕获快捷键提示、系统通知权限状态。挂在 ?page=about（左侧导航栏「关于」，AboutPage）。
// §68.6 追记（2026-09-14，issue #309）——「一键更新」不许报假成功：about 快照 add-only 带 deploy_state（server 请求时现读
// state/deploy_state.json，不是看板投影）。上一轮的状态属于 BLOCKING（refused_branch / refused_dirty / blocked_tcc——提前
// kickstart 一轮也是同样的拒绝）→ 按钮禁用 + 一行「更新链路断着：<状态> — <原文>」+ 怎么修；真点下去时 server 也不 kickstart，
// 409 details.reason="deploy_refused" 走同一行文案，**绝不**打开 release 页、**绝不**说「已触发」。kickstart 真发生时回执带
// 上一轮的 deploy_status/deploy_detail/deploy_failed_sha：deferred → 说清会话散了才更新（kickstart 不带 --force，会再延后
// 一轮）、中毒家族（failed / rolled_back / rollback_failed / ci_failed）→ 整族都不许承诺版本会变，记得下 sha 的说「该 sha 在
// main 挪窝之前不会被重试」，没 sha 的（ff-only 失败的分叉 checkout 等）说「很可能同样倒在那里」。状态词与两个集合一律复用
// components/shell/DeployLabel（顶栏与本页永不各判一次）。版本行按 §56.1 追记只显示 tag，`+N` 降级成一句辅助说明。
// 「卸载…」= 原生 confirmUninstall：确认弹窗（正文逐字原生 informativeText：会做的三件事 + 默认保留什么）→
// POST /api/uninstall/terminal 在 Terminal 跑 uninstall.sh（脚本自己再问；server 不删任何东西）；脚本缺席（404）→
// 「找不到卸载脚本」、Terminal 打不开 →「无法打开 Terminal」，两个弹窗都附「请手动在 Terminal 里运行：<server 给的命令>」+「好」。
import { useEffect, useState } from "react";
import { ApiError, postSetupStep, postUninstallTerminal, postUpdateCheck, postUpdateInstall } from "../../api";
import { ForkDialog } from "../board/ForkDialog";
import { ModalDialog } from "../board/ModalDialog";
import { useI18n } from "../../i18n";
import { buildAppUrl, buildSettingsUrl, DEPS_ANCHOR, navigate } from "../../route";
import { hasShellBridge, useShellState } from "../../shellBridge";
import { refreshAbout, setSetup, useAppState } from "../../store";
import type { AboutInfo, Board, DeployState, UpdateCheckResult, UpdateInstallReceipt } from "../../types";
import { aheadNote, releaseVersion } from "../../version";
import { RelativeTime } from "../board/cardChrome";
import { BLOCKING_STATUSES, POISONED_STATUSES, deferredLabel, statusLabel } from "../shell/DeployLabel";
import { LaunchAtLoginRow } from "./LaunchAtLoginRow";
import { errorMessage } from "./useToast";

type Text = (zh: string, en: string) => string;

/** 原生 finish()：每次「立即检查」落地后按钮冷却 10 s——不论成败，永不连点砸 API */
export const CHECK_COOLDOWN_MS = 10_000;

/** 看板投影 update_available（§26）里的 latest；类型是 unknown（旧 server 缺席）——只认 dict 里的字串 */
export function projectedLatest(board: Board | null): string | null {
  const upd = board?.update_available;
  if (!upd || typeof upd !== "object") return null;
  const latest = (upd as { latest?: unknown }).latest;
  return typeof latest === "string" ? latest : null;
}

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

// ----- §68.6 追记（#309）：自动部署的判决 → 「一键更新」的话术（纯函数，判例直接调） ----- #

function pick(state: DeployState | null | undefined, key: string): string {
  const value = state?.[key];
  return typeof value === "string" ? value : "";
}

/** 回执里的上一轮判决（server 在 kickstart 那一刻现读出来的）——比 about 快照新：
 *  页面载入之后才倒下的那一轮，快照里根本没有。缺席（旧 server）→ 空串，回落到快照。 */
function fromReceipt(receipt: UpdateInstallReceipt | null, key: string): string {
  const value = receipt?.[key];
  return typeof value === "string" ? value : "";
}

/** 拒绝原文一行：「更新链路断着：<状态> — <detail>」。一进页（about.deploy_state）与 409
 *  （details.deploy_status / deploy_detail）共用同一句——两条路说的话必须逐字一样。 */
export function refusalLine(status: string, detail: string, text: Text): string {
  const tail = detail ? ` — ${detail}` : "";
  return text(`更新链路断着：${statusLabel(status, text)}${tail}`,
              `The update path is broken: ${statusLabel(status, text)}${tail}`);
}

/** 怎么修：提前跑一轮也是同样的结果，所以先修上面那件事（stale 状态的出口也在这句里——
 *  刚把工作树清干净的人不必等 10 分钟，`--force` 立刻生效）。 */
export function refusalFix(text: Text): string {
  return text("提前跑一轮也是同样的拒绝。先把上面这件事修好，然后等下一轮自动部署（每 10 分钟一轮），或在 repo 里跑 bash scripts/auto-deploy.sh --force。",
              "Installing now hits the same refusal. Fix what that line names, then wait for the next auto-deploy round (every 10 min) or run bash scripts/auto-deploy.sh --force in the repo.");
}

/** 一进页就知道的闸门：上一轮的状态 kickstart 清不掉 → 按钮禁用 + 上面两句。其余 → null。 */
export function deployGate(state: DeployState | null | undefined, text: Text): { line: string; fix: string } | null {
  const status = pick(state, "status");
  if (!status || !BLOCKING_STATUSES.has(status)) return null;
  return { line: refusalLine(status, pick(state, "detail"), text), fix: refusalFix(text) };
}

/** 409 是不是「这一轮必然被拒」（§68.6 追记 #309 的 details.reason）——不是则回 null，
 *  旧 server 的 409（agent 未加载）照旧走 release 页兜底。 */
export function refusalDetails(err: unknown): { status: string; detail: string } | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const details = err.details && typeof err.details === "object" ? err.details as Record<string, unknown> : {};
  if (details.reason !== "deploy_refused") return null;
  return {
    status: typeof details.deploy_status === "string" ? details.deploy_status : "",
    detail: typeof details.deploy_detail === "string" ? details.deploy_detail : "",
  };
}

/** kickstart 真发生了之后那句话：能不能承诺「几分钟后版本会变」由**上一轮**的状态决定。 */
export function installNote(receipt: UpdateInstallReceipt | null, state: DeployState | null | undefined,
                            now: number, text: Text): string {
  const triggered = text("已触发自动部署——几分钟后这里的版本会变；部署后 doctor 变红会自动回滚。",
                         "Auto-deploy triggered — the version here changes in a few minutes; a red doctor after deploy rolls back automatically.");
  const status = fromReceipt(receipt, "deploy_status");
  if (!status || status === "deployed" || status === "up_to_date") return triggered;
  const label = statusLabel(status, text);
  if (status === "deferred") {
    // kickstart 不带 --force，会话闸门（§56.3）原样再判一次——会话还在就再延后一轮
    const waiting = deferredLabel(state ?? {}, now, text).label;
    return text(`已触发一轮自动部署，但上一轮还在等：${waiting}。会话散了这里的版本才会变；等不及就在 repo 里跑 bash scripts/auto-deploy.sh --force（它会打断这些会话）。`,
                `Auto-deploy was triggered, but the last round is still waiting: ${waiting}. The version here changes once those sessions end; in a hurry, run bash scripts/auto-deploy.sh --force in the repo (it interrupts them).`);
  }
  if (POISONED_STATUSES.has(status)) {
    // 中毒家族一律不许再承诺「版本会变」——`failed` 只在 scripts/auto-deploy.sh 的五个写点里
    // 有两个记得下 failed_sha；ff-only 失败（分叉的 checkout，正是 #309 那台机器）、卷探针、
    // symbolic-ref 读不动、认不出 GitHub 远端这四个写的是**没有 sha 的 failed**，而分叉是
    // 永久态：靠 sha 在不在来决定说不说谎，等于把同一句假话留给了最该说真话的那一路。
    const sha = fromReceipt(receipt, "deploy_failed_sha") || pick(state, "failed_sha");
    if (sha) {
      return text(`已触发一轮自动部署，但上一轮是「${label}」：${sha.slice(0, 7)} 在 main 挪窝之前不会被重试——要现在重试请在 repo 里跑 bash scripts/auto-deploy.sh --force。`,
                  `Auto-deploy was triggered, but the last round ended "${label}": ${sha.slice(0, 7)} is not retried until main moves — to retry it now, run bash scripts/auto-deploy.sh --force in the repo.`);
    }
    const detail = fromReceipt(receipt, "deploy_detail") || pick(state, "detail");
    const why = detail ? text(`：${detail}`, `: ${detail}`) : "";
    return text(`已触发一轮自动部署，但上一轮是「${label}」${why}——这一轮很可能同样倒在那里，版本变了才算数；先 tail -40 ~/Library/Logs/zelin-ai-assistant/auto-deploy.log 看它为什么倒下。`,
                `Auto-deploy was triggered, but the last round ended "${label}"${why} — this round may well end the same way; only a changed version means it worked. Start with tail -40 ~/Library/Logs/zelin-ai-assistant/auto-deploy.log.`);
  }
  return `${triggered}${text(`（上一轮：${label}。）`, ` (last round: ${label}.)`)}`;
}

/** 原生 confirmUninstall 的 informativeText 逐字（Pages.swift）：会做的三件事 + 默认保留什么；第三条点名壳 bundle
 *  「Zelin's AI Assistant.app」（uninstall.sh 第 4 步删的就是它；退役的菜单栏 app 已不是产品）。uninstall.sh 在 Terminal 里
 *  逐条再显示一遍并再确认一次——这里只是让用户在点之前就知道。 */
export function UninstallBody() {
  const { text } = useI18n();
  return (
    <div className="dialog-body">
      <p>{text("将执行以下操作（在 Terminal 中逐条显示，动手前再确认一次）：", "What will happen (each step shown in Terminal, with one final confirmation there):")}</p>
      <ul className="dialog-list">
        <li>{text("停止并移除全部后台服务（AI 派发、屏幕录制、雷达、定时任务）", "Stop and remove every background service (AI dispatch, screen recording, radars, scheduled jobs)")}</li>
        <li>{text("从 crontab 移除本产品的行（你的其他行原样保留）", "Remove this product's lines from your crontab (all your other lines kept)")}</li>
        <li>{text("退出看板 app，删除 /Applications 里的 Zelin's AI Assistant.app 与系统级管线副本", "Quit the board app, delete Zelin's AI Assistant.app in /Applications and the system-level pipeline copy")}</li>
      </ul>
      <p>{text("默认保留：任务历史（state/）、API 密钥、Obsidian vault、屏幕录像——每一项都会附上删除命令。", "Kept by default: task history (state/), API keys, your Obsidian vault, screen recordings — each listed with its removal command.")}</p>
    </div>
  );
}

export function AboutSection() {
  const { text } = useI18n();
  const { about, board, pageErrors } = useAppState();
  const shell = useShellState();
  const present = hasShellBridge();
  const [last, setLast] = useState<UpdateCheckResult | null>(null);
  const [busy, setBusy] = useState<"check" | "install" | "setup" | "uninstall" | null>(null);
  const [cooldownUntil, setCooldownUntil] = useState(0);   // 0 = 没在冷却
  const [note, setNote] = useState<string | null>(null);
  const [uninstall, setUninstall] = useState<"none" | "confirm" | { title: string; command: string; extra: string | null }>("none");
  const latestProjected = projectedLatest(board);

  // 原生 onAppear + onChange(dashboard.update_available)：一进页拉一次；actd 一个 pass 落了新投影（比如刚手动检查完）再拉一次。
  // 原生 reload() 会把 latest / url / current / updateAvailable / checkedAt 全部按快照重写——finish() 的回执不再压着行，
  // 所以这里同时丢掉回执（挂载时本来就是 null，无副作用）：页面开着 ≥24 h 日检落了新版、或部署完成投影消失，行都跟着变。
  // store.loadPage 把并发 refresh 合成一个在途请求，挂载与「没快照」两条路不重复打 server。
  useEffect(() => {
    setLast(null);
    void refreshAbout();
  }, [latestProjected]);

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
      setNote(errorMessage(err));
    } finally {
      setBusy(null);
      setCooldownUntil(Date.now() + CHECK_COOLDOWN_MS);   // 原生 finish()：不论成败都冷却，永不连点砸 API
    }
  }

  // 原生 triggerUpdate：新架构 = 提前跑一轮 §56 自动部署；agent 不在（409）→ 原生非 Sparkle 兜底：打开 release 页。
  // §68.6 追记（#309）：server 判定这一轮必然被拒（409 deploy_refused）→ 说拒绝原文，不开 release 页、不说「已触发」。
  async function installNow(url: string | null) {
    setBusy("install");
    setNote(null);
    try {
      const receipt = await postUpdateInstall();
      setNote(installNote(receipt, about?.deploy_state, Date.now(), text));
    } catch (err) {
      const refused = refusalDetails(err);
      if (refused) {
        setNote(`${refusalLine(refused.status, refused.detail, text)} ${refusalFix(text)}`);
        await refreshAbout();   // 拒绝的状态刚被 server 现读出来——让按钮也跟着灰掉
      } else if (err instanceof ApiError && err.status === 409 && url) {
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
  // 原生 .disabled(upd.checking || upd.cooldown || !upd.enabled)
  const checkDisabled = busy === "check" || busy === "install" || cooldownUntil > 0 || view?.enabled === false;
  // §68.6 追记（#309）：上一轮的判决 kickstart 清不掉 → 一进页就禁用「一键更新」并说清原因
  const gate = deployGate(about?.deploy_state, text);
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
              {view.updateAvailable && view.latest && (
                <button type="button" className="btn btn-primary" disabled={busy === "install" || busy === "check" || gate !== null} onClick={() => void installNow(view.url)}>
                  {text(`新版本 v${view.latest} 可用 — 一键更新`, `Update v${view.latest} available — install now`)}
                </button>
              )}
              <UpdateStatus view={view} checking={busy === "check"} />
              {gate && (
                <span className="settings-update-line is-warning" role="status">
                  <span>{gate.line}</span>
                  <span className="settings-helper">{gate.fix}</span>
                </span>
              )}
              <span className="settings-actions">
                <button type="button" className="btn" title={text("检查更新", "Check for updates")} disabled={checkDisabled} onClick={() => void checkNow()}>{text("立即检查", "Check now")}</button>
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
        <a className="settings-link" href={buildSettingsUrl(window.location.href, DEPS_ANCHOR).toString()}>{text("依赖检查", "Dependencies")}</a>
        <a className="settings-link" href={buildAppUrl(window.location.href, "permissions", null).toString()}>{text("权限体检", "Permissions checkup")}</a>
      </div>
      <p className="settings-helper">
        {text("owner 机器上合并即自动部署（§56）：「一键更新」只是把下一轮自动部署提前——部署后 doctor 变红会自动回滚；没有自动部署的机器会打开 release 页手动装。设置与任务数据都在本机，升级后原样保留。", "The owner machine auto-deploys on merge (§56): “install now” only brings the next auto-deploy round forward — a red doctor after deploy rolls back automatically; machines without auto-deploy get the release page instead. Settings and task data stay on this Mac across upgrades.")}
      </p>
      {present && shell && (
        <>
          <div className="settings-subhead">{text("看板 app（壳）", "Board app (shell)")}</div>
          <LaunchAtLoginRow id="launch-at-login-about" helper={text("没有它，开机后不会有系统通知、字幕与录制也不会自启。", "Without it there are no system notifications after a reboot, and captions / recording do not auto-start.")} />
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
          body={<UninstallBody />}
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
