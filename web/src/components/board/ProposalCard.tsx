// 提案卡（needs_approval 分区项，含 raising 占位）。四颗决策动词镜像 Mac v0.21 拍板：
//   批准（T2 走 typed-confirm 弹窗，wire 不变）· 拒绝（fork：不想做 reject / 已办完
//   done_external，§41）· 修改（comment 文本弹窗）· 暂缓（defer，提案→潜在任务）。
// processing=true 的灰卡是 AI 研究中占位——只展示 sheen，不给决策按钮。
import { useState } from "react";
import { useI18n } from "../../i18n";
import type { ApprovalCard } from "../../types";
import { cardAction, costLine, effectiveTier, openCardDetail, useSubmit } from "./boardActions";
import { ForkDialog } from "./ForkDialog";
import { T2ConfirmDialog } from "./T2ConfirmDialog";
import { TextDialog } from "./TextDialog";

interface ProposalCardProps {
  card: ApprovalCard;
}

type DialogKind = "none" | "t2" | "reject" | "comment";

export function ProposalCard({ card }: ProposalCardProps) {
  const { text } = useI18n();
  const { pending, error, submit } = useSubmit();
  const [dialog, setDialog] = useState<DialogKind>("none");

  const summary = typeof card.summary === "string" && card.summary ? card.summary : card.title;

  if (card.processing) {
    // raising 占位：dashboard.py 对 status=raising 发的形状（cf. demo_seed R-104）
    return (
      <article className="task-card" onDoubleClick={() => openCardDetail(card.id)}>
        <div className="card-title">{card.title}</div>
        <div className="task-processing-row is-running">
          <span className="task-processing-ring" aria-hidden="true"><span /></span>
          <span className="task-processing-label">
            {text("AI 研究中，完成后变成正式提案", "AI is researching; becomes a proposal when done")}
          </span>
        </div>
      </article>
    );
  }

  const decide = (action: string, comment: string | null = null) => {
    setDialog("none");
    void submit(cardAction(card.id, action, comment));
  };

  return (
    <article className="task-card" onDoubleClick={() => openCardDetail(card.id)}>
      <div className="card-id">{card.id}</div>
      <div className="card-summary">{summary}</div>
      <div className="card-badges">
        {/* tier 章 = Mac systemPurple 粉紫（owner 验收单：粉紫T1章）；交付 tag 同紫（§10 提取表拍板） */}
        <span className="chip chip-purple">{card.tier}{card.tier_hint ? ` · ${card.tier_hint}` : ""}</span>
        {card.delivery_mode === "chat" && (
          <span className="chip chip-purple">{text("交付：聊天成稿", "Deliver: chat draft")}</span>
        )}
        {/* 紧急截止 = Mac 红字——outline 档红 chip（文字前置），非紧急保持中性 */}
        {card.deadline && (
          <span className={typeof card.days_left === "number" && card.days_left <= 3 ? "chip chip-danger chip-outline" : "chip"}>
            {card.deadline}
            {typeof card.days_left === "number" ? text(`（剩 ${card.days_left} 天）`, ` (${card.days_left}d left)`) : ""}
          </span>
        )}
        {card.show_cost && typeof card.cost_usd === "number" && (
          <span className="chip">${card.cost_usd}</span>
        )}
        {card.hardness === "hard" && <span className="chip chip-danger">{text("硬需求", "Hard")}</span>}
        {/* 被提×N 是 lineage 计数——quiet 档，比状态 chip 安静 */}
        {typeof card.repeated === "number" && card.repeated > 1 && (
          <span className="chip chip-warning chip-quiet">{text(`被提×${card.repeated}`, `Raised ×${card.repeated}`)}</span>
        )}
        {card.green_sign && (
          <span className="chip chip-warning">
            {text("需 manager green-sign（只出草稿）", "Needs manager green-sign (draft only)")}
          </span>
        )}
        {card.reraised && <span className="chip chip-warning">{text("↩︎ 回锅", "↩︎ Returned")}</span>}
      </div>
      {card.reraised && card.reraised_note && <p className="card-line is-warning">{card.reraised_note}</p>}
      {card.disagreement && (
        <p className="card-line is-warning">{text("⚠ 有分歧：", "⚠ Disagreement: ")}{card.disagreement}</p>
      )}
      {pending ? (
        <p className="card-pending-note">{text("已提交…", "Submitted…")}</p>
      ) : (
        <div className="card-actions">
          {/* 四动词色相 = Mac tint 一比一（Cards.swift normalBody）：绿批准 · 红拒绝 · 蓝修改 · 灰暂缓 */}
          <button
            type="button"
            className="btn btn-success"
            // W17（§50）：typed-confirm 闸门读 effective_tier——外部升档卡
            // （声明 T1、生效 T2）也必须过确认词，绝不单击直批
            onClick={() => (effectiveTier(card) === "T2" ? setDialog("t2") : decide("approve"))}
          >
            {text("批准", "Approve")}
          </button>
          <button type="button" className="btn btn-danger" onClick={() => setDialog("reject")}>
            {text("拒绝", "Reject")}
          </button>
          <button type="button" className="btn btn-info" onClick={() => setDialog("comment")}>
            {text("修改", "Comment")}
          </button>
          <button type="button" className="btn" onClick={() => decide("defer")}>
            {text("暂缓", "Later")}
          </button>
        </div>
      )}
      {error && <p className="card-error">{error}</p>}

      {dialog === "t2" && (
        <T2ConfirmDialog
          cardId={card.id}
          summary={summary}
          costLine={costLine(card, text)}
          onConfirm={() => decide("approve")}
          onCancel={() => setDialog("none")}
        />
      )}
      {dialog === "reject" && (
        <ForkDialog
          title={text(`拒绝 ${card.id}？`, `Reject ${card.id}?`)}
          body={summary}
          choices={[
            { label: text("不想做（进回收站）", "Won't do (to trash)"), isDanger: true, onPick: () => decide("reject") },
            { label: text("已办完（记为已交付）", "Already done (mark delivered)"), onPick: () => decide("done_external") },
          ]}
          onCancel={() => setDialog("none")}
        />
      )}
      {dialog === "comment" && (
        <TextDialog
          title={text("修改方向", "Change of direction")}
          body={text("你的意见会并入计划，卡片重新等待审批。", "Your input folds into the plan; the card waits for re-approval.")}
          placeholder={text("想怎么改？", "What should change?")}
          submitLabel={text("提交", "Submit")}
          onSubmit={(t) => decide("comment", t)}
          onCancel={() => setDialog("none")}
        />
      )}
    </article>
  );
}
