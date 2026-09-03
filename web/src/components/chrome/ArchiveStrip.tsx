// 永久性完成（archived / Done for good）折叠侧条——看板最右的书立条，与左侧 BacklogStrip
// 对称（原生 Kanban.swift v0.33 collapsibleColumn「🗄 永久性完成 · done for good」）。
// 收起 = 竖排窄条只显计数（counts.archived 真实总数）；展开 = 搜索框 + 行列表（原生
// ArchiveLaneContent：title/summary 客户端过滤），每行「放回看板」→ inbox {action:"unarchive"}。
// 归档不是看板列（不进多选/过滤 chips）；顺序保持 server 给的 archived_at 倒序（原生同）。
// 展开态是本地瞬态不进 URL。
import { useState } from "react";
import "./chrome.css";
import { domainLabel, LANE_LABELS, TRASH_KIND_LABELS, useI18n } from "../../i18n";
import { useAppState } from "../../store";
import type { ArchivedRow } from "../../types";
import { cardAction, useSubmit } from "../board/boardActions";
import { CardHead, RelativeTime } from "../board/cardChrome";
import { LaneHelpButton, useLaneHelp } from "../board/Lane";

/** archived prev_status（registry State）→ 用户认识的列名（原生 prevStatusLabel） */
export function prevStatusLabel(status: string, language: "zh" | "en", text: (zh: string, en: string) => string): string {
  switch (status) {
    case "detected": return domainLabel(LANE_LABELS, language, "debt");
    case "raising": case "card_sent": return domainLabel(LANE_LABELS, language, "needs_approval");
    case "approved": case "executing": return domainLabel(LANE_LABELS, language, "running");
    case "review": return domainLabel(LANE_LABELS, language, "review");
    case "delivered": return domainLabel(LANE_LABELS, language, "completed");
    case "merged": return text("已合并", "Merged");
    case "trashed": return domainLabel(LANE_LABELS, language, "trash");
    default: return status;
  }
}

export function ArchiveRow({ item }: { item: ArchivedRow }) {
  const { text, language } = useI18n();
  const { pending, error, submit } = useSubmit();
  const title = typeof item.display_title === "string" && item.display_title ? item.display_title : item.summary || item.title;
  const reason = item.archive_reason === "user"
    ? { label: text("你封存", "You sealed"), cls: "chip chip-success" }
    : item.archive_reason === "auto"
      ? { label: text("自动封存", "Auto-sealed"), cls: "chip" }
      : null;
  return (
    <article className="task-card">
      <CardHead card={item} title={title} />
      <div className="card-badges">
        {reason && <span className={reason.cls}>{reason.label}</span>}
        {item.kind && <span className="chip">{domainLabel(TRASH_KIND_LABELS, language, item.kind)}</span>}
        {typeof item.prev_status === "string" && item.prev_status && (
          <span className="card-meta-text">{text("原来在：", "was in: ")}{prevStatusLabel(item.prev_status, language, text)}</span>
        )}
        <RelativeTime iso={item.archived_at} />
      </div>
      {pending ? (
        <p className="card-pending-note">{text("已提交…", "Submitted…")}</p>
      ) : (
        <div className="card-actions">
          <button type="button" className="btn btn-success" onClick={() => void submit(cardAction(item.id, "unarchive"))}>
            {text("放回看板", "Put back")}
          </button>
        </div>
      )}
      {error && <p className="card-error">{error}</p>}
    </article>
  );
}

export function ArchiveStrip() {
  const { text } = useI18n();
  const { board } = useAppState();
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState("");
  const help = useLaneHelp("archived");

  const all: ArchivedRow[] = board?.archived ?? [];
  const total = board?.counts["archived"] ?? all.length;
  const needle = query.trim().toLowerCase();
  const rows = !needle
    ? all
    : all.filter((it) => it.title.toLowerCase().includes(needle) || (it.summary ?? "").toLowerCase().includes(needle));
  const title = text("永久性完成 · done for good", "Done for good");

  return (
    <aside className={`backlog-strip is-archive${expanded ? "" : " is-collapsed"}`} aria-label={title}>
      <div className="backlog-strip-head">
        <button
          type="button"
          className="backlog-strip-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          <span aria-hidden="true">{expanded ? "▾" : "◂"}</span>
          <span>🗄 {title}</span>
          <span className="backlog-strip-count">{total}</span>
        </button>
        {expanded && help && <LaneHelpButton help={help} />}
      </div>

      {expanded && (
        <div className="backlog-strip-list">
          <input
            type="search"
            className="chrome-search trash-search"
            placeholder={text("搜索标题 / summary…", "Search title / summary…")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {rows.length === 0 && (
            <p className="trash-empty">
              {all.length === 0 ? text("还没有永久完成的卡", "Nothing here yet") : text("无匹配项", "No matches")}
            </p>
          )}
          {rows.map((item) => <ArchiveRow key={item.id} item={item} />)}
          {total > all.length && (
            <p className="column-cap-note">{text(`仅显示最近 ${all.length} 条`, `Showing the latest ${all.length} only`)}</p>
          )}
        </div>
      )}
    </aside>
  );
}
