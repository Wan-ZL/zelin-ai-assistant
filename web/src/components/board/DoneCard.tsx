// 阶段性完成卡（completed 分区项，delivered）：两动词——
//   退回待验收（revert_review：可能对方反馈来了要再看）·
//   永久完成（archive → 封存；一点即发、不弹确认——可逆，永久性完成书立条「放回看板」随时撤回；
//   原生 Cards.swift:1540-1548「One tap, no confirm (reversible via 放回看板)」，潜在任务卡同一动词同款，§41 / §54.1 追记）。
// 卡面（原生 TaskRow lane=.completed 收起态）：已交付 章（绿）· repo 章 · 验收于 <相对时间> ·
//   一句话（§64 AI 白话摘要优先，缺席回落 delivered_summary；单行截断，hover 全文）· 单击复制指令 行。
//   交付摘要全文 / 摘要 / 怎样算办完 / 指令 / 会话 ID 住右侧详情侧栏（「展开详情 ▸」打开，D34；DetailFields 渲染）。
//   v0.21 契约七：阶段性完成卡也可多选参与合并（Kanban.swift:491-493，CardSurface selectable）。
import { useI18n } from "../../i18n";
import type { TaskRow } from "../../types";
import { cardAction, resumeCommand, useSubmit, pendingNote } from "./boardActions";
import { CardHead, CardSurface, CopyCommandLine, DetailsToggle, RelativeTime, RepoChip } from "./cardChrome";
import { stateLabel } from "./RunningCard";
import { AssessmentSummaryLine } from "./VerdictChip";

interface DoneCardProps {
  row: TaskRow;
}

export function DoneCard({ row }: DoneCardProps) {
  const { text } = useI18n();
  const { pending, pendingAction, error, submit } = useSubmit();

  const title = typeof row.display_title === "string" && row.display_title ? row.display_title : row.name;
  const cmd = resumeCommand(row);

  return (
    <CardSurface cardId={row.id} label={`${text("已完成", "Done")} · ${title}`} selectable>
      <CardHead card={row} title={title} leading={<span className="card-dot is-done" aria-hidden="true" />} />
      <div className="card-badges">
        {/* 原生 completed 行：状态章 已交付（绿 accent）· 验收于 <相对> · repo 章 */}
        <span className="chip chip-success">{stateLabel(row.state === "done" ? "delivered" : row.state, text)}</span>
        <RepoChip path={row.cwd} />
        <RelativeTime epoch={row.accepted_at} prefix={text("验收于 ", "accepted ")} />
      </div>
      {/* 原生 completed 行的一句：一行 11 regular 次级，lineLimit(1)——§64 AI 摘要优先，回落 delivered_summary */}
      <AssessmentSummaryLine assessment={row.assessment} fallback={row.delivered_summary} />
      <CopyCommandLine cmd={cmd} />
      {pending ? (
        <p className="card-pending-note">{pendingNote(pendingAction, text)}</p>
      ) : (
        <div className="card-actions">
          {/* 色相 = Mac tint：青退回待验收 · 灰永久完成 */}
          <button
            type="button"
            className="btn btn-accent"
            onClick={() => void submit(cardAction(row.id, "revert_review"))}
          >
            {text("退回待验收", "Back to review")}
          </button>
          {/* 原生 :1545-1548：一点即 archive，无确认——封存可逆（书立条「放回看板」），动作回传送主键 row.id（§60） */}
          <button
            type="button"
            className="btn"
            title={text("封存这条已验收的线程：不再参与匹配、不再提示，后续相关信息会开新卡。可随时从永久性完成「放回看板」", "Seal this accepted thread: no more matching or suggestions; future related info opens a new card. You can always put it back from Done for good")}
            onClick={() => void submit(cardAction(row.id, "archive"))}
          >
            {text("永久完成", "Done for good")}
          </button>
          <DetailsToggle cardId={row.id} />
        </div>
      )}
      {error && <p className="card-error">{error}</p>}
    </CardSurface>
  );
}
