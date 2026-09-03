// 会议纪要页的纯逻辑（CONTRACT §63）：行标签、按日分组、badge 词表、语言选择、复制正文。
// 无 React、无 fetch——vitest node 环境可直测。wire 字段来自 dashboard.json 顶层 recaps[]。
import type { Language } from "../../i18n";
import type { RecapRow } from "../../types";

/** 会议应用 slug（server 定，act/lib/recap_sessions.DEFAULT_MEETING_RULES）→ 显示名 */
const APP_LABELS: Record<string, string> = {
  zoom: "Zoom",
  teams: "Teams",
  webex: "Webex",
  facetime: "FaceTime",
  meet: "Google Meet",
  "slack-huddle": "Slack Huddle",
  audio: "Audio",
};

export function appLabel(app: string): string {
  return APP_LABELS[app] ?? app;
}

function hhmm(iso: string): string {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "--:--";
  return `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
}

/** 行标签：`12:56–13:16 · Zoom · 20 min`（本机时区） */
export function rowLabel(row: RecapRow): string {
  return `${hhmm(row.start)}–${hhmm(row.end)} · ${appLabel(row.app)} · ${row.duration_min} min`;
}

/** 本地日期键 YYYY-MM-DD（分组用） */
export function dayKey(iso: string): string {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "?";
  const y = t.getFullYear();
  const m = String(t.getMonth() + 1).padStart(2, "0");
  const d = String(t.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export interface DayGroup {
  day: string;
  rows: RecapRow[];
}

/** 按日分组，日与日内都按 start 倒序（server 已倒序，这里只稳定分组） */
export function groupByDay(rows: RecapRow[]): DayGroup[] {
  const groups: DayGroup[] = [];
  for (const row of rows) {
    const day = dayKey(row.start);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.rows.push(row);
    else groups.push({ day, rows: [row] });
  }
  return groups;
}

export type BadgeTone = "accent" | "info" | "success" | "warning" | "danger" | "quiet";

export interface Badge {
  id: string;
  zh: string;
  en: string;
  tone: BadgeTone;
}

/** 行 badge（issue #129 §3 词表）：进行中 / 新 / 已复制 / 已发送 / 已更新 / 转写不全 / 需复核 / 无音频 / 生成失败 */
export function badgesFor(row: RecapRow): Badge[] {
  const out: Badge[] = [];
  if (row.status === "open") out.push({ id: "open", zh: "进行中", en: "In progress", tone: "info" });
  if (row.partial && row.en) out.push({ id: "partial", zh: "阶段稿", en: "Partial", tone: "quiet" });
  if (row.sent_at) out.push({ id: "sent", zh: "已发送", en: "Sent", tone: "success" });
  else if (row.copied_at) out.push({ id: "copied", zh: "已复制", en: "Copied", tone: "quiet" });
  else if (row.en && row.status === "closed") out.push({ id: "new", zh: "新", en: "New", tone: "accent" });
  if ((row.version ?? 0) > 1 && row.en) out.push({ id: "updated", zh: "已更新", en: "Updated", tone: "info" });
  switch (row.quality) {
    case "needs_review":
      out.push({ id: "review", zh: "需复核", en: "Needs review", tone: "warning" });
      break;
    case "thin_transcript":
      out.push({ id: "thin", zh: "转写不全", en: "Thin transcript", tone: "warning" });
      break;
    case "no_audio":
      out.push({ id: "silent", zh: "无音频", en: "No audio", tone: "danger" });
      break;
    case "generation_failed":
      out.push({ id: "failed", zh: "生成失败", en: "Generation failed", tone: "danger" });
      break;
    default:
      break;
  }
  return out;
}

/** 详情默认语言：recap.default_language auto 跟随 UI 语言 */
export function pickLanguage(defaultLanguage: string | undefined, ui: Language): Language {
  return defaultLanguage === "zh" || defaultLanguage === "en" ? defaultLanguage : ui;
}

/** 复制正文 = 该语言 5 行、换行连接、不加任何别的东西（issue #129 §4） */
export function recapBody(row: RecapRow, language: Language): string {
  const lines = language === "zh" ? row.zh : row.en;
  return (lines ?? []).join("\n");
}

/** §63.4 草稿回执文案（wire status 词表 add-only；未知值按字符串兜底） */
export function slackDraftLabel(status: string | undefined, text: (zh: string, en: string) => string): string {
  switch (status) {
    case "posted":
      return text("已投草稿", "Draft placed");
    case "draft_already_exists":
      return text("Slack 已有草稿", "Slack already has a draft");
    case "no_target":
      return text("未投草稿：无目标会话", "No draft: no target conversation");
    case "disabled":
      return text("未投草稿：开关已关", "No draft: toggle is off");
    case "failed":
      return text("投递失败", "Draft failed");
    default:
      return status ?? "";
  }
}
