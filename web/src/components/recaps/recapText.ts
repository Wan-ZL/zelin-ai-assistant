// 会议纪要页的纯逻辑（CONTRACT §63 / §63.3 / §63.5 / §63.8 / §63.9）：行标签、按日分组、badge 词表、
// 语言选择、复制正文与它的表头、「重新生成」的生成态判定、§63.3 追记的校验原因与自动修剪文案、
// §63.5 追记（issue #301）的三栏判定（活跃 / 已归档 / 已忽略）、
// §63.9（issue #300）的行级引用标签 D/S/L/C/O、版本标题与两版逐行差异。
// 无 React、无 fetch——vitest node 环境可直测。wire 字段来自 dashboard.json 顶层 recaps[]。
import type { Language } from "../../i18n";
import type { RecapPending } from "../../store";
import type { RecapProblem, RecapRepair, RecapRow } from "../../types";
import { WEEKDAYS } from "../shell/recordingSchedule";

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

/** 行 badge（issue #129 §3 词表 + §63.8 生成中 / 后台未接手 / 生成未落地 / 生成未启动
 *  + §63.5 追记 issue #301 已忽略）：
 *  进行中 / 新 / 已复制 / 已发送 / 已忽略 / 已更新 / 转写不全 / 需复核 / 无音频 / 生成失败 */
export function badgesFor(row: RecapRow, phase: GenerationPhase = "idle"): Badge[] {
  const out: Badge[] = [];
  if (isGenerating(phase)) out.push({ id: "generating", zh: "生成中", en: "Generating", tone: "info" });
  else if (phase === "unclaimed") out.push({ id: "unclaimed", zh: "后台未接手", en: "Not picked up", tone: "warning" });
  else if (phase === "lost") out.push({ id: "lost", zh: "生成未落地", en: "Generation lost", tone: "warning" });
  else if (phase === "noop") out.push({ id: "noop", zh: "生成未启动", en: "Did not start", tone: "warning" });
  if (row.status === "open") out.push({ id: "open", zh: "进行中", en: "In progress", tone: "info" });
  if (row.partial && row.en) out.push({ id: "partial", zh: "阶段稿", en: "Partial", tone: "quiet" });
  if (row.dismissed_at) out.push({ id: "dismissed", zh: "已忽略", en: "Dismissed", tone: "quiet" });
  else if (row.sent_at) out.push({ id: "sent", zh: "已发送", en: "Sent", tone: "success" });
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

/**
 * §63.5 追记（issue #301）一行落在哪一栏：
 *   archived  = `sent_at`（标记已发送派生，无新存储态；取消标记即回到活跃）；
 *   dismissed = `dismissed_at`（「忽略」标记；daemon 按自己的短保留期删，「恢复」撤销）；
 *   active    = 其余（含 OPEN 行）。
 * 已忽略优先于已归档：它是「这场会不需要纪要」的判决，不是「已经发出去了」。
 */
export type RecapLane = "active" | "archived" | "dismissed";

export function recapLane(row: RecapRow): RecapLane {
  if (row.dismissed_at) return "dismissed";
  if (row.sent_at) return "archived";
  return "active";
}

/** 三栏的顺序与双语标题（页面 segmented 过滤器读它；文案仍走唯一的双语机制 text(zh,en)） */
export const RECAP_LANES: { id: RecapLane; zh: string; en: string }[] = [
  { id: "active", zh: "活跃", en: "Active" },
  { id: "archived", zh: "已归档", en: "Archived" },
  { id: "dismissed", zh: "已忽略", en: "Dismissed" },
];

/** 每栏的行数（空栏也有键——过滤器上的 0 要显示出来） */
export function laneCounts(rows: RecapRow[]): Record<RecapLane, number> {
  const out: Record<RecapLane, number> = { active: 0, archived: 0, dismissed: 0 };
  for (const row of rows) out[recapLane(row)] += 1;
  return out;
}

/** 详情默认语言：recap.default_language auto 跟随 UI 语言 */
export function pickLanguage(defaultLanguage: string | undefined, ui: Language): Language {
  return defaultLanguage === "zh" || defaultLanguage === "en" ? defaultLanguage : ui;
}

/** 复制正文 = 该语言 5 行、换行连接、不加任何别的东西（issue #129 §4）；`<pre>` 显示的也是它 */
export function recapBody(row: RecapRow, language: Language): string {
  const lines = language === "zh" ? row.zh : row.en;
  return (lines ?? []).join("\n");
}

/** 表头的星期：复用 §61.7 录制日程的 WEEKDAYS 表（id = getDay()+1），不另起第二套双语星期词表 */
function weekdayParens(iso: string, language: Language): string {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "";
  const day = WEEKDAYS.find((d) => d.id === t.getDay() + 1);
  if (!day) return "";
  return language === "zh" ? `（周${day.zh}）` : `(${day.en})`;
}

/** 表头（issue #299）：`2026-08-31 (Mon) 12:56–13:16 · Zoom · 20 min`；中文是 `2026-08-31（周一） …`
 *  ——全角括号自带空白，前面不补半角空格。start 坏了 = `? --:--–--:-- · …`（无星期括号），恒不抛 */
export function recapHeader(row: RecapRow, language: Language): string {
  const wd = weekdayParens(row.start, language);
  const gap = language === "zh" ? "" : " ";
  return `${dayKey(row.start)}${wd ? gap + wd : ""} ${rowLabel(row)}`;
}

/** 剪贴板文本 = 一行表头 + 5 行正文（§63.5 追记）；存储与 Slack 草稿正文仍恰是 5 行 */
export function recapClipboardText(row: RecapRow, language: Language): string {
  return `${recapHeader(row, language)}\n${recapBody(row, language)}`;
}

type Bilingual = (zh: string, en: string) => string;

/**
 * §63.9（issue #300）**行级引用标签** D / S / L / C / O：五行的标签文字与顺序是固定的
 * （`act/lib/recap_text.LABELS_EN` / `LABELS_ZH`，§63.3 的硬闸），所以**位置本身就是身份**
 * ——不需要在 wire 上给每行发一个 id 就能把一行citable。粘出去的五行正文一字不变
 * （引用串是另一次复制，chip 各自一颗）。
 * 逐项 id 的 `D1` / `A2` / `O3` 形（一行里的第几条）要等 #303 的多条目格式，本版不伪造。
 */
export const LINE_TAGS: string[] = ["D", "S", "L", "C", "O"];

/** 五个标签的双语名（chip 的可达名用；文案仍走唯一的 text(zh, en) 机制） */
export const LINE_TAG_LABELS: { tag: string; zh: string; en: string }[] = [
  { tag: "D", zh: "定了", en: "Decided" },
  { tag: "S", zh: "分工", en: "Split" },
  { tag: "L", zh: "截止", en: "Deadline" },
  { tag: "C", zh: "较上次变化", en: "Changed since last plan" },
  { tag: "O", zh: "待定", en: "Open" },
];

/**
 * 一行的引用串 `2026-08-31 Zoom #D`：日期（本机时区，与左列日分组 / 复制表头同一口径）
 * + 会议应用 + 行标签。五行之外 = 空串（手改坏的文件不给 chip）。
 * **纯展示层**：不是 wire 字段，也不进 `recapBody()` / `recapClipboardText()`。
 */
export function lineCitation(row: RecapRow, index: number): string {
  const tag = LINE_TAGS[index];
  if (!tag) return "";
  return `${dayKey(row.start)} ${appLabel(row.app)} #${tag}`;
}

/**
 * §63.9 两版逐行比：`true` = 这一行变了。**纯字符串比较，零模型、零请求、同输入恒同结果**
 * （§63.5 预检的同一条纪律）。长度不同按位置比（缺的一侧当空串）——正常两版都恰是五行。
 */
export function changedLines(current: string[], previous: string[]): boolean[] {
  const out: boolean[] = [];
  for (let i = 0; i < Math.max(current.length, previous.length); i += 1) {
    out.push((current[i] ?? "") !== (previous[i] ?? ""));
  }
  return out;
}

/** `2026-08-31 12:56`（本机时区）；坏 / 缺时间戳 = 空串，永不显示 Invalid Date */
function stampLabel(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "";
  return `${dayKey(iso)} ${hhmm(iso)}`;
}

/** §63.9 版本选择器一项的标题：`第 2 版（阶段稿） · 2026-08-31 12:56` */
export function versionLabel(entry: { version: number; generated_at?: string | null; partial?: boolean },
                            text: Bilingual): string {
  const head = text(`第 ${entry.version} 版`, `Version ${entry.version}`);
  const partial = entry.partial ? text("（阶段稿）", " (partial)") : "";
  const stamp = stampLabel(entry.generated_at);
  return stamp ? `${head}${partial} · ${stamp}` : `${head}${partial}`;
}

/** §63.3 追记：wire 上的发现行（老 daemon 无此键、手改过的文件可能是任意东西）——只留像样的对象 */
export function recapProblems(row: RecapRow): RecapProblem[] {
  const rows = Array.isArray(row.problems) ? row.problems : [];
  return rows.filter((p): p is RecapProblem => Boolean(p) && typeof p === "object" && typeof p.code === "string");
}

/** 同上，修剪台账：一行修剪必须有语言与行号才说得出话 */
export function recapRepairs(row: RecapRow): RecapRepair[] {
  const rows = Array.isArray(row.repairs) ? row.repairs : [];
  return rows.filter((r): r is RecapRepair => Boolean(r) && typeof r === "object" && typeof r.line === "number");
}

/** 「英文第 3 行」/「English line 3」；整语言级的禁项没有行号（daemon 给 null）= 只说语言 */
function where(lang: unknown, line: unknown, text: Bilingual): string {
  const name = lang === "zh" ? text("中文", "Chinese") : lang === "en" ? text("英文", "English") : text("正文", "The text");
  return typeof line === "number" ? text(`${name}第 ${line} 行`, `${name} line ${line}`) : name;
}

/** §63.3 追记 一条校验原因的人话（code 词表 add-only；词表外的新 code 原样显示 daemon 那句英文） */
export function problemLabel(problem: RecapProblem, text: Bilingual): string {
  const at = where(problem.lang, problem.line, text);
  switch (problem.code) {
    case "line_count":
      return text("两版都必须恰好五行", "Each language must have exactly five lines");
    case "label_mismatch":
      return text(`${at}：标签不对（五个标签的文字与顺序是固定的）`, `${at}: wrong label (their wording and order are fixed)`);
    case "line_too_long":
      return text(`${at}：超出上限 ${problem.over ?? "?"} 个字符（上限 ${problem.limit ?? "?"}）`,
                  `${at}: ${problem.over ?? "?"} characters over the ${problem.limit ?? "?"}-character cap`);
    case "reported_speech":
      return text(`${at}：转述（said / mentioned / 说 / 提到）`, `${at}: reported speech (said / mentioned / 说 / 提到)`);
    case "timestamp":
      return text(`${at}：有时间戳`, `${at}: contains a timestamp`);
    case "link":
      return text(`${at}：有链接`, `${at}: contains a link`);
    case "quotes":
      return text(`${at}：有引号`, `${at}: contains quotation marks`);
    case "markup":
      return text(`${at}：有 markdown 格式`, `${at}: contains markdown formatting`);
    case "emoji":
      return text(`${at}：有 emoji`, `${at}: contains emoji`);
    case "mention":
      return text(`${at}：有 @ 提及`, `${at}: contains an @mention`);
    default:
      return problem.text || problem.code;
  }
}

/**
 * §63.3 追记 一行自动修剪的人话——剪过就一定说出来，粘出去的正文不许有暗改。
 * 说的是**真正剪掉的字符数**（`removed`），超出量只做括注：英文按词边界回退，
 * 只报 over 会把一刀说小（老 daemon 无此键 → 退回只说超出量）。
 */
export function repairLabel(repair: RecapRepair, text: Bilingual): string {
  const at = where(repair.lang, repair.line, text);
  if (typeof repair.removed !== "number") {
    return text(`已自动修剪${at}（原来超出 ${repair.over} 个字符）`,
                `Trimmed ${at} automatically (it was ${repair.over} characters over)`);
  }
  return text(`已自动修剪${at}：剪掉行尾 ${repair.removed} 个字符（原来超出 ${repair.over} 个）`,
              `Trimmed ${at} automatically: ${repair.removed} characters off the end (it was ${repair.over} over the cap)`);
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
