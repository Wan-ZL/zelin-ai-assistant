// 五列看板装配（BUILD-CONTRACT §2.2 列序）：
//   潜在任务（G4 的 BacklogStrip 折叠侧条，经 renderCard 缝注入 DebtCardItem）|
//   提案（顶部 propose 捕获框）| 运行中（合并列：needs_input blocked 卡最前 →
//   running 分区原序 queued/working 混排，顶部 direct-run 框）| 待验收 | 阶段性完成。
// 全部列消费全局过滤 chips + ⌘F 搜索（taskFilters.matchesCardFilters，G4 与 BacklogStrip
// 同一条规则）；徽章数字 = counts 真实总数，过滤生效时显示「命中/总数」。
// 列是审批状态机的投影——没有拖拽换状态，一切转移都是卡上的显式按钮动词（§0.8）。
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";
import { matchesCardFilters } from "../../taskFilters";
import { BacklogStrip } from "../chrome/BacklogStrip";
import { DebtCardItem } from "./DebtCardItem";
import { DoneCard } from "./DoneCard";
import { Lane } from "./Lane";
import { LaneComposer } from "./LaneComposer";
import { ProposalCard } from "./ProposalCard";
import { ReviewCard } from "./ReviewCard";
import { RunningCard } from "./RunningCard";

export function BoardLanes() {
  const { text } = useI18n();
  const { board, filters } = useAppState();
  if (!board) return null; // AppShell 只在有快照时渲染页面，这里兜底防御

  const pick = <T extends Record<string, unknown>>(rows: T[]): T[] =>
    rows.filter((row) => matchesCardFilters(row, filters));

  const proposals = pick(board.needs_approval);
  const blocked = pick(board.needs_input);
  const running = pick(board.running);
  const review = pick(board.review);
  const completed = pick(board.completed);

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
        help={text(
          "需要你现在拍板的卡：AI 已附上计划、成本和验收标准。批准=后台开始执行；修改=补充方向重提；暂缓=先不做，放进潜在任务。灰色卡是 AI 正在研究的占位。",
          "Cards that need your decision now, each with a plan, cost, and acceptance criteria. Approve = start executing; Comment = redo with your input; Later = not now, parks it in Backlog. Grey cards are placeholders the AI is still researching.",
        )}
        countLabel={label(proposals.length, counts["needs_approval"] ?? board.needs_approval.length)}
        colorToken="--status-todo"
        composer={
          <LaneComposer
            placeholder={text("快速捕获：一句话说清要做什么…", "Quick capture: one line on what to do…")}
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
        help={text(
          "已批准的任务由 AI 在后台执行（排队中显示灰卡）。橙色「需输入」= AI 卡住等你回答，排在最前。",
          'Approved tasks the AI is executing in the background (queued ones show grey). Orange "Needs input" = the AI is blocked on your answer; those sort first.',
        )}
        countLabel={label(blocked.length + running.length, runningTotal)}
        colorToken="--status-progress"
        composer={
          <LaneComposer
            placeholder={text("直接开跑（跳过提案闸）…", "Run directly (skips the proposal gate)…")}
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
        help={text(
          "AI 认为做完了：看交付摘要或 draft PR。验收=进入「阶段性完成」；打回=带你的反馈继续改。",
          "The AI thinks it's done — check the delivery summary or draft PR. Accept moves it to Done for now; Send back continues with your feedback.",
        )}
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
        help={text(
          "本轮完成——可能还在等对方反馈，可随时退回待验收；确认彻底结束就点「永久完成」。徽章数字是真实总数，列表只显示最近 50 条。",
          'Done for this round — it may still be waiting on someone\'s reply, and can go back to Review any time; when it\'s truly over, press "Done for good". The badge shows the true total; the list keeps the latest 50.',
        )}
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
    </div>
  );
}
