// §56 合并即上岗：顶栏小字「v0.48.4 · deployed 12m ago」——读 board.deploy_state
// （dashboard add-only 顶层键，scripts/auto-deploy.sh 写、actd 投影）。healthy
// （deployed / up_to_date）用第三级文字色；其余状态（rolled_back / refused_dirty /
// fetch_failed…）切警告色并点名状态，title 挂 detail 原文。无 deploy_state 或无
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
    case "failed":
      return text("部署失败", "deploy failed");
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
  const healthy = HEALTHY.has(status);
  const parts = [`v${version}`];
  const deployedAt = parseGeneratedAt(state.last_deployed);
  if (deployedAt != null) {
    const age = relativeAge(Math.max(0, (now - deployedAt) / 1000), text);
    parts.push(text(`${age}部署`, `deployed ${age}`));
  }
  if (!healthy) parts.push(statusLabel(status, text));
  const detail = typeof state.detail === "string" ? state.detail : "";

  return (
    <span className={`shell-deploy${healthy ? "" : " is-warn"}`} role="status" title={detail || undefined}>
      {parts.join(" · ")}
    </span>
  );
}
