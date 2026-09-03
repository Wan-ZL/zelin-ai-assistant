// 待验收卡（review 分区项）：三动词——
//   验收（accept → 阶段性完成）· 打回（rework，反馈弹窗；留空时客户端替换成
//   Mac 同款自查指令字面量，见 boardActions.REWORK_EMPTY_FALLBACK）·
//   复制成稿（final_draft 非空时，剪贴板 + 1.5s「已复制 ✓」回执，纯客户端）。
// 卡面（原生 ReviewRow 收起态的 meta 行）：会话有新活动（青）· repo 章 · 耗时 <dispatched→review> ·
//   已等待验收 <review→now，自驱走表> · 单击复制指令 行；「展开详情 ▸」后：交付了什么 /
//   摘要 / ☐ 验收清单（§11：永远渲染，空给兜底句）/ 📋 要做什么 / 💬 需求来自 / 日志 / 指令。
import { useEffect, useRef, useState } from "react";
import { displayId } from "../../cardId";
import { useI18n } from "../../i18n";
import type { ReviewCard as ReviewCardRow } from "../../types";
import { cardAction, REWORK_EMPTY_FALLBACK, useSubmit } from "./boardActions";
import { CardDetails, CardHead, CardSurface, CopyCommandLine, DetailsToggle, DurationText, RepoChip } from "./cardChrome";
import { BodyText, CopyPathLine, DodList, MetaLine, PlanList, SourceList } from "./detailBlocks";
import { TextDialog } from "./TextDialog";

interface ReviewCardProps {
  card: ReviewCardRow;
}

export function ReviewCard({ card }: ReviewCardProps) {
  const { text } = useI18n();
  const { pending, error, submit } = useSubmit();
  const [showRework, setShowRework] = useState(false);
  const [copied, setCopied] = useState(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (copyTimer.current) clearTimeout(copyTimer.current);
  }, []);

  const copyDraft = () => {
    if (!card.final_draft) return;
    void navigator.clipboard.writeText(card.final_draft).then(() => {
      setCopied(true);
      if (copyTimer.current) clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(false), 1500);
    });
  };

  const title = typeof card.display_title === "string" && card.display_title ? card.display_title : card.name;

  return (
    <CardSurface cardId={card.id} label={`${text("待验收", "In review")} · ${title}`}>
      <CardHead card={card} title={title} leading={<span className="card-dot is-review" aria-hidden="true" />} />
      <div className="card-badges">
        {/* §30 会话再活跃：只是平静地标注，不是打回轮（原生 teal 章） */}
        {card.session_active && <span className="chip chip-accent">{text("会话有新活动", "Session active")}</span>}
        {card.interrupted === true && <span className="chip chip-warning">{text("中断收割", "Interrupted")}</span>}
        <RepoChip path={card.cwd} />
        <DurationText from={card.dispatched_at} to={card.review_at} prefix={text("耗时 ", "took ")} />
        <DurationText from={card.review_at} prefix={text("已等待验收 ", "in review ")} />
      </div>
      <CopyCommandLine cmd={card.copy_cmd} />
      <CardDetails cardId={card.id}>
        {card.delivered_summary ? (
          <>
            {/* v0.10：执行器实际交付的 = 正文；审批时摘要降为灰色上下文 */}
            <div className="card-detail-subheading">{text("交付了什么：", "Delivered:")}</div>
            <BodyText value={card.delivered_summary} />
            <BodyText value={card.summary} className="card-detail-muted" />
          </>
        ) : (
          <BodyText value={card.summary} />
        )}
        <DodList dod={card.dod} heading={text("验收清单——逐条对照：", "Acceptance checklist:")} checklist />
        <PlanList plan={card.plan} />
        <SourceList sources={card.sources} />
        <CopyPathLine label={text("日志：", "Log: ")} path={card.log} />
        <CopyPathLine label={text("指令：", "Command: ")} path={card.copy_cmd} />
        <MetaLine label={text("claude agents 列表名：", "claude agents list name: ")} value={card.agent_name} />
      </CardDetails>
      {pending ? (
        <p className="card-pending-note">{text("已提交…", "Submitted…")}</p>
      ) : (
        <div className="card-actions">
          {/* 三动词色相 = Mac tint 一比一：绿验收 · 橙打回 · 青复制成稿 */}
          <button
            type="button"
            className="btn btn-success"
            onClick={() => void submit(cardAction(card.id, "accept"))}
          >
            {text("验收", "Accept")}
          </button>
          <button type="button" className="btn btn-warning" onClick={() => setShowRework(true)}>
            {text("打回", "Send Back")}
          </button>
          {card.final_draft && (
            <button type="button" className="btn btn-accent" onClick={copyDraft}>
              {copied ? text("已复制 ✓", "Copied ✓") : text("复制成稿", "Copy final draft")}
            </button>
          )}
          <DetailsToggle cardId={card.id} />
        </div>
      )}
      {error && <p className="card-error">{error}</p>}

      {showRework && (
        <TextDialog
          title={text(`打回 ${displayId(card)}`, `Send back ${displayId(card)}`)}
          body={text(
            "反馈会送回原会话继续改。留空 = 让 AI 按验收标准自查改进。",
            "Feedback goes back to the session. Leave empty = AI self-reviews against the DoD.",
          )}
          placeholder={text("哪里不对？想怎么改？", "What's wrong? What should change?")}
          submitLabel={text("打回", "Send Back")}
          allowEmpty
          onSubmit={(t) => {
            setShowRework(false);
            // 空反馈替换为 Mac 同款字面量——客户端行为，actd 不做此替换（inbox-actions.md R9）
            void submit(cardAction(card.id, "rework", t || REWORK_EMPTY_FALLBACK));
          }}
          onCancel={() => setShowRework(false)}
        />
      )}
    </CardSurface>
  );
}
