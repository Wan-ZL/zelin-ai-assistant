// §25 失败目录的「对症一键」（原生 Doctor.swift FailureCatalog.actionLabel / perform 的 web 版）：doctor 行、
// 诊断条、卡片错误行带 failure_id（act/lib/failures.py 的闭集）时给一颗按钮——标签逐字镜像原生
// （安装页 / 去诊断 / 去设置 / 去录制页 / 看进度 / 重启引擎 / 安装 ffmpeg / 去授权… / 一键修复 / 查看修法 / 显示文件），
// 动作落到 web 已有的机制：外链（Claude Code / Node.js / ffmpeg 安装页）、页面深链（依赖检查 / 设置凭证区 /
// 录制页 / 权限体检）、桥（重启录制引擎 / 系统设置面板；浏览器里退成页面深链）、server（POST /api/repair/actd
// 一键修复、POST /api/reveal {target:"config"} 显示文件）。未知 id → null（不装按钮，原文照旧）。
import { useState } from "react";
import { postRepairActd, postRevealTarget } from "../../api";
import { useI18n } from "../../i18n";
import { buildAppUrl, type AppPage } from "../../route";
import { callShell, hasShellBridge } from "../../shellBridge";
import { refreshHealth } from "../../store";
import { errorMessage } from "./useToast";

type Text = (zh: string, en: string) => string;

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

const PAGE: Record<string, { page: AppPage; anchor?: string }> = {
  claude_cli_outdated: { page: "deps" }, interpreter_blind: { page: "deps" }, claude_blind: { page: "deps" }, deploy_blind_tcc: { page: "deps" },
  cron_missing: { page: "deps" },
  claude_auth_failed: { page: "settings", anchor: "credentials" },
  engine_dead: { page: "ingest" }, engine_npm_download: { page: "ingest" },
};

function pageHref(target: { page: AppPage; anchor?: string }): string {
  const url = buildAppUrl(window.location.href, target.page, null);
  if (target.anchor) url.searchParams.set("anchor", target.anchor);
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
  if (id === "screen_tcc_lost" || id === "cron_fda_blocked") {
    if (!shell) return <a className={cls} href={pageHref({ page: "permissions" })}>{label}</a>;
    const pane = id === "screen_tcc_lost" ? "screen" : "full_disk";
    return <button type="button" className={cls} onClick={() => void callShell("openPane", { pane }).catch((e) => setNote(errorMessage(e)))}>{label}</button>;
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
