// 待验收卡（review 分区项）：交付摘要 + DoD 验收清单 + 三动词——
//   验收（accept → 阶段性完成）· 打回（rework，反馈弹窗；留空时客户端替换成
//   Mac 同款自查指令字面量，见 boardActions.REWORK_EMPTY_FALLBACK）·
//   复制成稿（final_draft 非空时，剪贴板 + 1.5s「已复制 ✓」回执，纯客户端）。
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../../i18n";
import type { ReviewCard as ReviewCardRow } from "../../types";
import { cardAction, openCardDetail, REWORK_EMPTY_FALLBACK, useSubmit } from "./boardActions";
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

  return (
    <article className="task-card" onDoubleClick={() => openCardDetail(card.id)}>
      <div className="card-id">{card.id}</div>
      <div className="card-title">{card.name}</div>
      {card.delivered_summary && <p className="card-summary">{card.delivered_summary}</p>}
      {card.dod.length > 0 ? (
        <div className="card-line">
          {text("验收清单——逐条对照：", "Acceptance checklist:")}
          <ul style={{ margin: "2px 0 0", paddingLeft: 18 }}>
            {card.dod.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="card-line">
          {text("该任务未定义验收标准，请自行判断", "No acceptance criteria defined; use your judgement")}
        </p>
      )}
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
        </div>
      )}
      {error && <p className="card-error">{error}</p>}

      {showRework && (
        <TextDialog
          title={text(`打回 ${card.id}`, `Send back ${card.id}`)}
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
    </article>
  );
}
