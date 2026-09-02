// §56 合并即上岗：顶栏小字「v0.48.4 · deployed 12m ago」——读 board.deploy_state
// （dashboard add-only 顶层键，scripts/auto-deploy.sh 写、actd 投影）。healthy
// （deployed / up_to_date）用第三级文字色；其余状态（rolled_back / refused_dirty /
// fetch_failed / ci_pending / ci_failed…）切警告色并点名状态，title 挂 detail 原文。
// healthy 但 last_incident 在案（回滚被拒后 HEAD 留在新 sha，下一轮的 up_to_date 不许
// 把判决冲掉——#135 review）→ 同样警告色，title 挂判决原文。无 deploy_state 或无
// version → 整个隐藏：这台机器不跑 auto-deploy（.pkg 安装 / Linux / flag 关）。
// 相对时间与 FreshnessLabel 共用 relativeAge，60s tick 自驱重算。
import { useEffect, useState } from "react";
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";
import { parseGeneratedAt, relativeAge } from "./FreshnessLabel";

const TICK_MS = 60_000;
const HEALTHY = new Set(["deployed", "up_to_date"]);

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

export function DeployLabel() {
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
  const parts = [`v${version}`];
  const deployedAt = parseGeneratedAt(state.last_deployed);
  if (deployedAt != null) {
    const age = relativeAge(Math.max(0, (now - deployedAt) / 1000), text);
    parts.push(text(`${age}部署`, `deployed ${age}`));
  }
  if (!healthy) parts.push(statusLabel(status, text));
  else if (incident) parts.push(text("上次回滚判决待处理", "unresolved rollback verdict"));
  const detail = typeof state.detail === "string" ? state.detail : "";
  const title = healthy && incident ? incident : detail;
  const warn = !healthy || Boolean(incident);

  return (
    <span className={`shell-deploy${warn ? " is-warn" : ""}`} role="status" title={title || undefined}>
      {parts.join(" · ")}
    </span>
  );
}
