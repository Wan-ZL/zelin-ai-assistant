// 管线健康横幅（CONTRACT §47.4）：Mac 版 PipelineHealthBanner 的 web 替身——
// 2026-08-31 actd 静默卡死 2.5h，唯一的 detector 是正在退役的菜单栏 app。
// 数据源 = GET /api/health（store.health，app.tsx 每 30s 轮询 + SSE 重连后即刷）。
// 三个要说话的 verdict：stalled（心跳停了——server 只 stat 文件、不探进程，卡死与已死都长这样）/
// failing（连续崩 ≥3）/ stale（没心跳且看板过期 = 没在跑）。ok / unknown（老 daemon 仍在写看板）不渲染。
// 与 ErrorBanner 互斥：server 连不上时那条横幅说话，本条闭嘴（同一信息绝不双份）。
// 修法命令不再揉进正文句子：三态都在动作行给同一条可复制的「手动命令：」（原生 CopyPathLine，§68.8）。
import { useState } from "react";
import { postAiFixDoctor } from "../../api";
import { useI18n } from "../../i18n";
import { buildSettingsUrl, DEPS_ANCHOR } from "../../route";
import { useAppState } from "../../store";
import type { HealthSnapshot } from "../../types";
import { CopyLine } from "../chrome/CopyLine";
import { errorMessage } from "../settings/useToast";
import { useRepairActd } from "./repairActd";

export const RESTART_CMD = "launchctl kickstart -k gui/$(id -u)/com.zelin.aiassistant.actd";

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
      // 只说已知的：心跳多久没跳、最后停在哪一步。server 没探过进程（§47.4 读者 2），
      // 「进程还活着」谁也没查过——原生 Freshness.swift 的 dead 句同样只报数据多久没更新
      const mins = minutes(health.heartbeat?.age_s);
      const phase = health.heartbeat?.phase ?? "?";
      return {
        tone: "danger",
        title: text("后台服务卡住了", "Background service is stuck"),
        detail: text(
          `后台服务已 ${mins} 分钟没有心跳（最后阶段：${phase}）——卡在原地或已停止，卡片不会动。`,
          `No heartbeat for ${mins} min (last phase: ${phase}) — stuck or stopped; cards will not move.`,
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
          `${mins == null ? "看板从未生成" : `看板数据 ${mins} 分钟没更新`}，也没有心跳。修复：bash install.sh，或用「手动命令」重启。`,
          `${mins == null ? "The board was never generated" : `Board data is ${mins} min old`} and there is no heartbeat. Fix: bash install.sh, or restart with the manual command.`,
        ),
      };
    }
    default:
      return null;
  }
}

/**
 * 「让 AI 修」（原生 Freshness.swift 失败行的第二颗按钮）：POST /api/ai-fix {source:"doctor"}——上下文由 server
 * 从自己跑的 doctor 报告推导（§54.4 / §68.4），与设置页依赖检查区同一条路；忙态词 / 失败前缀逐字镜像 DepsSection。
 */
function AiFixDoctorButton() {
  const { text, language } = useI18n();
  const [state, setState] = useState<"idle" | "busy" | string>("idle");
  const launch = async () => {
    setState("busy");
    try {
      await postAiFixDoctor(language);
      setState("idle");
    } catch (err) {
      setState(text("让 AI 修启动失败：", "Fix with AI failed to launch: ") + errorMessage(err));
    }
  };
  return (
    <>
      <button type="button" className="shell-button" disabled={state === "busy"} onClick={() => void launch()}>
        {state === "busy" ? text("正在准备诊断包…", "Preparing the diagnostic bundle…") : text("让 AI 修", "Fix with AI")}
      </button>
      {state !== "idle" && state !== "busy" && <span className="shell-banner-note">{state}</span>}
    </>
  );
}

/**
 * 一键修复（原生 PipelineRepair 的 web 落点，§68.8）：POST /api/repair/actd → server 对已加载的 actd agent
 * kickstart；未加载（409）时 server 的整句原文指向 bash install.sh。之后 useRepairActd 每 1 s 问一次 health、
 * 最多 15 s：恢复 → 「已恢复 ✓ 数据重新更新了」6 s 后刷 store（横幅退场）；没恢复 → 诚实的失败行
 * （原生 Freshness.swift:208-238）：自动修复没成功：<原因> + 再试一次 + 让 AI 修 + 可复制的手动命令。
 */
export function RepairButton({ verdict }: { verdict?: string }) {
  const { text } = useI18n();
  const { phase, run } = useRepairActd();
  // 原生 Freshness.swift：卡住 / 连崩 = 「一键修复」（kickstart）；没在跑（stale）= 「启动后台服务」（同一 kickstart，
  // 未加载时 server 409 指向 bash install.sh）；失败后按钮换成「再试一次」+ 让 AI 修 + 手动命令
  const isStart = verdict === "stale";
  const busy = phase.kind === "running";
  // 依赖检查自 D30 起是设置页的一区：?page=settings&anchor=deps（查看日志再带 ?log=actd.log，区内直接翻开该尾巴）
  const depsUrl = buildSettingsUrl(window.location.href, DEPS_ANCHOR);
  const depsHref = depsUrl.toString();
  depsUrl.searchParams.set("log", "actd.log");
  const logHref = depsUrl.toString();
  return (
    <span className="shell-banner-actions">
      {phase.kind === "failure" ? (
        <>
          <span className="shell-banner-note"><span>{isStart ? text("启动没成功：", "Start didn't work: ") : text("自动修复没成功：", "Auto-repair didn't work: ")}</span><span>{phase.detail}</span></span>
          <button type="button" className="shell-button" onClick={run}>{text("再试一次", "Try again")}</button>
          <AiFixDoctorButton />
        </>
      ) : phase.kind === "success" ? (
        <span className="shell-banner-note is-success" role="status">{text("已恢复 ✓ 数据重新更新了", "Recovered ✓ data is updating again")}</span>
      ) : (
        <button type="button" className="shell-button" disabled={busy} onClick={run}>
          {busy ? text("修复中…", "Repairing…") : isStart ? text("启动后台服务", "Start service") : text("一键修复", "Fix now")}
        </button>
      )}
      <a className="shell-banner-link" href={depsHref}>{isStart ? text("打开依赖检查", "Open dependency check") : text("依赖检查", "Dependency check")}</a>
      <a className="shell-banner-link" href={logHref}>{text("查看日志", "View log")}</a>
      {busy && (
        <span className="shell-banner-note">
          {isStart ? text("正在启动并等待首份数据…", "Starting and waiting for the first data…") : text("正在重启后台服务并等待数据更新（最多 15 秒）…", "Restarting the background service and waiting for data (up to 15 s)…")}
        </span>
      )}
      <CopyLine label={text("手动命令：", "Manual command: ")} value={RESTART_CMD} />
    </span>
  );
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
      <RepairButton verdict={health.verdict} />
    </div>
  );
}
