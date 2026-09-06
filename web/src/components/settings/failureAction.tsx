// §25 失败目录的「对症一键」（原生 Doctor.swift FailureCatalog.actionLabel / perform 的 web 版）：doctor 行、
// 诊断条、卡片错误行带 failure_id（act/lib/failures.py 的闭集）时给一颗按钮——标签逐字镜像原生
// （安装页 / 去诊断 / 去设置 / 去录制页 / 看进度 / 重启引擎 / 安装 ffmpeg / 去授权… / 一键修复 / 查看修法 / 显示文件），
// 动作落到 web 已有的机制：外链（Claude Code / Node.js / ffmpeg 安装页）、页面深链（依赖检查 / 设置凭证区 /
// 录制页 / 权限体检 / 依赖检查区的 engine.log 尾巴 = 「看进度」）、桥（重启录制引擎 / 系统设置面板；浏览器里退成页面深链）、
// server（POST /api/repair/actd 一键修复、POST /api/reveal {target:"config"} 显示文件）。未知 id → null（不装按钮，原文照旧）。
// cron FDA 的「去授权」是引导式的（原生 CronFDA.beginGrant，§25 / §68.4 追记）：先把 /usr/sbin/cron 放进剪贴板再开面板——
// 这里的 grantCronFda / cronGrantSteps / CronFdaGrantButton 是唯一实现，DepRows 的 cron 行与向导末步 FinaleStep 都从这借。
import { useState } from "react";
import { postRepairActd, postRevealTarget } from "../../api";
import { useI18n } from "../../i18n";
import { buildAppUrl, DEPS_ANCHOR, type AppPage } from "../../route";
import { callShell, hasShellBridge } from "../../shellBridge";
import { refreshHealth } from "../../store";
import { copyText } from "../detail/copyText";
import { errorMessage } from "./useToast";

type Text = (zh: string, en: string) => string;

/** 原生 Doctor.swift CronFDA.cronBinary——FDA 面板 ⌘⇧G 弹层里要粘的那条路径（server/permissions.CRON_BINARY 同一字面量） */
export const CRON_BINARY = "/usr/sbin/cron";

/** 原生 CronFDA.beginGrant：清剪贴板 → 放入 /usr/sbin/cron → 打开「完全磁盘访问」面板。复制失败不挡开面板（原生也不查）；
 *  浏览器里没有面板可开——照样先复制，面板那一步由调用方深链权限体检页（那页印着同一套 ⌘⇧G 步骤 + 可复制的 cron 路径）。 */
export async function grantCronFda(): Promise<void> {
  await copyText(CRON_BINARY);
  if (hasShellBridge()) await callShell("openPane", { pane: "full_disk" });
}

/** 原生 CronFDA.grantSteps（Doctor.swift:331-334 逐字）：失败行下方的行内 click-by-click 步骤 */
export function cronGrantSteps(text: Text): string {
  return text(`点「去授权」会把 ${CRON_BINARY} 复制到剪贴板并打开「完全磁盘访问」面板。然后：点 ➕ → 按 ⌘⇧G → ⌘V 粘贴 → 回车 → 选中 cron → 开启开关。下次定时任务运行（约 30 分钟内）后这一行会自动变绿。`,
    `"Grant…" copies ${CRON_BINARY} to the clipboard and opens the Full Disk Access pane. Then: click ➕ → press ⌘⇧G → ⌘V to paste → Return → select cron → toggle it on. This row turns green after the next scheduled run (within ~30 min).`);
}

/** cron FDA 的「去授权」：壳里一颗按钮 = grantCronFda；浏览器里是权限体检页的 <a>（深链照旧可点、可中键），点击顺手复制路径。
 *  `onError` 收桥的 reject（老壳 / 面板打不开），调用方决定怎么显示。 */
export function CronFdaGrantButton({ className = "btn btn-quiet", onError }: { className?: string; onError?: (message: string) => void }) {
  const { text } = useI18n();
  const label = text("去授权", "Grant…");
  const run = () => void grantCronFda().catch((e) => onError?.(errorMessage(e)));
  if (!hasShellBridge()) {
    // 不 preventDefault：复制是顺手的事，导航照走
    return <a className={className} href={pageHref({ page: "permissions" })} onClick={run}>{label}</a>;
  }
  return <button type="button" className={className} onClick={run}>{label}</button>;
}

/** 原生 FailureCatalog.actionLabel（Doctor.swift:202–220）；null = 无 in-app 动作 */
export function failureActionLabel(id: string | null | undefined, text: Text): string | null {
  switch (id ?? "") {
    case "claude_cli_missing": case "node_missing": return text("安装页", "Install page");
    case "claude_cli_outdated": return text("去诊断", "Open diagnostics");
    case "claude_auth_failed": return text("去设置", "Open Settings");
    case "engine_dead": return text("去录制页", "Open Recording");
    case "engine_npm_download": return text("看进度", "View progress");
    case "engine_crashed": return text("重启引擎", "Restart engine");
    case "engine_ffmpeg_missing": return text("安装 ffmpeg", "Install ffmpeg");
    case "screen_tcc_lost": return text("去授权", "Grant…");
    case "agent_unloaded": case "dashboard_stale": return text("一键修复", "Fix now");
    case "cron_missing": return text("查看修法", "How to fix");
    // 不给「一键修复」：重装 agent 会把同一个瞎解释器再渲一遍，得重跑安装器（原生同注）
    case "interpreter_blind": case "claude_blind": case "deploy_blind_tcc": return text("去诊断", "Open diagnostics");
    case "cron_fda_blocked": return text("去授权", "Grant…");
    case "config_invalid": return text("显示文件", "Reveal file");
    default: return null;
  }
}

const EXTERNAL: Record<string, string> = {
  claude_cli_missing: "https://claude.com/claude-code",
  node_missing: "https://nodejs.org",
  engine_ffmpeg_missing: "https://ffmpeg.org/download.html",
};

interface PageTarget { page: AppPage; anchor?: string; log?: string }

const PAGE: Record<string, PageTarget> = {
  // 「去诊断」一族 → 设置页的依赖检查区（D30；原来是独立页 ?page=deps）
  claude_cli_outdated: { page: "settings", anchor: DEPS_ANCHOR }, interpreter_blind: { page: "settings", anchor: DEPS_ANCHOR },
  claude_blind: { page: "settings", anchor: DEPS_ANCHOR }, deploy_blind_tcc: { page: "settings", anchor: DEPS_ANCHOR },
  cron_missing: { page: "settings", anchor: DEPS_ANCHOR },
  claude_auth_failed: { page: "settings", anchor: "credentials" },
  engine_dead: { page: "ingest" },
  // 「看进度」：原生 Doctor.swift:239-243 直接亮出 engine.log——「engine.log is all the progress bar there is」；
  // web 的等价物是依赖检查区的日志尾巴深链（?log=engine.log 直接翻开，DepsSection）。此前指回录制页 = 按钮所在的那页，自链
  engine_npm_download: { page: "settings", anchor: DEPS_ANCHOR, log: "engine.log" },
};

function pageHref(target: PageTarget): string {
  const url = buildAppUrl(window.location.href, target.page, null);
  if (target.anchor) url.searchParams.set("anchor", target.anchor);
  if (target.log) url.searchParams.set("log", target.log);
  return url.toString();
}

/** 一颗对症按钮；无动作的 id 渲染 null。`compact` = 卡面 / 诊断条上的小号。 */
export function FailureActionButton({ failureId, compact = false }: { failureId: string | null | undefined; compact?: boolean }) {
  const { text } = useI18n();
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const label = failureActionLabel(failureId, text);
  if (!label) return null;
  const id = failureId ?? "";
  const cls = compact ? "btn btn-quiet" : "btn";

  if (EXTERNAL[id]) return <a className={cls} href={EXTERNAL[id]} target="_blank" rel="noreferrer">{label}</a>;
  if (PAGE[id]) return <a className={cls} href={pageHref(PAGE[id])}>{label}</a>;

  const shell = hasShellBridge();
  if (id === "engine_crashed") {
    if (!shell) return <a className={cls} href={pageHref({ page: "ingest" })}>{label}</a>;
    return <button type="button" className={cls} onClick={() => void callShell("restartRecording").catch((e) => setNote(errorMessage(e)))}>{label}</button>;
  }
  if (id === "cron_fda_blocked") {
    // 原生 FailureCatalog.perform → CronFDA.beginGrant（复制 /usr/sbin/cron + 开面板），不是裸 openPane
    return (
      <>
        <CronFdaGrantButton className={cls} onError={setNote} />
        {note && <span className="settings-warning" role="alert">{note}</span>}
      </>
    );
  }
  if (id === "screen_tcc_lost") {
    if (!shell) return <a className={cls} href={pageHref({ page: "permissions" })}>{label}</a>;
    return <button type="button" className={cls} onClick={() => void callShell("openPane", { pane: "screen" }).catch((e) => setNote(errorMessage(e)))}>{label}</button>;
  }

  const run = async (fn: () => Promise<unknown>, after?: () => void) => {
    setBusy(true);
    setNote(null);
    try {
      await fn();
      after?.();
    } catch (e) {
      setNote(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const action = id === "config_invalid"
    ? () => run(() => postRevealTarget("config"))
    : () => run(() => postRepairActd(), () => { window.setTimeout(() => void refreshHealth(), 3000); });
  return (
    <>
      <button type="button" className={cls} disabled={busy} onClick={() => void action()}>{label}</button>
      {note && <span className="settings-warning" role="alert">{note}</span>}
    </>
  );
}
