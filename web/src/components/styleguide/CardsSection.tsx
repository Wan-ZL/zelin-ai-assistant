// 第 4 节 Cards：每个 lane 状态一张真卡（真组件 + fixture 数据）——
// proposal T1 / proposal T2 / processing 占位 / queued / working / needs-input /
// review / done（+ 潜在任务 debt 附赠）。卡面 = .task-card 基座（--surface /
// --card-shadow / --border），子状态 class：.is-queued（--surface-muted 虚线框）、
// .is-blocked（--warning 左边条）；sheen 动效行来自 animations.css fork 块。
import { DebtCardItem } from "../board/DebtCardItem";
import { DoneCard } from "../board/DoneCard";
import { ProposalCard } from "../board/ProposalCard";
import { ReviewCard } from "../board/ReviewCard";
import { RunningCard } from "../board/RunningCard";
import {
  DEBT_FIXTURE,
  PROPOSAL_PROCESSING,
  PROPOSAL_T1,
  PROPOSAL_T2,
  REVIEW_FIXTURE,
  TASK_BLOCKED,
  TASK_DONE,
  TASK_QUEUED,
  TASK_WORKING,
} from "./fixtures";
import { SpecimenNote } from "./SpecimenNote";

export function CardsSection() {
  return (
    <div className="sg-grid">
      <figure className="sg-specimen">
        <ProposalCard card={PROPOSAL_T1} />
        <SpecimenNote
          zh="提案 T1（ProposalCard）：徽章全开——tier .chip-purple 粉紫、交付 .chip-purple、紧急截止 .chip-danger.chip-outline 红字、成本 .chip、被提×N .chip-warning.chip-quiet、green-sign / 回锅 .chip-warning、分歧行 .card-line.is-warning"
          en="Proposal T1 (ProposalCard): full badge row — tier .chip-purple pink-magenta, deliver .chip-purple, urgent deadline .chip-danger.chip-outline red, cost .chip, raised ×N .chip-warning.chip-quiet, green-sign / returned .chip-warning, disagreement line .card-line.is-warning"
        />
      </figure>
      <figure className="sg-specimen">
        <ProposalCard card={PROPOSAL_T2} />
        <SpecimenNote
          zh="提案 T2（ProposalCard）：批准弹键入确认（§41 confirmT2）；硬需求 .chip-danger（--danger / --danger-soft）"
          en="Proposal T2 (ProposalCard): Approve opens typed confirm (§41 confirmT2); Hard chip .chip-danger (--danger / --danger-soft)"
        />
      </figure>
      <figure className="sg-specimen">
        <ProposalCard card={PROPOSAL_PROCESSING} />
        <SpecimenNote
          zh="processing 占位（ProposalCard processing=true）：只有 sheen 动效行（.task-processing-ring，animations.css），无决策按钮"
          en="Processing placeholder (ProposalCard processing=true): sheen row only (.task-processing-ring, animations.css), no decision buttons"
        />
      </figure>
      <figure className="sg-specimen">
        <RunningCard row={TASK_QUEUED} />
        <SpecimenNote
          zh="queued 灰卡（RunningCard state=queued）：.task-card.is-queued（--surface-muted + 虚线框）；排队原因中性 .chip、派发错误 .chip-danger"
          en="Queued grey card (RunningCard state=queued): .task-card.is-queued (--surface-muted + dashed border); reason neutral .chip, dispatch error .chip-danger"
        />
      </figure>
      <figure className="sg-specimen">
        <RunningCard row={TASK_WORKING} />
        <SpecimenNote
          zh="working 卡（RunningCard）：sheen 行 + steer 三态回执 chips + 告警行 .card-line.is-warning"
          en="Working card (RunningCard): sheen row + tri-state steer chips + warning line .card-line.is-warning"
        />
      </figure>
      <figure className="sg-specimen">
        <RunningCard row={TASK_BLOCKED} isBlocked />
        <SpecimenNote
          zh="needs-input 卡（RunningCard isBlocked）：.is-blocked 左边条（--warning）；需输入 .chip-warning 橙、恢复放弃 .chip-danger 红、等待 .chip-notice 黄、问题正文警示行"
          en="Needs-input card (RunningCard isBlocked): .is-blocked left bar (--warning); Input .chip-warning orange, resume-exhausted .chip-danger red, waiting .chip-notice yellow, question as warning line"
        />
      </figure>
      <figure className="sg-specimen">
        <ReviewCard card={REVIEW_FIXTURE} />
        <SpecimenNote
          zh="review 卡（ReviewCard）：交付摘要 + DoD 验收清单 + 三动词（复制成稿仅 final_draft 非空时）"
          en="Review card (ReviewCard): delivery summary + DoD checklist + three verbs (Copy final draft only with final_draft)"
        />
      </figure>
      <figure className="sg-specimen">
        <DoneCard row={TASK_DONE} />
        <SpecimenNote
          zh="done 卡（DoneCard）：验收于 .chip-success（--success）+ 退回待验收 / 永久完成"
          en="Done card (DoneCard): accepted chip .chip-success (--success) + Back to review / Done for good"
        />
      </figure>
      <figure className="sg-specimen">
        <DebtCardItem item={DEBT_FIXTURE} />
        <SpecimenNote
          zh="潜在任务卡（DebtCardItem，附赠）：type 词表 .chip + 硬需求 .chip-danger"
          en="Backlog card (DebtCardItem, bonus): type table .chip + Hard .chip-danger"
        />
      </figure>
    </div>
  );
}
