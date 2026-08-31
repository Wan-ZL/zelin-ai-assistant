// 回收站页（G4；?page=trash 深链）。行为对齐 mac/Sources/Cards.swift
// TrashSectionView/TrashRow（CONTRACT §9 + §40.5）：
//   - 搜索框客户端过滤 title/summary；
//   - 每行「恢复」→ inbox {action:"restore"}、「永久保存」→ {action:"pin"}（已 pinned 不显）；
//   - purge 倒计时「X 天后永久删除」：天数向上取整、≤7 天红色；pinned 显「已永久保留」；
//     purge_at 缺失/null = 不会自动清 → 不显示倒计时（倒计时绝不许诺不会发生的删除）。
// 动作后不做乐观看板更新——只置行级本地标记（镜像 Mac pinnedLocal），等 SSE → refetch 回流。
import { useState } from "react";
import "../components/chrome/chrome.css";
import { ApiError, postAction } from "../api";
import {
  domainLabel,
  TRASH_KIND_LABELS,
  TRASH_REASON_LABELS,
  useI18n,
  type I18n,
} from "../i18n";
import { buildAppUrl } from "../route";
import { useAppState } from "../store";
import type { TrashRow } from "../types";

/** §40 倒计时：距 purge_at 的整天数（向上取整，明早被清也说 1 天不说 0）；null = 无已知清理 */
function daysUntilPurge(purgeAt: string | null | undefined): number | null {
  if (!purgeAt) return null;
  const t = Date.parse(purgeAt);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.ceil((t - Date.now()) / 86_400_000));
}

/** trashed_at 的相对时间（简化版：天粒度） */
function ageLabel(trashedAt: string, text: I18n["text"]): string | null {
  const t = Date.parse(trashedAt);
  if (Number.isNaN(t)) return null;
  const days = Math.floor((Date.now() - t) / 86_400_000);
  if (days <= 0) return text("今天", "today");
  return text(`${days} 天前`, days === 1 ? "1 day ago" : `${days} days ago`);
}

export function TrashPage() {
  const { text, language } = useI18n();
  const { board } = useAppState();
  const [query, setQuery] = useState("");
  // 行级本地回执（镜像 Mac pinnedLocal）：动作已发出、等 actd 消化 + SSE 回流期间的即时反馈
  const [pinnedLocal, setPinnedLocal] = useState<ReadonlySet<string>>(new Set());
  const [restoredLocal, setRestoredLocal] = useState<ReadonlySet<string>>(new Set());
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [actionError, setActionError] = useState<string | null>(null);

  const items = board?.trash ?? [];
  const needle = query.trim().toLowerCase();
  const filtered = !needle
    ? items
    : items.filter(
        (it) =>
          it.title.toLowerCase().includes(needle)
          || (it.summary ?? "").toLowerCase().includes(needle),
      );

  const mark = (set: ReadonlySet<string>, id: string, on: boolean): ReadonlySet<string> => {
    const next = new Set(set);
    if (on) next.add(id);
    else next.delete(id);
    return next;
  };

  async function submit(action: "restore" | "pin", id: string) {
    setActionError(null);
    setBusyIds((s) => mark(s, id, true));
    try {
      // §3 卡路径动词 wire：只发 action + id；ts 由 server 盖章，comment 省略（可选字段）
      await postAction({ action, id });
      if (action === "pin") setPinnedLocal((s) => mark(s, id, true));
      else setRestoredLocal((s) => mark(s, id, true));
    } catch (error) {
      if (error instanceof ApiError && error.status === 501) {
        setActionError(text(
          "动作通道尚未接线（server 返回 501）——G1 inbox_writer 落地后自动可用。",
          "Action channel not wired yet (server returned 501) — available once G1 inbox_writer lands.",
        ));
      } else {
        setActionError(error instanceof ApiError ? error.message : String(error));
      }
    } finally {
      setBusyIds((s) => mark(s, id, false));
    }
  }

  function renderRow(item: TrashRow) {
    const isPinned = item.permanent || pinnedLocal.has(item.id);
    const isRestored = restoredLocal.has(item.id);
    const isBusy = busyIds.has(item.id);
    const days = daysUntilPurge(item.purge_at);
    const age = ageLabel(item.trashed_at, text);

    return (
      <article key={item.id} className={`trash-row${isRestored ? " is-restored" : ""}`}>
        <div className="trash-row-main">
          <span className="trash-row-text">{item.summary || item.title}</span>
          {isPinned && <span className="chrome-badge is-pinned">{text("永久", "Pinned")}</span>}
        </div>
        <div className="trash-row-meta">
          {item.kind && <span className="chrome-badge">{domainLabel(TRASH_KIND_LABELS, language, item.kind)}</span>}
          {item.trash_reason && <span>{domainLabel(TRASH_REASON_LABELS, language, item.trash_reason)}</span>}
          {age && <span>{age}</span>}
          {isPinned ? (
            <span className="trash-row-purge is-pinned">{text("已永久保留", "Kept forever")}</span>
          ) : (
            days !== null && (
              <span className={`trash-row-purge${days <= 7 ? " is-imminent" : ""}`}>
                {text(`${days} 天后永久删除`, `Deleted for good in ${days}d`)}
              </span>
            )
          )}
        </div>
        <div className="trash-row-actions">
          <button
            type="button"
            className="trash-button is-restore"
            disabled={isBusy || isRestored}
            onClick={() => void submit("restore", item.id)}
          >
            {isRestored ? text("已请求恢复", "Restore requested") : text("恢复", "Restore")}
          </button>
          {!isPinned && (
            <button
              type="button"
              className="trash-button is-pin"
              disabled={isBusy || isRestored}
              onClick={() => void submit("pin", item.id)}
            >
              {text("永久保存", "Pin")}
            </button>
          )}
        </div>
      </article>
    );
  }

  return (
    <main className="trash-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>
        {text("← 返回看板", "← Back to board")}
      </a>
      <div className="trash-page-head">
        <h2 className="trash-page-title">{text("🗑 回收站", "🗑 Trash")}</h2>
        <span className="trash-page-count">{board?.counts?.trash ?? items.length}</span>
      </div>

      <input
        className="chrome-search trash-search"
        type="search"
        placeholder={text("搜索标题 / summary…", "Search title / summary…")}
        aria-label={text("搜索回收站", "Search trash")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />

      {actionError && <p className="trash-action-error" role="alert">{actionError}</p>}

      {filtered.length === 0 ? (
        <p className="trash-empty">
          {items.length === 0 ? text("回收站为空", "Trash is empty") : text("无匹配项", "No matches")}
        </p>
      ) : (
        filtered.map(renderRow)
      )}
    </main>
  );
}
