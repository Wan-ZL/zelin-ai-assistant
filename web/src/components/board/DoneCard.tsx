// 阶段性完成卡（completed 分区项，delivered）：两动词——
//   退回待验收（revert_review：可能对方反馈来了要再看）·
//   永久完成（archive → 封存，确认弹窗文案统一用「永久完成」，§41）。
import { useState } from "react";
import { useI18n } from "../../i18n";
import type { TaskRow } from "../../types";
import { cardAction, openCardDetail, useSubmit } from "./boardActions";
import { ForkDialog } from "./ForkDialog";

interface DoneCardProps {
  row: TaskRow;
}

export function DoneCard({ row }: DoneCardProps) {
  const { text, locale } = useI18n();
  const { pending, error, submit } = useSubmit();
  const [confirmArchive, setConfirmArchive] = useState(false);

  const acceptedAt = typeof row.accepted_at === "number"
    ? new Date(row.accepted_at * 1000).toLocaleDateString(locale)
    : null;

  return (
    <article className="task-card" onDoubleClick={() => openCardDetail(row.id)}>
      <div className="card-id">{row.id}</div>
      <div className="card-title">{row.name}</div>
      {(row.delivered_summary || row.summary) && (
        <p className="card-summary">{row.delivered_summary ?? row.summary}</p>
      )}
      {acceptedAt && (
        <div className="card-badges">
          <span className="chip chip-success">{text(`验收于 ${acceptedAt}`, `accepted ${acceptedAt}`)}</span>
        </div>
      )}
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
        </div>
      )}
      {error && <p className="card-error">{error}</p>}

      {confirmArchive && (
        <ForkDialog
          title={text(`永久完成 ${row.id}？`, `Done for good — ${row.id}?`)}
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
    </article>
  );
}
