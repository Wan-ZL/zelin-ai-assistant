// 每日整理横幅（CONTRACT §70，owner 决策 D10 的设计判断：不弹系统通知，看板顶部留一行）。
// 数据源 = board.maintenance（dashboard add-only 顶层键，actd 在循环起止各写一次 → board.updated → 重拉）。
// 两种话：
//   running — phase ≠ idle 且 started_at 在 2 小时内（更久 = 上次崩在半路，投影不诚实就不说）：「正在整理看板…」；
//   summary — 最近一次运行落在今天（本地日）且合并/清理/提案/自检有一项 > 0：「今日整理：合并 N、清理 M（可撤销）· 提案 K」；
//             三计数全零（只剩自检）时说「今日整理：看板无变动」——不报三个 0，也不许诺一次没发生过的撤销。
//   「回收站可恢复」链接只在真有卡进了回收站时出现（清理 > 0，或合并 > 0——同题合成后旧卡也进回收站）。
// D33：last_result.advisories（自检类信号——doctor 红灯 / 派发卡死 / 日志刷屏……不铸卡）在同一行右侧收成一个
// 「系统自检 N 条」按钮，点开在横幅下方列出每条（文本 + 首见日期）；仍不弹系统通知，仍不新增 inbox 动词。
// 与 ErrorBanner / PipelineBanner 不互斥（它们说的是「服务坏了」，本条说的是「服务在干活」），但 server 连不上时闭嘴。
import { useState } from "react";
import { useI18n } from "../../i18n";
import { buildAppUrl } from "../../route";
import { useAppState } from "../../store";
import type { Maintenance, MaintenanceAdvisory } from "../../types";

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

/** wire 的 advisories 列表 → 只留形状对的行（text 非空）；缺键 / 坏形状 = 空列表。 */
export function advisoriesOf(m: Maintenance | undefined): MaintenanceAdvisory[] {
  const raw = m?.last_result?.advisories;
  if (!Array.isArray(raw)) return [];
  return raw.filter((a): a is MaintenanceAdvisory => !!a && typeof a === "object" && typeof a.text === "string" && a.text !== "");
}

/** 投影 → 文案；null = 不该显示。导出供测试直测。 */
export function describeMaintenance(
  m: Maintenance | undefined,
  text: (zh: string, en: string) => string,
  now: Date = new Date(),
): { kind: "running" | "summary"; message: string; advisories: MaintenanceAdvisory[]; trashed: boolean } | null {
  if (!m) return null;
  const nowS = now.getTime() / 1000;
  if (m.phase !== "idle" && typeof m.started_at === "number" && nowS - m.started_at < RUNNING_STALE_S) {
    const [zh, en] = PHASE_LABEL[m.phase] ?? ["整理中", "working"];
    return { kind: "running", message: text(`正在整理看板…（${zh}）`, `Tidying the board… (${en})`), advisories: [], trashed: false };
  }
  if (typeof m.last_run_at !== "number" || !sameLocalDay(m.last_run_at, now)) return null;
  const r = m.last_result ?? { merged: 0, trashed: 0, proposals: 0 };
  const merged = Number(r.merged) || 0;
  const trashed = Number(r.trashed) || 0;
  const proposals = Number(r.proposals) || 0;
  const advisories = advisoriesOf(m);
  if (merged + trashed + proposals + advisories.length === 0) return null;
  if (merged + trashed + proposals === 0) {
    return { kind: "summary", message: text("今日整理：看板无变动", "Today's tidy-up: nothing to change"), advisories, trashed: false };
  }
  const parts = [
    text(`合并 ${merged}`, `${merged} merged`),
    text(`清理 ${trashed}（可撤销）`, `${trashed} cleaned up (undoable)`),
    text(`提案 ${proposals}`, `${proposals} proposed`),
  ];
  return {
    kind: "summary",
    message: text(`今日整理：${parts.join("、")}`, `Today's tidy-up: ${parts.join(" · ")}`),
    advisories,
    trashed: merged + trashed > 0,   // 合并也把旧卡送进回收站（daily-merge），所以「可恢复」看合并 + 清理
  };
}

export function MaintenanceBanner() {
  const { text } = useI18n();
  const { board, boardError, connection } = useAppState();
  const [open, setOpen] = useState(false);
  if (!board || boardError != null || connection === "reconnecting") return null;
  const described = describeMaintenance(board.maintenance, text);
  if (!described) return null;
  const trashHint = described.trashed ? text("回收站可恢复", "Restore from the trash") : null;
  const n = described.advisories.length;
  const showList = open && n > 0;

  return (
    <div className={`shell-banner is-info${showList ? " is-open" : ""}`} role="status" data-kind={described.kind}>
      <span className="shell-banner-icon shell-banner-icon-dot" aria-hidden="true">●</span>
      <strong className="shell-banner-title">{text("每日整理", "Daily tidy-up")}</strong>
      <span className="shell-banner-detail">{described.message}</span>
      {n > 0 && (
        <button
          type="button"
          className="shell-banner-toggle"
          aria-expanded={open}
          aria-controls="maintenance-advisories"
          onClick={() => setOpen((v) => !v)}
        >
          {text(`系统自检 ${n} 条`, `${n} self-check ${n === 1 ? "note" : "notes"}`)}
          <span aria-hidden="true">{open ? " ▴" : " ▾"}</span>
        </button>
      )}
      {trashHint && (
        <a className="shell-banner-link" href={buildAppUrl(window.location.href, "trash", null).toString()}>{trashHint}</a>
      )}
      {showList && (
        <ul id="maintenance-advisories" className="shell-banner-list">
          {described.advisories.map((a, i) => (
            <li key={a.fingerprint || `${a.kind}:${i}`}>
              <span className="shell-banner-list-kind">{a.kind}</span>
              <span>{a.text}</span>
              {a.first_seen && <span className="shell-banner-note">{text(`首见 ${a.first_seen}`, `since ${a.first_seen}`)}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
