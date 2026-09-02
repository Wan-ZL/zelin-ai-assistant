// 潜在任务卡（debt 行；经 chrome/BacklogStrip 的 renderCard 缝注入——侧条开合归 G4，
// 本组件只管卡面 + 两个动词，Mac DebtRow 同款）：
//   研究并提议（raise → AI 扩写成提案）· 删除（trash → 回收站，可恢复，不弹确认）。
// 卡面：摘要标题 + type / 硬需求 章；「展开详情 ▸」后：技术标题 + 💬 需求来自。
import { domainLabel, TYPE_LABELS, useI18n } from "../../i18n";
import type { DebtCard } from "../../types";
import { cardAction, useSubmit } from "./boardActions";
import { CardDetails, CardHead, CardSurface, DetailsToggle } from "./cardChrome";
import { SourceList } from "./detailBlocks";

interface DebtCardItemProps {
  item: DebtCard;
}

export function DebtCardItem({ item }: DebtCardItemProps) {
  const { text, language } = useI18n();
  const { pending, error, submit } = useSubmit();
  const summary = typeof item.summary === "string" && item.summary ? item.summary : item.title;
  const displayTitle = typeof item.display_title === "string" && item.display_title ? item.display_title : summary;

  return (
    <CardSurface cardId={item.id} label={`${text("潜在任务", "Backlog")} · ${displayTitle}`}>
      <CardHead card={item} title={displayTitle} leading={<span className="card-dot is-backlog" aria-hidden="true" />} />
      <div className="card-badges">
        {item.type && <span className="chip">{domainLabel(TYPE_LABELS, language, item.type)}</span>}
        {item.hardness === "hard" && <span className="chip chip-danger">{text("硬需求", "Hard")}</span>}
      </div>
      <CardDetails cardId={item.id}>
        {item.title !== displayTitle && <p className="card-detail-title">{item.title}</p>}
        <SourceList sources={item.sources} />
      </CardDetails>
      {pending ? (
        <p className="card-pending-note">
          {text("已提交，AI 分析中（通常 2-3 分钟）", "Submitted; AI is researching (usually 2-3 min)")}
        </p>
      ) : (
        <div className="card-actions">
          {/* 色相 = Mac DebtRow tint：蓝研究并提议 · 红删除 */}
          <button
            type="button"
            className="btn btn-info"
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
          <DetailsToggle cardId={item.id} />
        </div>
      )}
      {error && <p className="card-error">{error}</p>}
    </CardSurface>
  );
}
