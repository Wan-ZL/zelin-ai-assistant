// 看板装配（BUILD-CONTRACT §2.2 列序 + 原生 Kanban.swift 的两根书立条）：
//   潜在任务（BacklogStrip 左侧折叠条，经 renderCard 缝注入 DebtCardItem）|
//   提案（顶部 propose 捕获框）| 运行中（合并列：needs_input blocked 卡最前 →
//   running 分区 queued/working 混排，顶部 direct-run 框）| 待验收 | 阶段性完成 |
//   永久性完成（ArchiveStrip 右侧折叠条——原生 v0.33 的第二根书立条）。
// 全部列消费全局过滤 chips + ⌘F 搜索（taskFilters.matchesCardFilters，G4 与 BacklogStrip
// 同一条规则），再按 store.sortOrder 排序（cardSort.ts 镜像原生 Store.sortCards：默认新的在上；
// 提案列的 processing 占位卡钉在列顶不参与排序，原生 契约一）；徽章数字 = counts 真实总数，
// 过滤生效时显示「命中/总数」。列头「?」说明文案来自 server 目录（Lane.tsx）。
// 列是审批状态机的投影——没有拖拽换状态，一切转移都是卡上的显式按钮动词（§0.8）。
import { sortCards, type SortOrder } from "../../cardSort";
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";
import { matchesCardFilters } from "../../taskFilters";
import type { ApprovalCard } from "../../types";
import { ArchiveStrip } from "../chrome/ArchiveStrip";
import { BacklogStrip } from "../chrome/BacklogStrip";
import { DebtCardItem } from "./DebtCardItem";
import { DoneCard } from "./DoneCard";
import { Lane } from "./Lane";
import { LaneComposer } from "./LaneComposer";
import { ProposalCard } from "./ProposalCard";
import { ReviewCard } from "./ReviewCard";
import { RunningCard } from "./RunningCard";

/** 提案列排序（原生 visibleApprovals）：processing 占位卡保持在顶、不参与排序；其余按偏好，deadline 模式可用 */
export function orderProposals(cards: ApprovalCard[], order: SortOrder): ApprovalCard[] {
  const placeholders = cards.filter((c) => c.processing);
  const real = cards.filter((c) => !c.processing);
  return [...placeholders, ...sortCards(real, order, (c) => (typeof c.deadline === "string" ? c.deadline : null))];
}

export function BoardLanes() {
  const { text } = useI18n();
  const { board, filters, sortOrder } = useAppState();
  if (!board) return null; // AppShell 只在有快照时渲染页面，这里兜底防御

  const pick = <T extends Record<string, unknown>>(rows: T[]): T[] =>
    rows.filter((row) => matchesCardFilters(row, filters));

  const proposals = orderProposals(pick(board.needs_approval), sortOrder);
  const blocked = sortCards(pick(board.needs_input), sortOrder);
  const running = sortCards(pick(board.running), sortOrder);
  const review = sortCards(pick(board.review), sortOrder);
  const completed = sortCards(pick(board.completed), sortOrder);

  const counts = board.counts;
  // 徽章 = counts 真实总数（completed cap 50 后仍读 counts）；过滤命中数另行标注
  const label = (shown: number, total: number) => (shown === total ? `${total}` : `${shown}/${total}`);
  const runningTotal = (counts["running"] ?? board.running.length) + (counts["needs_input"] ?? board.needs_input.length);
  const completedTotal = counts["completed"] ?? board.completed.length;

  return (
    <div className="board-main">
      <BacklogStrip renderCard={(card) => <DebtCardItem item={card} />} />

      <Lane
        title={text("提案", "Proposals")}
        slug="needs_approval"
        countLabel={label(proposals.length, counts["needs_approval"] ?? board.needs_approval.length)}
        colorToken="--status-todo"
        composer={
          <LaneComposer
            placeholder={text("一句话，AI 来研究并提案…", "One sentence — AI researches and proposes…")}
            submitLabel={text("捕获", "Capture")}
            successNote={text("已提交，AI 分析中（通常 2-3 分钟）", "Submitted; AI is analyzing (usually 2-3 min)")}
            buildBody={(t) => ({ action: "capture", text: t })}
          />
        }
        isEmpty={proposals.length === 0}
      >
        {proposals.map((card) => (
          <ProposalCard key={card.id} card={card} />
        ))}
      </Lane>

      <Lane
        title={text("运行中", "Running")}
        slug="running"
        countLabel={label(blocked.length + running.length, runningTotal)}
        colorToken="--status-progress"
        composer={
          <LaneComposer
            placeholder={text("一句话，直接开跑（跳过提案）…", "One line — run it now (skips proposal)…")}
            submitLabel={text("直跑", "Run")}
            successNote={text("已提交，直接开跑（跳过提案），排队派发中…", "Submitted to run directly (skipping proposal); queued for dispatch…")}
            buildBody={(t) => ({ action: "capture", text: t, mode: "run" })}
          />
        }
        isEmpty={blocked.length === 0 && running.length === 0}
      >
        {blocked.map((row) => (
          <RunningCard key={row.id} row={row} isBlocked />
        ))}
        {running.map((row) => (
          <RunningCard key={row.id} row={row} />
        ))}
      </Lane>

      <Lane
        title={text("待验收", "In review")}
        slug="review"
        countLabel={label(review.length, counts["review"] ?? board.review.length)}
        colorToken="--status-review"
        isEmpty={review.length === 0}
      >
        {review.map((card) => (
          <ReviewCard key={card.id} card={card} />
        ))}
      </Lane>

      <Lane
        title={text("阶段性完成", "Done for now")}
        slug="completed"
        countLabel={label(completed.length, completedTotal)}
        colorToken="--status-done"
        capNote={
          completedTotal > board.completed.length
            ? text(`仅显示最近 ${board.completed.length} 条`, `Showing the latest ${board.completed.length} only`)
            : undefined
        }
        isEmpty={completed.length === 0}
      >
        {completed.map((row) => (
          <DoneCard key={row.id} row={row} />
        ))}
      </Lane>

      <ArchiveStrip />
    </div>
  );
}
