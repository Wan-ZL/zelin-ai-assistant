// 管线健康横幅（CONTRACT §47.4）：Mac 版 PipelineHealthBanner 的 web 替身——
// 2026-08-31 actd 静默卡死 2.5h，唯一的 detector 是正在退役的菜单栏 app。
// 数据源 = GET /api/health（store.health，app.tsx 每 30s 轮询 + SSE 重连后即刷）。
// 三个要说话的 verdict：stalled（进程活着、心跳停了）/ failing（连续崩 ≥3）/
// stale（没心跳且看板过期 = 没在跑）。ok / unknown（老 daemon 仍在写看板）不渲染。
// 与 ErrorBanner 互斥：server 连不上时那条横幅说话，本条闭嘴（同一信息绝不双份）。
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";
import type { HealthSnapshot } from "../../types";

const RESTART_CMD = "launchctl kickstart -k gui/$(id -u)/com.zelin.aiassistant.actd";

function minutes(seconds: number | undefined | null): number {
  return Math.max(1, Math.floor((seconds ?? 0) / 60));
}

/** verdict → (title, detail)；null = 不该显示。导出供测试直测文案分派。 */
export function describeHealth(
  health: HealthSnapshot,
  text: (zh: string, en: string) => string,
): { title: string; detail: string; tone: "danger" | "warning" } | null {
  switch (health.verdict) {
    case "stalled": {
      const mins = minutes(health.heartbeat?.age_s);
      const phase = health.heartbeat?.phase ?? "?";
      return {
        tone: "danger",
        title: text("后台服务卡住了", "Background service is stuck"),
        detail: text(
          `actd 进程还活着，但已 ${mins} 分钟没有心跳（最后阶段：${phase}）——卡片不会动。修复：${RESTART_CMD}`,
          `actd is alive but has not beaten for ${mins} min (last phase: ${phase}) — cards will not move. Fix: ${RESTART_CMD}`,
        ),
      };
    }
    case "failing": {
      const n = health.loop_health.consecutive_failures;
      const err = health.loop_health.last_error ? `：${health.loop_health.last_error}` : "";
      const errEn = health.loop_health.last_error ? `: ${health.loop_health.last_error}` : "";
      return {
        tone: "danger",
        title: text("后台服务每轮都在崩", "Background service crashes every pass"),
        detail: text(
          `连续 ${n} 轮失败${err}。看 ~/Library/Logs/zelin-ai-assistant/actd.launchd.log`,
          `${n} passes failed in a row${errEn}. See ~/Library/Logs/zelin-ai-assistant/actd.launchd.log`,
        ),
      };
    }
    case "stale": {
      const mins = health.dashboard ? minutes(health.dashboard.age_s) : null;
      return {
        tone: "warning",
        title: text("后台服务没在运行", "Background service is not running"),
        detail: text(
          `${mins == null ? "看板从未生成" : `看板数据 ${mins} 分钟没更新`}，也没有心跳。修复：bash install.sh，或 ${RESTART_CMD}`,
          `${mins == null ? "The board was never generated" : `Board data is ${mins} min old`} and there is no heartbeat. Fix: bash install.sh, or ${RESTART_CMD}`,
        ),
      };
    }
    default:
      return null;
  }
}

export function PipelineBanner() {
  const { text } = useI18n();
  const { health, boardError, connection } = useAppState();

  if (!health) return null;
  if (boardError != null || connection === "reconnecting") return null; // ErrorBanner 在说话
  const described = describeHealth(health, text);
  if (!described) return null;

  return (
    <div className={`shell-banner is-${described.tone}`} role="alert" data-verdict={health.verdict}>
      <svg className="shell-banner-icon" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M12 2.8 22.6 21H1.4L12 2.8Zm0 6.2a1 1 0 0 0-1 1v4a1 1 0 1 0 2 0v-4a1 1 0 0 0-1-1Zm0 8a1.2 1.2 0 1 0 0 2.4 1.2 1.2 0 0 0 0-2.4Z"
          fill="currentColor"
        />
      </svg>
      <strong className="shell-banner-title">{described.title}</strong>
      <span className="shell-banner-detail">{described.detail}</span>
    </div>
  );
}
