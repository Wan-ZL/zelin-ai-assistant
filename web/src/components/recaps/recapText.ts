// 会议纪要页的纯逻辑（CONTRACT §63 / §63.5 / §63.8）：行标签、按日分组、badge 词表、语言选择、
// 正文与抬头一行（`recapHeader` / `recapClipboardText`，issue #299）、「重新生成」的生成态判定。
// 无 React、无 fetch——vitest node 环境可直测。wire 字段来自 dashboard.json 顶层 recaps[]。
import type { Language } from "../../i18n";
import type { RecapPending } from "../../store";
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

/**
 * §63.8 行的生成态（issue #297）：
 *   queued    = 本地刚按下、actd 还没回执（乐观）；
 *   unclaimed = 按下 90 s 仍无回执也无新版本——actd 大概没在跑（按钮解锁、一句人话；10 分钟后退场）；
 *   running   = actd 回执 running，新版本还没落地；
 *   lost / noop = actd 回执说丢了 / 没起；idle = 没在生成（含 done）。
 * server 回执只要比按下时看到的新就以它为准；本地 pending 只填 actd 还没接手的那几秒。
 */
export type GenerationPhase = "idle" | "queued" | "unclaimed" | "running" | "lost" | "noop";

/** 没人接手的判线：actd pass 是 10 s，90 s 与 VoiceGenerate / §48.7「立即测试一轮」同款 */
export const PICKUP_TIMEOUT_MS = 90_000;
/** 乐观「排队中」的退场线（与 daemon 侧 recap_requests.LOST_AFTER_S 同款 10 分钟） */
export const PENDING_TIMEOUT_MS = 10 * 60 * 1000;

export function generationPhase(row: RecapRow, pending: RecapPending | undefined, now: number): GenerationPhase {
  const receipt = row.generate_request ?? null;
  const receiptIsNew = receipt !== null && (!pending || receipt.requested_at !== pending.requested_at);
  if (receiptIsNew) {
    if (receipt.state === "running") return "running";
    if (receipt.state === "lost") return "lost";
    if (receipt.state === "noop") return "noop";
    return "idle";                                  // done（或未知词）：本地 pending 一并结束
  }
  if (!pending) return "idle";
  if ((row.version ?? 0) > pending.version) return "idle";   // 新版本已落地
  const age = now - pending.at;
  if (age > PENDING_TIMEOUT_MS) return "idle";                // 退场：别永远挂着
  return age > PICKUP_TIMEOUT_MS ? "unclaimed" : "queued";
}

/** 生成态是否还在等结果（行上「生成中」+ 面板状态行 + 按钮禁用 + 5 s 补拉）；unclaimed 不算——按钮要能再按 */
export function isGenerating(phase: GenerationPhase): boolean {
  return phase === "queued" || phase === "running";
}

/** 行 badge（issue #129 §3 词表 + §63.8 生成中 / 后台未接手 / 生成未落地 / 生成未启动）：
 *  进行中 / 新 / 已复制 / 已发送 / 已更新 / 转写不全 / 需复核 / 无音频 / 生成失败 */
export function badgesFor(row: RecapRow, phase: GenerationPhase = "idle"): Badge[] {
  const out: Badge[] = [];
  if (isGenerating(phase)) out.push({ id: "generating", zh: "生成中", en: "Generating", tone: "info" });
  else if (phase === "unclaimed") out.push({ id: "unclaimed", zh: "后台未接手", en: "Not picked up", tone: "warning" });
  else if (phase === "lost") out.push({ id: "lost", zh: "生成未落地", en: "Generation lost", tone: "warning" });
  else if (phase === "noop") out.push({ id: "noop", zh: "生成未启动", en: "Did not start", tone: "warning" });
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

/** 正文 = 该语言 5 行、换行连接、不加任何别的东西（issue #129 §4）。抬头一行由
 *  `recapHeader` 单独给，剪贴板的那一份走 `recapClipboardText`（§63.5 追记）。 */
export function recapBody(row: RecapRow, language: Language): string {
  const lines = language === "zh" ? row.zh : row.en;
  return (lines ?? []).join("\n");
}

// 星期名写死两张表，**不**走 `Intl` / `toLocaleDateString`：Node 与浏览器的 ICU 数据
// 不一致（精简版 Node 只有 en-US），判例会随运行环境漂。周日起，与 `Date.getDay()` 同序。
const WEEKDAYS_EN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WEEKDAYS_ZH = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

/** 日期 + 星期（本机时区，与 `dayKey` 同一天）：`2026-03-04 (Wed)` / `2026-03-04（周三）`。
 *  解析不出 = 空串（不写 `? (?)`——调用方据此退回不带日期的标签）。 */
export function dayLabel(iso: string, language: Language): string {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "";
  const day = dayKey(iso);
  return language === "zh"
    ? `${day}（${WEEKDAYS_ZH[t.getDay()]}）`
    : `${day} (${WEEKDAYS_EN[t.getDay()]})`;
}

/** 抬头一行（§63.5 追记，issue #299）：`2026-03-04 (Wed) 14:07–14:48 · Zoom · 41 min`。
 *  时间是**采集到的原值**（不向下取整到整点）——粘出去的是记录，不是日程。
 *  start 解析不出 → 退回 `rowLabel`（它自己已退化成 `--:--`），绝不多出一个 `?`。 */
export function recapHeader(row: RecapRow, language: Language): string {
  const day = dayLabel(row.start, language);
  const label = rowLabel(row);
  if (!day) return label;
  return language === "zh" ? `${day}${label}` : `${day} ${label}`;
}

/** 剪贴板那一份（§63.5 追记）= 抬头一行 + 该语言 5 行；`copy_header: false` 回到只有 5 行。
 *  没有正文 = 空串——光一行抬头不是纪要（复制键本来也只在有正文时出现）。 */
export function recapClipboardText(row: RecapRow, language: Language, withHeader: boolean): string {
  const body = recapBody(row, language);
  if (!body) return "";
  const header = withHeader ? recapHeader(row, language) : "";
  return header ? `${header}\n${body}` : body;
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
