// 阶段性完成卡（completed 分区项，delivered）：两动词——
//   退回待验收（revert_review：可能对方反馈来了要再看）·
//   永久完成（archive → 封存，确认弹窗文案统一用「永久完成」，§41）。
// 卡面（原生 TaskRow lane=.completed 收起态）：已交付 章（绿）· repo 章 · 验收于 <相对时间> ·
//   一句话（§64 AI 白话摘要优先，缺席回落 delivered_summary；单行截断，hover 全文）· 单击复制指令 行；
//   「展开详情 ▸」后：交付摘要全文 / 摘要 / 怎样算办完 / 指令 / 会话 ID。
import { useState } from "react";
import { displayId } from "../../cardId";
import { useI18n } from "../../i18n";
import type { TaskRow } from "../../types";
import { cardAction, useSubmit } from "./boardActions";
import { CardDetails, CardHead, CardSurface, CopyCommandLine, DetailsToggle, RelativeTime, RepoChip } from "./cardChrome";
import { BodyText, CopyPathLine, DodList, MetaLine } from "./detailBlocks";
import { ForkDialog } from "./ForkDialog";
import { resumeCommand, stateLabel } from "./RunningCard";
import { AssessmentSummaryLine } from "./VerdictChip";

interface DoneCardProps {
  row: TaskRow;
}

export function DoneCard({ row }: DoneCardProps) {
  const { text } = useI18n();
  const { pending, error, submit } = useSubmit();
  const [confirmArchive, setConfirmArchive] = useState(false);

  const title = typeof row.display_title === "string" && row.display_title ? row.display_title : row.name;
  const cmd = resumeCommand(row);

  const shownId = displayId(row);   // §60：展示工作编号；动作仍送主键 row.id

  return (
    <CardSurface cardId={row.id} label={`${text("已完成", "Done")} · ${title}`}>
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
      <CardDetails cardId={row.id}>
        <BodyText value={row.delivered_summary} />
        <BodyText value={row.summary} className={row.delivered_summary ? "card-detail-muted" : "card-summary"} />
        <DodList dod={row.dod} />
        <CopyPathLine label={text("指令：", "Command: ")} path={cmd} />
        <MetaLine label={text("会话 ID：", "Session ID: ")} value={row.short_id ?? row.session_id} />
      </CardDetails>
      {pending ? (
        <p className="card-pending-note">{text("已提交…", "Submitted…")}</p>
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
          <button type="button" className="btn" onClick={() => setConfirmArchive(true)}>
            {text("永久完成", "Done for good")}
          </button>
          <DetailsToggle cardId={row.id} />
        </div>
      )}
      {error && <p className="card-error">{error}</p>}

      {confirmArchive && (
        <ForkDialog
          title={text(`永久完成 ${shownId}？`, `Done for good — ${shownId}?`)}
          body={text(
            "封存这条线程：不再参与匹配、不再提示，后续相关信息会开新卡。可随时从归档「放回看板」。",
            "Seal this thread: no more matching or suggestions; future related info opens a new card. You can always bring it back from the archive.",
          )}
          choices={[
            {
              label: text("永久完成", "Done for good"),
              onPick: () => {
                setConfirmArchive(false);
                void submit(cardAction(row.id, "archive"));
              },
            },
          ]}
          onCancel={() => setConfirmArchive(false)}
        />
      )}
    </CardSurface>
  );
}
