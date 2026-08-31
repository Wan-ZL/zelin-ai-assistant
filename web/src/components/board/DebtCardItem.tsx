// 潜在任务卡（debt 行；经 chrome/BacklogStrip 的 renderCard 缝注入——侧条开合归 G4，
// 本组件只管卡面 + 两个动词，Mac DebtRow 同款）：
//   研究并提议（raise → AI 扩写成提案）· 删除（trash → 回收站，可恢复，不弹确认）。
import { domainLabel, TYPE_LABELS, useI18n } from "../../i18n";
import type { DebtCard } from "../../types";
import { cardAction, openCardDetail, useSubmit } from "./boardActions";

interface DebtCardItemProps {
  item: DebtCard;
}

export function DebtCardItem({ item }: DebtCardItemProps) {
  const { text, language } = useI18n();
  const { pending, error, submit } = useSubmit();
  const summary = typeof item["summary"] === "string" ? (item["summary"] as string) : null;

  return (
    <article className="task-card" onDoubleClick={() => openCardDetail(item.id)}>
      <div className="card-id">{item.id}</div>
      <div className="card-title">{item.title}</div>
      {summary && <p className="card-line">{summary}</p>}
      <div className="card-badges">
        {item.type && <span className="chip">{domainLabel(TYPE_LABELS, language, item.type)}</span>}
        {item.hardness === "hard" && <span className="chip chip-danger">{text("硬需求", "Hard")}</span>}
      </div>
      {pending ? (
        <p className="card-pending-note">
          {text("已提交，AI 分析中（通常 2-3 分钟）", "Submitted; AI is researching (usually 2-3 min)")}
        </p>
      ) : (
        <div className="card-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void submit(cardAction(item.id, "raise"))}
          >
            {text("研究并提议", "Research & propose")}
          </button>
          <button
            type="button"
            className="btn btn-danger"
            onClick={() => void submit(cardAction(item.id, "trash"))}
          >
            {text("删除", "Delete")}
          </button>
        </div>
      )}
      {error && <p className="card-error">{error}</p>}
    </article>
  );
}
