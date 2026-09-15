// 会议纪要页右侧详情（CONTRACT §63 / issue #129 §3）：segmented 中文 | English、5 行正文、
// 复制 / 标记已发送 / 重新生成（≤500 字纠正备注）/ OPEN 行「现在生成」/ 开关开着时「投到 Slack 草稿」。
// 唯一出口是剪贴板：复制 = navigator.clipboard + 本地标记（§63.5 追记：写出去的是「一行表头 + 5 行正文」，
// 表头与 h3 同一个 recapHeader(row, language)——所见即所复制；存储仍恰是 5 行）；重新生成 / 投草稿走 inbox 特形动作
// （recap_generate / recap_slack_draft，字段逐字按 §63，多一个键 server 400）。
// §63.8（issue #297）：重新生成排队后面板不再装死——状态行说「排队中 / 正在生成」、两颗生成按钮禁用，
// 新版本随 board 回流落地时闪一句「已更新到第 N 版」（落地无正文则按 quality 说清）；90 s 没人接手说
// 「actd 可能没在跑」并解锁按钮；actd 回执 lost / noop 各一句人话。
// §63.3 追记（issue #298）：needs_review 不再只有一句「校验未通过」——脚注按 wire 的结构化 problems[]
// 逐条说清语言 + 行号 + 超出量（纠正备注可以照着写），落地前被自动修剪的行按 repairs[] 一并摊开。
// §63.5 追记（issue #301）：CLOSED 行多一颗「忽略 / 恢复」（POST /api/recaps/mark dismissed，与
// 「标记已发送」同一个 toggle 机制）——不需要记录的会议不必被迫标成「已发送」才能离开活跃列表；
// 忽略过的行脚注说明它会先被删掉、按「恢复」即撤销。OPEN 行不给这颗按钮（会还没开完，无从判断）。
import { useEffect, useRef, useState } from "react";
import { ApiError, postAction } from "../../api";
import { useI18n, type Language } from "../../i18n";
import { markRecap, markRecapPending } from "../../store";
import type { RecapRow, RecapSettings } from "../../types";
import { copyText } from "../detail/copyText";
import { noteConflicts, type NoteConflictId } from "./noteCheck";
import {
  isGenerating, pickLanguage, problemLabel, recapBody, recapClipboardText, recapHeader, recapProblems,
  recapRepairs, repairLabel, slackDraftLabel, type GenerationPhase,
} from "./recapText";

const NOTE_MAX = 500;
const CHANNEL_RE = /^[CDG][A-Z0-9]{6,20}$/;

type Bilingual = (zh: string, en: string) => string;

/** §63.5 预检文案：一条诉求一句「为什么做不到」，说的是格式的硬约束，不是模型的脾气。
 *  Record 而非 switch——漏掉一类新 id 是编译错误，不是一条空行。 */
const CONFLICT_LINES: Record<NoteConflictId, (text: Bilingual) => string> = {
  drop_line: (text) =>
    text("删不掉某一行：纪要恒是这五行，每行都带标签；没内容的那行只会写「无」。",
         "A line cannot be dropped: the recap is always these five labelled lines; an empty one comes back as none."),
  add_line: (text) =>
    text("加不了新的一行：只有这五行，多出来的内容只能并进其中一行。",
         "A line cannot be added: there are only these five; anything extra has to fold into one of them."),
  more_detail: (text) =>
    text("写不了更详细：每行有硬性长度上限，超了会被校验判成「需复核」。",
         "More detail does not fit: every line has a hard length cap, and going over it gets the recap flagged needs review."),
  relabel: (text) =>
    text("改不了标签：五个标签的文字与顺序是固定的。",
         "The labels cannot change: their wording and their order are fixed."),
  language_count: (text) =>
    text("改不了语言：中英两版一次产出、都会存下来，上面的切换按钮选看哪版。",
         "The languages cannot change: Chinese and English are always both produced and stored; the tabs above pick which one you read."),
  formatting: (text) =>
    text("加不了格式：加粗、项目符号、emoji、链接、时间戳、引号都会被校验拦下。",
         "Formatting cannot be added: bold, bullets, emoji, links, timestamps and quotation marks are all rejected by the validator."),
};

export interface RecapDetailProps {
  row: RecapRow;
  settings: RecapSettings | null;
  phase?: GenerationPhase;
}

type Panel = null | "note" | "slack";
type Text = (zh: string, en: string) => string;

/** §63.8 生成态的一句话（idle 不说话；done 由正文与 landedNote 的闪句体现） */
export function generationNote(phase: GenerationPhase, isOpen: boolean, text: Text): string | null {
  switch (phase) {
    case "queued":
      return text("已排队，等待后台接手…", "Queued, waiting for the daemon to pick it up…");
    case "unclaimed":
      return text("后台 90 秒没有接手：actd 可能没在跑（看「依赖检查」区的管线活性）。可以再试一次。", "Nothing picked this up in 90 s: actd may not be running (see Pipeline liveness under Dependency check). You can try again.");
    case "running":
      return isOpen
        ? text("正在生成阶段稿，落地后这里自动更新（通常 1–3 分钟）。", "Generating the partial recap. It lands here by itself (usually 1–3 min).")
        : text("正在重新生成，新版本落地后这里自动更新（通常 1–3 分钟）。", "Regenerating. The new version lands here by itself (usually 1–3 min).");
    case "lost":
      return text("上次生成没有落地：超过 10 分钟没写出新版本——后台进程崩了或模型调用失败（看 state/recap.log）。可以再试一次。", "The last generation never landed: no new version for over 10 minutes. The process crashed or the model call failed (see state/recap.log). You can try again.");
    case "noop":
      return text("上次生成没起来：后台进程启动失败（看 state/actd.log）。可以再试一次。", "The last generation did not start: the background process failed to launch (see state/actd.log). You can try again.");
    default:
      return null;
  }
}

/** 新版本落地那一下的闪句：有正文 = 已更新到第 N 版；没正文按 quality 说清为什么（失败不许穿成功的衣） */
export function landedNote(row: RecapRow, text: Text): string {
  const version = row.version ?? 0;
  if (row.en && row.en.length) return text(`已更新到第 ${version} 版`, `Updated to version ${version}`);
  const why = row.quality === "no_audio" ? text("无音频", "no audio")
    : row.quality === "thin_transcript" ? text("转写不全", "thin transcript")
    : text("生成失败", "generation failed");
  return text(`第 ${version} 版没有正文（${why}）`, `Version ${version} landed with no text (${why})`);
}

export function RecapDetail({ row, settings, phase = "idle" }: RecapDetailProps) {
  const { text, language: ui } = useI18n();
  const [language, setLanguage] = useState<Language>(pickLanguage(settings?.default_language, ui));
  const [panel, setPanel] = useState<Panel>(null);
  const [note, setNote] = useState("");
  const [channel, setChannel] = useState("");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  // 上一次渲染看到的 {key, version}：同一行版本号涨了 = 新版本落地，闪一句
  const seen = useRef<{ key: string; version: number }>({ key: row.key, version: row.version ?? 0 });

  // 切行 / 语言设置变化 → 语言与面板复位（草稿备注不跨行）
  useEffect(() => {
    setLanguage(pickLanguage(settings?.default_language, ui));
    setPanel(null);
    setNote("");
    setFlash(null);
  }, [row.key, settings?.default_language, ui]);

  useEffect(() => {
    const version = row.version ?? 0;
    if (seen.current.key === row.key && version > seen.current.version) setFlash(landedNote(row, text));
    seen.current = { key: row.key, version };
  }, [row, text]);

  useEffect(() => {
    if (!flash) return;
    const timer = setTimeout(() => setFlash(null), 4000);
    return () => clearTimeout(timer);
  }, [flash]);

  const body = recapBody(row, language);
  const problems = recapProblems(row);
  const repairs = recapRepairs(row);
  const hasText = Boolean(row.en && row.en.length);
  const isOpen = row.status === "open";
  const generating = isGenerating(phase);
  const progress = generationNote(phase, isOpen, text);

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
    const ok = await copyText(recapClipboardText(row, language));
    if (!ok) throw new Error(text("复制失败", "Copy failed"));
    await markRecap(row.key, "copied", true);
  });
  const toggleSent = () => run(
    row.sent_at ? text("已取消「已发送」，回到活跃列表", "Sent mark cleared; back in the active list")
                : text("已标记为已发送，归档到「已归档」", "Marked as sent and filed under Archived"),
    () => markRecap(row.key, "sent", !row.sent_at),
  );
  // §63.5 追记：忽略 = 放进「已忽略」并按自己的短保留期先删（恢复撤销它）；不是 registry 回收站（recap 不是卡）
  const toggleDismissed = () => run(
    row.dismissed_at ? text("已恢复到活跃列表", "Restored to the active list")
                     : text("已忽略，放进「已忽略」", "Dismissed and filed under Dismissed"),
    () => markRecap(row.key, "dismissed", !row.dismissed_at),
  );
  // §63.5 预检：备注命中五行契约做不到的诉求 → 逐条摊开，按钮改口，toast 不再假装全做到了
  const conflicts = noteConflicts(note);
  const regenerate = () => run(
    conflicts.length
      ? text("已排队重新生成——上面标出的部分格式做不到，不会变",
             "Regeneration queued — the flagged parts cannot be honored and will not change")
      : text("已排队重新生成，落地后这里自动更新", "Regeneration queued; this panel updates when it lands"),
    async () => {
      const payload: Record<string, unknown> = { action: "recap_generate", meeting_key: row.key };
      if (note.trim()) payload.note = note.trim().slice(0, NOTE_MAX);
      await postAction(payload);
      markRecapPending(row);
    });
  const generateNow = () => run(text("已排队生成阶段稿，落地后这里自动更新", "Partial recap queued; this panel updates when it lands"), async () => {
    await postAction({ action: "recap_generate", meeting_key: row.key, partial: true });
    markRecapPending(row);
  });
  const slackDraft = () => run(text("已排队投到 Slack 草稿", "Slack draft queued"), () =>
    postAction({ action: "recap_slack_draft", meeting_key: row.key, channel_id: channel.trim() }));

  return (
    <article className="recap-detail" aria-live="polite">
      <header className="recap-detail-head">
        <h3 className="recap-detail-title">{recapHeader(row, language)}</h3>
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

      {progress && (
        <p className={`recap-progress${generating ? " is-busy" : " is-warning"}`} role="status" data-phase={phase}>
          {progress}
        </p>
      )}

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
        {!isOpen && (
          <button type="button" className="btn" disabled={busy} onClick={() => void toggleDismissed()}>
            {row.dismissed_at ? text("恢复", "Restore") : text("忽略", "Dismiss")}
          </button>
        )}
        {isOpen ? (
          <button type="button" className="btn" disabled={busy || generating} onClick={() => void generateNow()}>
            {generating ? text("生成中…", "Generating…") : text("现在生成", "Generate now")}
          </button>
        ) : (
          <button type="button" className="btn" disabled={busy || generating} onClick={() => setPanel(panel === "note" ? null : "note")}>
            {generating ? text("生成中…", "Generating…") : text("重新生成…", "Regenerate…")}
          </button>
        )}
        {settings?.slack_draft_enabled && hasText && !isOpen && (
          <button type="button" className="btn" disabled={busy} onClick={() => setPanel(panel === "slack" ? null : "slack")}>
            {text("投到 Slack 草稿…", "Place in Slack drafts…")}
          </button>
        )}
      </div>

      {panel === "note" && !generating && (
        <div className="recap-panel">
          <label className="recap-panel-label" htmlFor="recap-note">
            {text("纠正备注（可选，≤500 字）：告诉模型哪里说错了", "Correction note (optional, ≤500 chars): what to fix")}
          </label>
          <textarea
            id="recap-note"
            className="recap-textarea"
            maxLength={NOTE_MAX}
            aria-describedby={conflicts.length ? "recap-note-conflicts" : undefined}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          {/* 不是 live region：内容随每个按键重算，role="status" 会让读屏在打字中途反复念整张表；
              挂在 textarea 的 aria-describedby 上 = 需要时可达，不追着人念（§63.5）。 */}
          {conflicts.length > 0 && (
            <div className="recap-note-conflicts" id="recap-note-conflicts">
              <p className="recap-note-conflicts-head">
                {text("这几件事五行格式做不到，重新生成也不会变：",
                      "The five-line format cannot honor these; regenerating will not change them:")}
              </p>
              <ul className="recap-note-conflicts-list">
                {conflicts.map((id) => <li key={id}>{CONFLICT_LINES[id](text)}</li>)}
              </ul>
            </div>
          )}
          <div className="recap-panel-actions">
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void regenerate()}>
              {conflicts.length ? text("仍要重新生成", "Regenerate anyway") : text("重新生成", "Regenerate")}
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
        {row.dismissed_at && (
          <span className="recap-meta-item">
            {text("已忽略：会比其他纪要更早被自动删除；按「恢复」撤销。",
                  "Dismissed: it is deleted earlier than the others. Press Restore to undo.")}
          </span>
        )}
        {row.quality === "needs_review" && (
          <span className="recap-meta-item">{text("校验未通过，粘贴前请通读一遍。", "Validator flagged this text; read it before pasting.")}</span>
        )}
        {problems.length > 0 && (
          <ul className="recap-reasons">
            {problems.map((problem, i) => (
              <li key={`${problem.code}-${problem.lang ?? ""}-${problem.line ?? ""}-${i}`}>{problemLabel(problem, text)}</li>
            ))}
          </ul>
        )}
        {repairs.length > 0 && (
          <ul className="recap-reasons is-repair">
            {repairs.map((repair, i) => (
              <li key={`${repair.lang}-${repair.line}-${i}`}>{repairLabel(repair, text)}</li>
            ))}
          </ul>
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
