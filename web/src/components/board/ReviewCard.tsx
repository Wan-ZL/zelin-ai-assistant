// 待验收卡（review 分区项）：三动词——
//   验收（accept → 阶段性完成）· 打回（rework，反馈弹窗；留空时客户端替换成
//   Mac 同款自查指令字面量，见 boardActions.REWORK_EMPTY_FALLBACK）·
//   复制成稿（final_draft 非空时：detail/copyText（Clipboard API → execCommand 兜底）+ 1.5s「已复制 ✓」回执；
//   两条路都失败 → 按钮旁一句「复制失败」短注 4s，不再静默吞掉，原生 Cards.swift:1803-1815；纯客户端）。
// 卡面（原生 ReviewRow 收起态的 meta 行）：会话有新活动（青）· repo 章 · 耗时 <dispatched→review> ·
//   已等待验收 <review→now，自驱走表> · §64 AI 评语章（建议验收/需继续做/需要拍板，点看理由）·
//   一句话（§64 AI 白话摘要优先；判官没评 / 内容已变时回落 delivered_summary、再回落审批时 summary——原生
//   ReviewRow 永远给一句交付说明，Cards.swift:1832-1854；单行截断，hover 全文，同 DoneCard）· 单击复制指令 行。
//   交付了什么（执行器原话，原样全文）/ 摘要 / ☐ 验收清单（§11：永远渲染，空给兜底句）/ 📋 要做什么 /
//   💬 需求来自 / 日志 / 指令 住右侧详情侧栏（「展开详情 ▸」打开，D34；DetailFields 渲染）。
//   评语只是建议：验收 / 打回仍只有下面两个按钮能按。
import { useEffect, useRef, useState } from "react";
import { displayId } from "../../cardId";
import { useI18n } from "../../i18n";
import type { Delivery, ReviewCard as ReviewCardRow } from "../../types";
import { copyText } from "../detail/copyText";
import { cardAction, REWORK_EMPTY_FALLBACK, useSubmit, pendingNote } from "./boardActions";
import { CardHead, CardSurface, CopiedAnnouncer, CopyCommandLine, DetailsToggle, DurationText, MergeStateChip, RepoChip, TerminalButton } from "./cardChrome";
import { TextDialog } from "./TextDialog";
import { AssessmentSummaryLine, VerdictChip } from "./VerdictChip";

interface ReviewCardProps {
  card: ReviewCardRow;
}

/** 复制成稿失败短注的停留时间（成功回执是原生的 1.5 s；失败要给人读完一句） */
export const COPY_FAILED_NOTE_MS = 4000;

/** §65.3 交付核验章：verified → 「PR #n · draft」链接（绿）；否则「PR 未核验：<reason>」（红）；无 delivery 不渲染 */
export function DeliveryChip({ delivery }: { delivery?: Delivery }) {
  const { text } = useI18n();
  if (!delivery) return null;
  if (delivery.verified) {
    const label = `PR #${delivery.pr_number ?? "?"}${delivery.pr_draft ? " · draft" : ""}`;
    return delivery.pr_url ? (
      <a className="chip chip-success" href={delivery.pr_url} target="_blank" rel="noreferrer" data-delivery="verified">
        {label}
      </a>
    ) : (
      <span className="chip chip-success" data-delivery="verified">{label}</span>
    );
  }
  return (
    <span className="chip chip-danger" data-delivery="unverified" title={delivery.reason ?? undefined}>
      {text(`PR 未核验：${delivery.reason ?? "?"}`, `PR unverified: ${delivery.reason ?? "?"}`)}
    </span>
  );
}

export function ReviewCard({ card }: ReviewCardProps) {
  const { text } = useI18n();
  const { pending, pendingAction, error, submit } = useSubmit();
  const [showRework, setShowRework] = useState(false);
  // 复制成稿三态：idle / copied（1.5s 回执，原生 draftCopied）/ failed（短注，原生没有——NSPasteboard 不会失败，浏览器会）
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (copyTimer.current) clearTimeout(copyTimer.current);
  }, []);

  const copyDraft = () => {
    if (!card.final_draft) return;
    void copyText(card.final_draft).then((ok) => {
      setCopyState(ok ? "copied" : "failed");
      if (copyTimer.current) clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopyState("idle"), ok ? 1500 : COPY_FAILED_NOTE_MS);
    });
  };
  const copied = copyState === "copied";

  const title = typeof card.display_title === "string" && card.display_title ? card.display_title : card.name;
  // 原生 ReviewRow：delivered_summary 非空 → 它是正文；否则审批时的 summary（空串 / 纯空白按缺席算——原生 `!ds.isEmpty`
  // 对纯空白会渲染一行看不见的正文再把 summary 降灰，web 单行只有一句，纯空白直接让位给 summary）
  const deliveryFallback = typeof card.delivered_summary === "string" && card.delivered_summary.trim() !== "" ? card.delivered_summary : card.summary;

  return (
    <CardSurface cardId={card.id} label={`${text("待验收", "In review")} · ${title}`} selectable>
      <CardHead card={card} title={title} leading={<span className="card-dot is-review" aria-hidden="true" />} />
      <div className="card-badges">
        <MergeStateChip cardId={card.id} />
        {/* §30 会话再活跃：只是平静地标注，不是打回轮（原生 teal 章） */}
        {card.session_active && <span className="chip chip-accent">{text("会话有新活动", "Session active")}</span>}
        {card.interrupted === true && <span className="chip chip-warning">{text("中断收割", "Interrupted")}</span>}
        {/* §65.3 self_improve 卡：gh 物理核验结果——通过 = PR 章（可点开）；未通过 = 原因 token（红） */}
        <DeliveryChip delivery={card.delivery} />
        <RepoChip path={card.cwd} />
        <DurationText from={card.dispatched_at} to={card.review_at} prefix={text("耗时 ", "took ")} />
        <DurationText from={card.review_at} prefix={text("已等待验收 ", "in review ")} />
        <VerdictChip assessment={card.assessment} />
      </div>
      {/* §64 AI 一句优先；判官没评 / 内容已变（assessment 整键缺席）→ 回落交付说明（原生卡面永远有这一句），执行器原话全文仍住详情侧栏 */}
      <AssessmentSummaryLine assessment={card.assessment} fallback={deliveryFallback} />
      <CopyCommandLine cmd={card.copy_cmd} />
      {pending ? (
        <p className="card-pending-note">{pendingNote(pendingAction, text)}</p>
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
            <>
              <button type="button" className="btn btn-accent" onClick={copyDraft}>
                {copied ? text("已复制 ✓", "Copied ✓") : text("复制成稿", "Copy final draft")}
              </button>
              <CopiedAnnouncer copied={copied} />
              {/* 剪贴板两条路都拒了（非 secure context / 权限拒绝）：短注点名出路，成稿全文在详情侧栏可手动选 */}
              {copyState === "failed" && (
                <span className="card-meta-text is-danger" role="alert">
                  {text("复制失败——到「展开详情 ▸」里手动选中成稿复制", "Copy failed — select the draft under Details ▸ and copy it by hand")}
                </span>
              )}
            </>
          )}
          {card.copy_cmd && <TerminalButton cardId={card.id} />}
          <DetailsToggle cardId={card.id} />
        </div>
      )}
      {error && <p className="card-error">{error}</p>}

      {showRework && (
        <TextDialog
          title={text("↩︎ 打回 · 追加要求", "↩︎ Send Back · Add Requirements")}
          body={text(
            "反馈会送回原会话继续改。留空 = 让 AI 按验收标准自查改进。",
            "Feedback goes back to the session. Leave empty = AI self-reviews against the DoD.",
          )}
          placeholder={text("改哪里…", "What to change…")}
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
