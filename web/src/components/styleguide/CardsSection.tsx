// 第 4 节 Cards：每个 lane 状态一张真卡（真组件 + fixture 数据）——
// 潜在任务 T1 / 潜在任务 T2 / processing 占位 / queued / working / needs-input /
// review / done（+ 老债务行附赠）。卡面 = .task-card 基座（--surface /
// --card-shadow / --border），子状态 class：.is-queued（--surface-muted 虚线框）、
// .is-blocked（--warning 左边条）；sheen 动效行来自 animations.css fork 块。
// §78（D80，issue #447）：提案列与 ProposalCard 一起墓碑，机器卡的样板一律是 DebtCardItem——
// 样板间不许挂一张看板上已经挂不出来的卡（否则退役的动词会从这一页漏回产品里）。
import { DebtCardItem } from "../board/DebtCardItem";
import { DoneCard } from "../board/DoneCard";
import { ReviewCard } from "../board/ReviewCard";
import { RunningCard } from "../board/RunningCard";
import {
  BACKLOG_PROCESSING,
  BACKLOG_T1,
  BACKLOG_T2,
  DEBT_FIXTURE,
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
        <DebtCardItem item={BACKLOG_T1} />
        <SpecimenNote
          zh="潜在任务 T1（DebtCardItem）：徽章全开——tier .chip-purple 粉紫、交付 .chip-purple、紧急截止 .chip-danger.chip-outline 红字、成本 .chip、被提×N .chip-warning.chip-quiet、已并入×N .chip-purple.chip-quiet、green-sign / 回锅 .chip-warning、章行之后「怎样算办完：」紧凑形 .card-dod.is-dod（前 3 条 + 「+N」，空 DoD 不渲染，D43）、分歧行 .card-line.is-warning；落点行「📄 草稿落点: your-workbench（只出文档）」；id 右上角；DoD 全文与其余详情在右侧侧栏（展开详情 ▸ 打开，D34）"
          en="Backlog T1 (DebtCardItem): full badge row — tier .chip-purple pink-magenta, deliver .chip-purple, urgent deadline .chip-danger.chip-outline red, cost .chip, raised ×N .chip-warning.chip-quiet, folded ×N .chip-purple.chip-quiet, green-sign / returned .chip-warning, compact “Definition of done:” .card-dod.is-dod after the badge row (first 3 + “+N”, hidden when empty, D43), disagreement line .card-line.is-warning; target line “📄 Drafts land in: your-workbench”; id top-right; full DoD and the rest of the details live in the right sidebar (Details ▸ opens it, D34)"
        />
      </figure>
      <figure className="sg-specimen">
        <DebtCardItem item={BACKLOG_T2} />
        <SpecimenNote
          zh="潜在任务 T2（DebtCardItem）：促成运行弹键入确认（§41 confirmT2，弹窗里的确认键仍是原生的「批准」）——没看过明细时那颗键换成「T2 需先展开看明细」一句；较难 .chip-danger（hardness=hard，hardnessLabel；--danger / --danger-soft）；落点行「🟠 修改现有: …（只提 draft PR）」.card-line.is-warning"
          en="Backlog T2 (DebtCardItem): Run it opens the typed confirm (§41 confirmT2; the dialog's confirm key keeps the native “Approve”) — before the details were opened the key is replaced by the “T2: expand details first” line; Hard chip .chip-danger (--danger / --danger-soft); target line “🟠 Modify existing: … (draft PR only)” .card-line.is-warning"
        />
      </figure>
      <figure className="sg-specimen">
        <DebtCardItem item={BACKLOG_PROCESSING} />
        <SpecimenNote
          zh="processing 占位（DebtCardItem processing=true）：只有 sheen 动效行（.task-processing-ring，animations.css），无决策按钮"
          en="Processing placeholder (DebtCardItem processing=true): sheen row only (.task-processing-ring, animations.css), no decision buttons"
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
          zh="working 卡（RunningCard）：运行时长（相对时间，hover 绝对）+ repo 章 .chip + sheen 行 + steer 三态回执 chips + 错误一句 .card-line.is-danger；出错 → 让 AI 修（.btn，POST /api/ai-fix）· 回答…（.btn-warning，comment/steer）· 停止"
          en="Working card (RunningCard): run age (relative, absolute on hover) + repo chip .chip + sheen row + tri-state steer chips + error line .card-line.is-danger; on error → Fix with AI (.btn, POST /api/ai-fix) · Answer… (.btn-warning, comment/steer) · Stop"
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
          zh="review 卡（ReviewCard）：meta 行 repo 章 + 耗时 + 已等待验收（自驱走表）；双击整卡 = 在终端接管（D36，卡面无指令行）；卡面永远渲染「☐ 验收清单」紧凑形 .card-dod.is-checklist（前 3 条 + 「+N」，空给兜底句；§64 评语 = 建议验收 → ☑ accent 记号，D43），全文清单 + 交付摘要在详情侧栏（展开详情 ▸ 打开）；三动词（复制成稿仅 final_draft 非空时）"
          en="Review card (ReviewCard): meta line repo chip + took + in review (live); double-click the card = take over in a terminal (D36, no command line on the face); the face always renders the compact “☐ Acceptance checklist” .card-dod.is-checklist (first 3 + “+N”, fallback sentence when empty; §64 verdict Looks done → ☑ accent mark, D43); full list + delivery summary live in the detail sidebar (Details ▸ opens it); three verbs (Copy final draft only with final_draft)"
        />
      </figure>
      <figure className="sg-specimen">
        <DoneCard row={TASK_DONE} />
        <SpecimenNote
          zh="done 卡（DoneCard）：已交付 .chip-success（--success）+ repo 章 + 验收于 <相对时间>（hover 绝对）+ 退回待验收 / 永久完成"
          en="Done card (DoneCard): Delivered .chip-success (--success) + repo chip + accepted <relative> (absolute on hover) + Back to review / Done for good"
        />
      </figure>
      <figure className="sg-specimen">
        <DebtCardItem item={DEBT_FIXTURE} />
        <SpecimenNote
          zh="老债务行（DebtCardItem，server 只发 type / 难度 / 摘要）：tier 章回落「未分级」+ type 词表 .chip + 较难 .chip-danger（hardness=hard，hardnessLabel）；缺席的字段整节不渲染，卡面自然退回 §78 改动前的样子"
          en="Legacy debt row (DebtCardItem with only type / hardness / summary from the server): the tier chip falls back to “Untiered”, plus type table .chip and Hard .chip-danger (hardness=hard, hardnessLabel); absent fields render nothing, so the face falls back to its pre-§78 shape"
        />
      </figure>
    </div>
  );
}
