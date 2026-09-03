// 永久性完成页（原生 MainWindow ArchivePageView / §54.1 第 6 项的整页形态 → §68.11；?page=archive）。
// 与右侧书立条 ArchiveStrip 同一行组件（ArchiveRow：你封存 / 自动封存、原来在、相对时间、
// 「放回看板」= unarchive），只是给一整页 + 搜索——57 张以上的封存卡在窄条里翻不动。
import { useState } from "react";
import "../components/chrome/chrome.css";
import { ArchiveRow } from "../components/chrome/ArchiveStrip";
import { useI18n } from "../i18n";
import { buildAppUrl } from "../route";
import { useAppState } from "../store";
import type { ArchivedRow } from "../types";

export function ArchivePage() {
  const { text } = useI18n();
  const { board } = useAppState();
  const [query, setQuery] = useState("");
  const all: ArchivedRow[] = board?.archived ?? [];
  const total = board?.counts["archived"] ?? all.length;
  const needle = query.trim().toLowerCase();
  const rows = !needle
    ? all
    : all.filter((it) => it.title.toLowerCase().includes(needle) || (it.summary ?? "").toLowerCase().includes(needle));

  return (
    <main className="trash-page archive-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>
        {text("← 返回看板", "← Back to board")}
      </a>
      <div className="trash-page-head">
        <h2 className="trash-page-title">{text("🗄 永久性完成", "🗄 Done for good")}</h2>
        <span className="trash-page-count">{total}</span>
      </div>
      <p className="column-help">
        {text("彻底结束、封存的线程：不再参与匹配，后续相关信息会开新卡。「放回看板」回到原状态列。", "Threads that are truly over: excluded from matching, later mentions open a fresh card. \"Put back\" returns one to its previous lane.")}
      </p>
      <input
        className="chrome-search trash-search"
        type="search"
        placeholder={text("搜索标题 / summary…", "Search title / summary…")}
        aria-label={text("搜索永久性完成", "Search archived")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {total > all.length && <p className="column-cap-note">{text(`仅显示最近 ${all.length} 条`, `Showing the latest ${all.length} only`)}</p>}
      {rows.length === 0 ? (
        <p className="trash-empty">{all.length === 0 ? text("还没有封存的卡", "Nothing archived yet") : text("无匹配项", "No matches")}</p>
      ) : (
        <div className="archive-page-list">{rows.map((item) => <ArchiveRow key={item.id} item={item} />)}</div>
      )}
    </main>
  );
}
