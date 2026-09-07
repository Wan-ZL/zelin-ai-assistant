// §56 合并即上岗：顶栏小字「v0.48.4 · deployed 12m ago」——读 board.deploy_state
// （dashboard add-only 顶层键，scripts/auto-deploy.sh 写、actd 投影）。healthy
// （deployed / up_to_date）用第三级文字色；其余状态（rolled_back / refused_dirty /
// fetch_failed / ci_pending / ci_failed…）切警告色并点名状态，title 挂 detail 原文。
// healthy 但 last_incident 在案（回滚被拒后 HEAD 留在新 sha，下一轮的 up_to_date 不许
// 把判决冲掉——#135 review）→ 同样警告色，title 挂判决原文。无 deploy_state 或无
// version → 整个隐藏：这台机器不跑 auto-deploy（.pkg 安装 / Linux / flag 关）。
// `deferred`（§56.3 会话闸门，2026-09-07）：绿的新版本已就绪，但 roster 上还有活着的
// 后台 claude 会话，部署任务不重启 actd——这是 owner 要的行为，不是故障：次级色 +
// 「新版本已就绪，等待 N 个会话结束或验收后更新」（「验收」因为 done 但进程还在的 worker
// ——每张待验收卡——也算活着，验收/打回经 actd 停掉它）；连续等满 DEFER_WARN_HOURS（镜像
// act/lib/deploy_state.py DEFER_WARN_AFTER_S，那是唯一真源）才切警告色并追加「已 X 小时」。
// 两种情况不许被 deferred 遮住（review of #284，与 doctor 行同判）：`reason` 里带着
// install_incomplete 的 token（修补被延后——机器没在跑它 checkout 的代码）→ 警告色 +
// 「安装未完成，」前缀；`last_incident` 在案 → 警告色 + 「上次回滚判决待处理」、title 挂判决。
// 相对时间与 FreshnessLabel 共用 relativeAge，60s tick 自驱重算。计算住 useDeployLabel（HeaderBar 调一次：
// full / compact 渲染成小字，tight 折进连接点的 tooltip，§49 追记 2026-09-04）；DeployLabel 只管渲染。
import { useEffect, useState } from "react";
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";
import { parseGeneratedAt, relativeAge } from "./FreshnessLabel";

const TICK_MS = 60_000;
const HEALTHY = new Set(["deployed", "up_to_date"]);
const DEFERRED = "deferred";
const DEFER_WARN_HOURS = 6;

const DEFER_OWN_REASONS = new Set(["sessions_running", "roster_unknown"]);

/** 「新版本已就绪，等待 N 个会话结束或验收后更新[（已 X 小时）]」+ 是否已过警告线 + 是否是被延后的修补。 */
function deferredLabel(
  state: { deferred_sessions?: unknown; deferred_since?: unknown; reason?: unknown },
  now: number,
  text: (zh: string, en: string) => string,
): { label: string; overdue: boolean; repair: boolean } {
  const n = typeof state.deferred_sessions === "string" ? state.deferred_sessions : "";
  const since = parseGeneratedAt(state.deferred_since);
  const hours = since == null ? 0 : Math.floor(Math.max(0, now - since) / 3_600_000);
  const overdue = since != null && hours >= DEFER_WARN_HOURS;
  // §56.3 step 2 修补被延后：reason 里除闸门自己的 token 外还有失配 token
  const reason = typeof state.reason === "string" ? state.reason : "";
  const repair = reason.split(/\s+/).some((t) => t !== "" && !DEFER_OWN_REASONS.has(t));
  const waiting = n
    ? text(`等待 ${n} 个会话结束或验收`, `waiting for ${n} session${n === "1" ? "" : "s"} to finish or be accepted`)
    : text("等待会话结束或验收", "waiting for sessions to finish or be accepted");
  let label = repair
    ? text(`安装未完成，修补${waiting}`, `install incomplete, repair ${waiting}`)
    : text(`新版本已就绪，${waiting}后更新`, `update ready, ${waiting}`);
  if (overdue) label += text(`（已 ${hours} 小时）`, ` (${hours}h)`);
  return { label, overdue, repair };
}

function statusLabel(status: string, text: (zh: string, en: string) => string): string {
  switch (status) {
    case "rolled_back":
      return text("已回滚", "rolled back");
    case "rollback_failed":
      return text("回滚失败", "rollback failed");
    case "refused_dirty":
      return text("工作树有改动，部署暂停", "deploy paused: dirty tree");
    case "refused_branch":
      return text("不在 main，部署暂停", "deploy paused: not on main");
    case "fetch_failed":
      return text("fetch 失败", "fetch failed");
    case "ci_pending":
      return text("等 main 的 CI", "waiting for CI on main");
    case "ci_failed":
      return text("main 的 CI 红了，未部署", "main CI red, not deployed");
    case "failed":
      return text("部署失败", "deploy failed");
    // §56.4 v0.48.20：HEAD 到位但没跑起来（install_report / heartbeat 版本不符；
    // 第一眼只记账，下一轮仍如此才重跑 install.sh）；launchd 任务读不到外置盘（TCC，需授权）
    case "install_incomplete":
      return text("安装未完成", "install incomplete");
    case "blocked_tcc":
      return text("后台任务读不到外置盘（需授权）", "job blocked from the volume (grant access)");
    default:
      return status || text("状态未知", "unknown state");
  }
}

export interface DeployLabelState {
  /** 「v0.48.4 · deployed 12m ago[ · rolled back]」 */
  label: string;
  /** detail 原文；healthy 但 last_incident 在案时是判决原文 */
  title: string;
  warn: boolean;
}

export function useDeployLabel(): DeployLabelState | null {
  const { text } = useI18n();
  const { board } = useAppState();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, []);

  const state = board?.deploy_state;
  const version = typeof state?.version === "string" ? state.version : "";
  if (!state || !version) return null;

  const status = typeof state.status === "string" ? state.status : "";
  const incident = typeof state.last_incident === "string" ? state.last_incident : "";
  const healthy = HEALTHY.has(status);
  const deferred = status === DEFERRED ? deferredLabel(state, now, text) : null;
  const parts = [`v${version}`];
  const deployedAt = parseGeneratedAt(state.last_deployed);
  if (deployedAt != null) {
    const age = relativeAge(Math.max(0, (now - deployedAt) / 1000), text);
    parts.push(text(`${age}部署`, `deployed ${age}`));
  }
  if (deferred) parts.push(deferred.label);
  else if (!healthy) parts.push(statusLabel(status, text));
  if ((healthy || deferred) && incident) parts.push(text("上次回滚判决待处理", "unresolved rollback verdict"));
  const detail = typeof state.detail === "string" ? state.detail : "";
  return {
    label: parts.join(" · "),
    title: (healthy || deferred) && incident ? incident : detail,
    warn: deferred ? deferred.overdue || deferred.repair || Boolean(incident) : !healthy || Boolean(incident),
  };
}

export function DeployLabel({ value }: { value: DeployLabelState | null }) {
  if (!value) return null;
  return (
    <span className={`shell-deploy${value.warn ? " is-warn" : ""}`} role="status" title={value.title || undefined}>
      {value.label}
    </span>
  );
}
