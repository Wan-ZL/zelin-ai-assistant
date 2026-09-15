// 潜在任务卡（debt 行；经 chrome/BacklogStrip 的 renderCard 缝注入——侧条开合归 G4，
// 本组件只管卡面 + 三个动词，Mac DebtRow 同款）：
//   研究并提议（raise → AI 扩写成提案）· 删除（trash → 回收站，可恢复，不弹确认）·
//   永久完成（封存，不再提示）（archive → 永久性完成书立条，可逆不弹确认；原生住右键菜单——
//   web 没有右键惯例，做成动作行里安静的第三颗）。
// §76.2（issue #313）：备选卡也会被雷达盖「疑似已完成」——绿章 + 证据一句，出口用卡上既有的
//   「永久完成（封存）」/「删除」两颗（不开新动词）；状态永远没变过，拍板是 owner 的一次点击。
// 卡面：摘要标题（§37 摘要优先链 cardHeadline = 原生 item.displaySummary，Cards.swift:2028；URL 可点 = 原生 linkified）+ type / 难度 章
//   （原生 hardnessLabel：hard → 较难 红 / soft → 常规 灰 / 其它原样，Cards.swift:2036-2039）；
//   技术标题 + 💬 需求来自 住右侧详情侧栏（「展开详情 ▸」打开，D34）。
import { domainLabel, TYPE_LABELS, useI18n } from "../../i18n";
import type { DebtCard } from "../../types";
import { cardAction, hardnessLabel, pendingNote, useSubmit } from "./boardActions";
import { CardHead, CardSurface, DetailsToggle, SessionHitChip } from "./cardChrome";
import { cardHeadline } from "./cardHeadline";

interface DebtCardItemProps {
  item: DebtCard;
}

export function DebtCardItem({ item }: DebtCardItemProps) {
  const { text, language } = useI18n();
  const { pending, pendingAction, error, submit } = useSubmit();
  const headline = cardHeadline(item) || item.title;
  const hardness = hardnessLabel(item.hardness, text);
  // §76.2 疑似已完成：wire 形 {at, note, channel}，非对象一律当缺席（server 已消毒过一遍）
  const hint = item.completion_hint && typeof item.completion_hint === "object" ? item.completion_hint : null;
  const hintNote = typeof hint?.note === "string" ? hint.note : "";

  return (
    <CardSurface cardId={item.id} label={`${text("潜在任务", "Backlog")} · ${headline}`} selectable>
      {/* 摘要里的 URL 可点（原生 Cards.swift:2028 linkified）；v0.21 契约七：潜在任务卡也可多选参与合并（Kanban.swift:337-339） */}
      <CardHead card={item} title={headline} leading={<span className="card-dot is-backlog" aria-hidden="true" />} linkify />
      <div className="card-badges">
        {/* §37.2 会话层「命中会话」（原生 DebtRow 章行首位，Cards.swift:2034） */}
        <SessionHitChip row={item} />
        {item.type && <span className="chip">{domainLabel(TYPE_LABELS, language, item.type)}</span>}
        {hardness && <span className={item.hardness === "hard" ? "chip chip-danger" : "chip"}>{hardness}</span>}
        {/* §76.2 疑似已完成：雷达扫到「这件事已经发生」的证据——章只说提示，状态一个字没改 */}
        {hint && (
          <span
            className="chip chip-success"
            title={text("雷达在新证据里看到这件事已经发生；状态没有变，怎么处理由你点", "The radar saw evidence this already happened; nothing changed status — the call is yours")}
          >
            {text("✅ 疑似已完成", "✅ Looks already done")}
          </span>
        )}
      </div>
      {/* §76.2 证据一句 + 指向卡上既有的两个出口（封存 / 删除）——不给备选卡开第二套动词 */}
      {hint && hintNote && (
        <p className="card-line is-success is-body">
          <span className="card-detail-label">{text("✅ 证据: ", "✅ Evidence: ")}</span>
          <span>{hintNote}</span>
          <span>{text("（真做完了就「永久完成（封存）」，不用做了就「删除」）", " (if it really is done, seal it with “Done for good”; if it is moot, delete it)")}</span>
        </p>
      )}
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
