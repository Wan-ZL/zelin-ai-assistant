// 每日整理横幅（CONTRACT §70，owner 决策 D10 的设计判断：不弹系统通知，看板顶部留一行）。
// 数据源 = board.maintenance（dashboard add-only 顶层键，actd 在循环起止各写一次 → board.updated → 重拉）。
// 两种话：
//   running — phase ≠ idle 且 started_at 在 2 小时内（更久 = 上次崩在半路，投影不诚实就不说）：「正在整理看板…」；
//   summary — 最近一次运行落在今天（本地日）且合并/清理/提案有一项 > 0：「今日整理：合并 N、清理 M（可撤销）· 提案 K」。
// 与 ErrorBanner / PipelineBanner 不互斥（它们说的是「服务坏了」，本条说的是「服务在干活」），但 server 连不上时闭嘴。
import { useI18n } from "../../i18n";
import { buildAppUrl } from "../../route";
import { useAppState } from "../../store";
import type { Maintenance } from "../../types";

const RUNNING_STALE_S = 2 * 3600;

const PHASE_LABEL: Record<string, [string, string]> = {
  dedup: ["去重合并", "deduplicating"],
  stale_sweep: ["清理过时卡", "sweeping stale cards"],
  proposals: ["生成提案", "drafting proposals"],
};

function sameLocalDay(epoch: number, now: Date): boolean {
  const d = new Date(epoch * 1000);
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}

/** 投影 → 文案；null = 不该显示。导出供测试直测。 */
export function describeMaintenance(
  m: Maintenance | undefined,
  text: (zh: string, en: string) => string,
  now: Date = new Date(),
): { kind: "running" | "summary"; message: string } | null {
  if (!m) return null;
  const nowS = now.getTime() / 1000;
  if (m.phase !== "idle" && typeof m.started_at === "number" && nowS - m.started_at < RUNNING_STALE_S) {
    const [zh, en] = PHASE_LABEL[m.phase] ?? ["整理中", "working"];
    return { kind: "running", message: text(`正在整理看板…（${zh}）`, `Tidying the board… (${en})`) };
  }
  if (typeof m.last_run_at !== "number" || !sameLocalDay(m.last_run_at, now)) return null;
  const r = m.last_result ?? { merged: 0, trashed: 0, proposals: 0 };
  const merged = Number(r.merged) || 0;
  const trashed = Number(r.trashed) || 0;
  const proposals = Number(r.proposals) || 0;
  if (merged + trashed + proposals === 0) return null;
  const parts = [
    text(`合并 ${merged}`, `${merged} merged`),
    text(`清理 ${trashed}（可撤销）`, `${trashed} cleaned up (undoable)`),
    text(`提案 ${proposals}`, `${proposals} proposed`),
  ];
  return {
    kind: "summary",
    message: text(`今日整理：${parts.join("、")}`, `Today's tidy-up: ${parts.join(" · ")}`),
  };
}

export function MaintenanceBanner() {
  const { text } = useI18n();
  const { board, boardError, connection } = useAppState();
  if (!board || boardError != null || connection === "reconnecting") return null;
  const described = describeMaintenance(board.maintenance, text);
  if (!described) return null;
  const trashHint = described.kind === "summary" ? text("回收站可恢复", "Restore from the trash") : null;

  return (
    <div className="shell-banner is-info" role="status" data-kind={described.kind}>
      <span className="shell-banner-icon shell-banner-icon-dot" aria-hidden="true">●</span>
      <strong className="shell-banner-title">{text("每日整理", "Daily tidy-up")}</strong>
      <span className="shell-banner-detail">{described.message}</span>
      {trashHint && (
        <a className="shell-banner-link" href={buildAppUrl(window.location.href, "trash", null).toString()}>{trashHint}</a>
      )}
    </div>
  );
}
