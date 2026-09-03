// 会议纪要页右侧详情（CONTRACT §63 / issue #129 §3）：segmented 中文 | English、5 行正文、
// 复制 / 标记已发送 / 重新生成（≤500 字纠正备注）/ OPEN 行「现在生成」/ 开关开着时「投到 Slack 草稿」。
// 唯一出口是剪贴板：复制 = navigator.clipboard + 本地标记；重新生成 / 投草稿走 inbox 特形动作
// （recap_generate / recap_slack_draft，字段逐字按 §63，多一个键 server 400）。
import { useEffect, useState } from "react";
import { ApiError, postAction } from "../../api";
import { useI18n, type Language } from "../../i18n";
import { markRecap } from "../../store";
import type { RecapRow, RecapSettings } from "../../types";
import { copyText } from "../detail/copyText";
import { pickLanguage, recapBody, rowLabel, slackDraftLabel } from "./recapText";

const NOTE_MAX = 500;
const CHANNEL_RE = /^[CDG][A-Z0-9]{6,20}$/;

export interface RecapDetailProps {
  row: RecapRow;
  settings: RecapSettings | null;
}

type Panel = null | "note" | "slack";

export function RecapDetail({ row, settings }: RecapDetailProps) {
  const { text, language: ui } = useI18n();
  const [language, setLanguage] = useState<Language>(pickLanguage(settings?.default_language, ui));
  const [panel, setPanel] = useState<Panel>(null);
  const [note, setNote] = useState("");
  const [channel, setChannel] = useState("");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  // 切行 / 语言设置变化 → 语言与面板复位（草稿备注不跨行）
  useEffect(() => {
    setLanguage(pickLanguage(settings?.default_language, ui));
    setPanel(null);
    setNote("");
    setFlash(null);
  }, [row.key, settings?.default_language, ui]);

  useEffect(() => {
    if (!flash) return;
    const timer = setTimeout(() => setFlash(null), 4000);
    return () => clearTimeout(timer);
  }, [flash]);

  const body = recapBody(row, language);
  const hasText = Boolean(row.en && row.en.length);
  const isOpen = row.status === "open";

  async function run(label: string, action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      setFlash(label);
      setPanel(null);
    } catch (error) {
      setFlash(error instanceof ApiError ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  const copy = () => run(text("已复制到剪贴板", "Copied to clipboard"), async () => {
    const ok = await copyText(body);
    if (!ok) throw new Error(text("复制失败", "Copy failed"));
    await markRecap(row.key, "copied", true);
  });
  const toggleSent = () => run(
    row.sent_at ? text("已取消「已发送」", "Sent mark cleared") : text("已标记为已发送", "Marked as sent"),
    () => markRecap(row.key, "sent", !row.sent_at),
  );
  const regenerate = () => run(text("已排队重新生成，稍后刷新", "Regeneration queued"), () => {
    const payload: Record<string, unknown> = { action: "recap_generate", meeting_key: row.key };
    if (note.trim()) payload.note = note.trim().slice(0, NOTE_MAX);
    return postAction(payload);
  });
  const generateNow = () => run(text("已排队生成阶段稿", "Partial recap queued"), () =>
    postAction({ action: "recap_generate", meeting_key: row.key, partial: true }));
  const slackDraft = () => run(text("已排队投到 Slack 草稿", "Slack draft queued"), () =>
    postAction({ action: "recap_slack_draft", meeting_key: row.key, channel_id: channel.trim() }));

  return (
    <article className="recap-detail" aria-live="polite">
      <header className="recap-detail-head">
        <h3 className="recap-detail-title">{rowLabel(row)}</h3>
        <div className="recap-segmented" role="tablist" aria-label={text("语言", "Language")}>
          {(["zh", "en"] as Language[]).map((lang) => (
            <button
              key={lang}
              type="button"
              role="tab"
              aria-selected={language === lang}
              className={`recap-segment${language === lang ? " is-active" : ""}`}
              onClick={() => setLanguage(lang)}
            >
              {lang === "zh" ? "中文" : "English"}
            </button>
          ))}
        </div>
      </header>

      {hasText ? (
        <pre className="recap-body">{body}</pre>
      ) : (
        <p className="recap-empty">
          {isOpen
            ? text("会议进行中：结束后 5–35 分钟内自动出稿；也可以现在生成一份阶段稿。", "Meeting in progress: the recap lands 5–35 minutes after it ends; you can also generate a partial one now.")
            : text("这场会没有可用正文（无音频 / 转写不全 / 生成失败）。可以重新生成试试。", "No usable text for this meeting (no audio / thin transcript / generation failed). You can try regenerating.")}
        </p>
      )}

      <div className="recap-actions">
        {hasText && (
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void copy()}>
            {text("复制", "Copy")}
          </button>
        )}
        {hasText && !isOpen && (
          <button type="button" className="btn" disabled={busy} onClick={() => void toggleSent()}>
            {row.sent_at ? text("取消已发送", "Unmark sent") : text("标记已发送", "Mark as sent")}
          </button>
        )}
        {isOpen ? (
          <button type="button" className="btn" disabled={busy} onClick={() => void generateNow()}>
            {text("现在生成", "Generate now")}
          </button>
        ) : (
          <button type="button" className="btn" disabled={busy} onClick={() => setPanel(panel === "note" ? null : "note")}>
            {text("重新生成…", "Regenerate…")}
          </button>
        )}
        {settings?.slack_draft_enabled && hasText && !isOpen && (
          <button type="button" className="btn" disabled={busy} onClick={() => setPanel(panel === "slack" ? null : "slack")}>
            {text("投到 Slack 草稿…", "Place in Slack drafts…")}
          </button>
        )}
      </div>

      {panel === "note" && (
        <div className="recap-panel">
          <label className="recap-panel-label" htmlFor="recap-note">
            {text("纠正备注（可选，≤500 字）：告诉模型哪里说错了", "Correction note (optional, ≤500 chars): what to fix")}
          </label>
          <textarea
            id="recap-note"
            className="recap-textarea"
            maxLength={NOTE_MAX}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          <div className="recap-panel-actions">
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void regenerate()}>
              {text("重新生成", "Regenerate")}
            </button>
            <span className="recap-hint">{note.length}/{NOTE_MAX}</span>
          </div>
        </div>
      )}

      {panel === "slack" && (
        <div className="recap-panel">
          <label className="recap-panel-label" htmlFor="recap-channel">
            {text("Slack 会话 id（C… / D… / G…）——草稿进你的「Drafts & Sent」，发送键仍在你手里", "Slack conversation id (C… / D… / G…) — the draft lands in your Drafts & Sent; sending stays yours")}
          </label>
          <input
            id="recap-channel"
            className="settings-input"
            value={channel}
            placeholder="C0123456789"
            spellCheck={false}
            onChange={(event) => setChannel(event.target.value.trim())}
          />
          <div className="recap-panel-actions">
            <button type="button" className="btn btn-primary" disabled={busy || !CHANNEL_RE.test(channel)} onClick={() => void slackDraft()}>
              {text("投到草稿", "Place draft")}
            </button>
          </div>
        </div>
      )}

      <footer className="recap-meta">
        {row.slack_draft?.status && (
          <span className="recap-meta-item">
            {slackDraftLabel(row.slack_draft.status, text)}
            {row.slack_draft.channel_link && (
              <>
                {" · "}
                <a href={row.slack_draft.channel_link} target="_blank" rel="noreferrer">{text("打开会话", "Open conversation")}</a>
              </>
            )}
          </span>
        )}
        {row.quality === "needs_review" && (
          <span className="recap-meta-item">{text("校验未通过，粘贴前请通读一遍。", "Validator flagged this text; read it before pasting.")}</span>
        )}
        {(row.version ?? 0) > 1 && (
          <span className="recap-meta-item">{text(`第 ${row.version} 版`, `Version ${row.version}`)}</span>
        )}
        {row.note && <span className="recap-meta-item">{text("上次备注：", "Last note: ")}{row.note}</span>}
        <span className="recap-meta-item">{text("同室第三人声和系统回声可能混入，粘贴前必读。", "Third-party voices and system echo may leak in — read before pasting.")}</span>
      </footer>

      {flash && <div className="recap-flash" role="status">{flash}</div>}
    </article>
  );
}
