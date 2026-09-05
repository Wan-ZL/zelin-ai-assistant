// 回收站页（G4；?page=trash 深链）。行为对齐 mac/Sources/Cards.swift
// TrashSectionView/TrashRow（CONTRACT §9 + §37 + §40.5）：
//   - 行标题 = §37 摘要优先面 cardHeadline（钦定名 > summary > display_title > title；原生 TrashItem.displaySummary，
//     Cards.swift:2594）——改过名再删的卡在回收站也显示你起的名字；搜索框客户端过滤 title/summary/display_title；
//   - 每行「恢复」→ inbox {action:"restore"}、「永久保存」→ {action:"pin"}（已 pinned 不显）；
//   - purge 倒计时「X 天后永久删除」：天数向上取整、≤7 天红色；pinned 显「已永久保留」；
//     purge_at 缺失/null = 不会自动清 → 不显示倒计时（倒计时绝不许诺不会发生的删除）。
// 动作后不做乐观看板更新：每行一个 useSubmit（与永久性完成书立条的 ArchiveRow 同款）——恢复中显示原生那句
// 「恢复中，卡片将回到原状态列」（Store.swift beginReturn），卡离开 trash 才解锁；180 s 没动 → 原生
// 「恢复超时，卡片仍在回收站，可重试（检查 actd 是否在运行）」，行恢复可操作（不再永远挂着「已请求恢复」）。
// 「永久」章的本地回执（镜像 Mac pinnedLocal）在 backend 回 permanent 后退场（PendingSweep.swift:277-279）。
import { useEffect, useState } from "react";
import "../components/chrome/chrome.css";
import { useSubmit } from "../components/board/boardActions";
import { RelativeTime } from "../components/board/cardChrome";
import { cardHeadline } from "../components/board/cardHeadline";
import {
  domainLabel,
  TRASH_KIND_LABELS,
  TRASH_REASON_LABELS,
  useI18n,
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

/** 搜索命中：title / summary / display_title 三个字段之一（原生 TrashSectionView 过滤 + §37 展示名） */
export function trashRowMatches(item: TrashRow, needle: string): boolean {
  const fields = [item.title, item.summary, item.display_title];
  return fields.some((f) => typeof f === "string" && f.toLowerCase().includes(needle));
}

export function TrashRowView({ item }: { item: TrashRow }) {
  const { text, language } = useI18n();
  const { pending, pendingAction, error, submit } = useSubmit();
  // 行级本地回执（镜像 Mac pinnedLocal）：pin 已发出、等 actd 消化 + 回流期间章先翻；backend 说 permanent 了它就多余
  const [pinnedLocal, setPinnedLocal] = useState(false);
  useEffect(() => {
    if (item.permanent && pinnedLocal) setPinnedLocal(false);
  }, [item.permanent, pinnedLocal]);
  useEffect(() => {
    // 180 s 兜底解锁时 backend 还没说 permanent：章是本地翻的，没有真凭据就收回（超时句已说明原因）
    if (!pending && error !== null && pinnedLocal && !item.permanent) setPinnedLocal(false);
  }, [pending, error, pinnedLocal, item.permanent]);

  const isPinned = item.permanent || pinnedLocal;
  const isRestoring = pending && pendingAction === "restore";
  const days = daysUntilPurge(item.purge_at);
  const headline = cardHeadline(item) || item.title;

  const pin = async () => {
    // §3 卡路径动词 wire：只发 action + id；ts 由 server 盖章，comment 省略（可选字段）
    const ok = await submit({ action: "pin", id: item.id });
    if (ok) setPinnedLocal(true);
  };

  return (
    <article className={`trash-row${isRestoring ? " is-restored" : ""}`}>
      <div className="trash-row-main">
        <span className="trash-row-text">{headline}</span>
        {isPinned && <span className="chrome-badge is-pinned">{text("永久", "Pinned")}</span>}
      </div>
      <div className="trash-row-meta">
        {item.kind && <span className="chrome-badge">{domainLabel(TRASH_KIND_LABELS, language, item.kind)}</span>}
        {item.trash_reason && <span>{domainLabel(TRASH_REASON_LABELS, language, item.trash_reason)}</span>}
        {/* trashed_at 相对时间（原生 RelativeTime.since：刚刚/N分钟前/N小时前/N天前），hover 绝对 */}
        <RelativeTime iso={item.trashed_at} />
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
      {pending ? (
        // 原生 beginReturn 的信息条（restore）；pin 没有换列，只有章先翻——等回流期间按钮行整体禁用
        <p className="card-pending-note">
          {isRestoring ? text("恢复中，卡片将回到原状态列", "Restoring — the card returns to its previous lane") : text("已提交…", "Submitted…")}
        </p>
      ) : (
        <div className="trash-row-actions">
          <button
            type="button"
            className="trash-button is-restore"
            onClick={() => void submit({ action: "restore", id: item.id })}
          >
            {text("恢复", "Restore")}
          </button>
          {!isPinned && (
            <button type="button" className="trash-button is-pin" onClick={() => void pin()}>
              {text("永久保存", "Pin")}
            </button>
          )}
        </div>
      )}
      {error && <p className="trash-action-error" role="alert">{error}</p>}
    </article>
  );
}

export function TrashPage() {
  const { text } = useI18n();
  const { board } = useAppState();
  const [query, setQuery] = useState("");

  const items = board?.trash ?? [];
  const needle = query.trim().toLowerCase();
  const filtered = !needle ? items : items.filter((it) => trashRowMatches(it, needle));

  return (
    <main className="trash-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>
        {text("← 返回看板", "← Back to board")}
      </a>
      <div className="trash-page-head">
        <h2 className="trash-page-title">{text("🗑 回收站 · trash", "🗑 Trash")}</h2>
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

      {filtered.length === 0 ? (
        <p className="trash-empty">
          {items.length === 0 ? text("回收站为空", "Trash is empty") : text("无匹配项", "No matches")}
        </p>
      ) : (
        filtered.map((item) => <TrashRowView key={item.id} item={item} />)
      )}
    </main>
  );
}
