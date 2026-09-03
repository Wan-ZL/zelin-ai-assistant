// 潜在任务（debt/Backlog）折叠侧条（G4，BUILD-CONTRACT §2.2：「潜在任务(折叠侧条)」）。
// 收起 = 竖排窄条只显计数；展开 = 列表（吃全局过滤 chips + ⌘F 搜索）。
// 行点击 = 开详情抽屉（selectCard + ?card= 深链同步）。「研究并提议」(raise)/「删除」(trash)
// 动词按钮属 A6 卡组件——经 renderCard 注入；缺省渲染只读简行。展开态是本地瞬态不进 URL。
// 行序吃全局排序偏好（store.sortOrder，cardSort.ts）；展开态列头带原生同款「?」说明（server 目录）。
import { useState, type ReactNode } from "react";
import "./chrome.css";
import { sortCards } from "../../cardSort";
import { domainLabel, TYPE_LABELS, useI18n } from "../../i18n";
import { buildAppUrl } from "../../route";
import { selectCard, useAppState } from "../../store";
import { matchesCardFilters } from "../../taskFilters";
import type { DebtCard } from "../../types";
import { LaneHelpButton, useLaneHelp } from "../board/Lane";

export interface BacklogStripProps {
  /** A6 注入真实卡组件（含动词按钮）；不传则渲染只读简行 */
  renderCard?: (card: DebtCard) => ReactNode;
}

export function BacklogStrip({ renderCard }: BacklogStripProps) {
  const { text, language } = useI18n();
  const { board, filters, sortOrder } = useAppState();
  const [expanded, setExpanded] = useState(false);
  const help = useLaneHelp("debt");

  const all = board?.debt ?? [];
  const rows = sortCards(all.filter((card) => matchesCardFilters(card, filters)), sortOrder);
  const countLabel = rows.length === all.length ? `${all.length}` : `${rows.length}/${all.length}`;

  function openCard(id: string) {
    selectCard(id);
    window.history.replaceState(null, "", buildAppUrl(window.location.href, "board", id));
  }

  return (
    <aside className={`backlog-strip${expanded ? "" : " is-collapsed"}`}>
      <div className="backlog-strip-head">
        <button
          type="button"
          className="backlog-strip-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
          <span>{text("潜在任务 · backlog", "Backlog")}</span>
          <span className="backlog-strip-count">{countLabel}</span>
        </button>
        {expanded && help && <LaneHelpButton help={help} />}
      </div>

      {expanded && (
        <div className="backlog-strip-list">
          {rows.length === 0 && (
            <p className="trash-empty">
              {all.length === 0
                ? text("不着急的事会先停在这里——不会自动执行，也永不过期", "Not-urgent items park here — nothing runs on its own, nothing expires")
                : text("无匹配卡片", "No matching cards")}
            </p>
          )}
          {rows.map((card) =>
            renderCard ? (
              <div key={card.id}>{renderCard(card)}</div>
            ) : (
              <button
                key={card.id}
                type="button"
                className="backlog-row"
                onClick={() => openCard(card.id)}
              >
                <span className="backlog-row-title">{card.title}</span>
                <span className="backlog-row-meta">
                  {typeof card.type === "string" && card.type && (
                    <span className="chrome-badge">{domainLabel(TYPE_LABELS, language, card.type)}</span>
                  )}
                  {typeof card.hardness === "string" && card.hardness && (
                    <span className="chrome-badge">{card.hardness}</span>
                  )}
                  {(card.sources ?? [])
                    .map((s) => s.channel)
                    .filter((c, i, arr) => c && arr.indexOf(c) === i)
                    .map((channel) => (
                      <span key={channel}>{channel}</span>
                    ))}
                </span>
              </button>
            ),
          )}
        </div>
      )}
    </aside>
  );
}
