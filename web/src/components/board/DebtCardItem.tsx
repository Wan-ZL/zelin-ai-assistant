// 潜在任务卡（debt 行；经 chrome/BacklogStrip 的 renderCard 缝注入——侧条开合归 G4，
// 本组件只管卡面 + 三个动词，Mac DebtRow 同款）：
//   研究并提议（raise → AI 扩写成提案）· 删除（trash → 回收站，可恢复，不弹确认）·
//   永久完成（封存，不再提示）（archive → 永久性完成书立条，可逆不弹确认；原生住右键菜单——
//   web 没有右键惯例，做成动作行里安静的第三颗）。
// 卡面：摘要标题（§37 摘要优先链 cardHeadline = 原生 item.displaySummary，Cards.swift:2028；URL 可点 = 原生 linkified）+ type / 难度 章
//   （原生 hardnessLabel：hard → 较难 红 / soft → 常规 灰 / 其它原样，Cards.swift:2036-2039）；
//   技术标题 + 💬 需求来自 住右侧详情侧栏（「展开详情 ▸」打开，D34）。
import { domainLabel, TYPE_LABELS, useI18n } from "../../i18n";
import type { DebtCard } from "../../types";
import { cardAction, hardnessLabel, pendingNote, useSubmit } from "./boardActions";
import { CardHead, CardSurface, DetailsToggle } from "./cardChrome";
import { cardHeadline } from "./cardHeadline";

interface DebtCardItemProps {
  item: DebtCard;
}

export function DebtCardItem({ item }: DebtCardItemProps) {
  const { text, language } = useI18n();
  const { pending, pendingAction, error, submit } = useSubmit();
  const headline = cardHeadline(item) || item.title;
  const hardness = hardnessLabel(item.hardness, text);

  return (
    <CardSurface cardId={item.id} label={`${text("潜在任务", "Backlog")} · ${headline}`}>
      {/* 摘要里的 URL 可点（原生 Cards.swift:2028 linkified） */}
      <CardHead card={item} title={headline} leading={<span className="card-dot is-backlog" aria-hidden="true" />} linkify />
      <div className="card-badges">
        {item.type && <span className="chip">{domainLabel(TYPE_LABELS, language, item.type)}</span>}
        {hardness && <span className={item.hardness === "hard" ? "chip chip-danger" : "chip"}>{hardness}</span>}
      </div>
      {pending ? (
        <p className="card-pending-note">
          {pendingAction === "raise"
            ? text("已提交，AI 分析中（通常 2-3 分钟）", "Submitted; AI is researching (usually 2-3 min)")
            : pendingNote(pendingAction, text)}
        </p>
      ) : (
        <div className="card-actions">
          {/* 色相 = Mac DebtRow tint：蓝研究并提议 · 红删除 · 灰封存 */}
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
          <button
            type="button"
            className="btn"
            title={text("封存这条：留作记录、不再参与匹配，也就不会再被提起（可从永久性完成放回）", "Seal it: kept as a record, excluded from matching so it never re-suggests (can be put back from Done for good)")}
            onClick={() => void submit(cardAction(item.id, "archive"))}
          >
            {text("永久完成（封存，不再提示）", "Done for good (seal, stop suggesting)")}
          </button>
          <DetailsToggle cardId={item.id} />
        </div>
      )}
      {error && <p className="card-error">{error}</p>}
    </CardSurface>
  );
}
