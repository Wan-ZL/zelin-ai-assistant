// 会议纪要页的纯逻辑（CONTRACT §63 / §63.3 / §63.5 / §63.8 / §63.9 / §63.10 / §63.11 / §63.12）：行标签、按日分组、badge 词表、
// 语言选择、复制正文与它的表头、「重新生成」的生成态判定、§63.3 追记的校验原因与自动修剪文案、
// §63.5 追记（issue #301）的三栏判定（活跃 / 已归档 / 已忽略）、
// §63.9（issue #300）的行级引用标签 D/S/L/C/O、版本标题、两版逐行差异与回退的回执态判定、
// §63.11（issue #302）意图问答的词表与答案拼装 + 「转写原版 / 我记录的版本」两版切换的正文、
// §63.12（issue #300 的后半）可发送长版的逐条标签：行首标签的读出、按标签的两版差异、逐条引用串。
// 无 React、无 fetch——vitest node 环境可直测。wire 字段来自 dashboard.json 顶层 recaps[]。
import type { Language } from "../../i18n";
import type { RecapPending } from "../../store";
import type { RecapProblem, RecapQuestion, RecapRepair, RecapRow, RecapSection } from "../../types";
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

/**
 * §63.16 这一行显示的结束时刻：owner 手改的 `end_override`（server 独写 marks.json 的投影，
 * 解析得出才算）优先，否则录制到的 `end`。手改只改显示——`end` 仍是录制到的那一刻，
 * 时长按显示的结束时刻重算（表头、行标签、剪贴板表头三处同一口径，所见即所复制）。
 */
export function shownEnd(row: RecapRow): { end: string; overridden: boolean } {
  const raw = row.end_override;
  if (typeof raw === "string" && raw && !Number.isNaN(new Date(raw).getTime())) return { end: raw, overridden: true };
  return { end: row.end, overridden: false };
}

/** 显示的时长（分钟）：手改过按 start→手改 end 算（不小于 0），否则 server 给的 duration_min */
export function shownDuration(row: RecapRow): number {
  const { end, overridden } = shownEnd(row);
  if (!overridden) return row.duration_min;
  const start = new Date(row.start).getTime();
  const stop = new Date(end).getTime();
  if (Number.isNaN(start) || Number.isNaN(stop)) return row.duration_min;
  return Math.max(0, Math.round((stop - start) / 60000));
}

/** 行标签：`12:56–13:16 · Zoom · 20 min`（本机时区；§63.16 结束时刻与时长按 shownEnd） */
export function rowLabel(row: RecapRow): string {
  return `${hhmm(row.start)}–${hhmm(shownEnd(row).end)} · ${appLabel(row.app)} · ${shownDuration(row)} min`;
}

/** `<input type="time">` 用的本地 `HH:MM`（坏时间戳 = 空串） */
export function localHHMM(iso: string): string {
  const label = hhmm(iso);
  return label === "--:--" ? "" : label;
}

/** §63.16 编辑框输入为什么不能用（面板据它说不同的一句）：`input` = 不是 HH:MM / 越界；
 *  `start` = 这一行的 start 解析不出；`same` = 与开始是同一分钟（一场会不可能零分钟）；null = 能用 */
export type EndOverrideProblem = "input" | "start" | "same" | null;

const DAY_MS = 24 * 60 * 60 * 1000;

function endCandidate(row: RecapRow, hhmmValue: string): { problem: EndOverrideProblem; end?: Date } {
  const m = /^(\d{2}):(\d{2})$/.exec(hhmmValue.trim());
  if (!m) return { problem: "input" };
  const hours = Number(m[1]);
  const minutes = Number(m[2]);
  if (hours > 23 || minutes > 59) return { problem: "input" };   // `25:99` 不许被 Date 悄悄滚到第二天
  const start = new Date(row.start);
  if (Number.isNaN(start.getTime())) return { problem: "start" };
  let end = new Date(start.getFullYear(), start.getMonth(), start.getDate(), hours, minutes, 0, 0);
  // 输入的时刻比开始早 = 跨过了本地午夜（23:40 开、00:05 结束的那种会）：落到开始的**下一天**；
  // 与开始同一分钟才是真的说不通（滚一天变成 24 小时的会，不是 owner 的意思）
  if (end.getTime() < start.getTime()) end = new Date(end.getTime() + DAY_MS);
  if (end.getTime() === start.getTime()) return { problem: "same" };
  return { problem: null, end };
}

/** §63.16 编辑框里的值能不能用（null = 能）——与 :func:`endOverrideIso` 同一条判定 */
export function endOverrideProblem(row: RecapRow, hhmmValue: string): EndOverrideProblem {
  return endCandidate(row, hhmmValue).problem;
}

/**
 * §63.16 把编辑框里的本地 `HH:MM` 合到会议开始那一天上 → ISO-Z（秒级，与记录上的 start / end 同形）；
 * 比开始早的时刻按跨午夜算、落到下一天。不能用的输入 = null（原因由 :func:`endOverrideProblem` 说）。
 */
export function endOverrideIso(row: RecapRow, hhmmValue: string): string | null {
  const { problem, end } = endCandidate(row, hhmmValue);
  if (problem || !end) return null;
  return end.toISOString().replace(/\.\d{3}Z$/, "Z");
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
/** 乐观「排队中」的退场线：10 分钟（= daemon 侧「丢了」判线的**地板** recap_timing.lost_after_s()；
 *  §63.15 起那条线按转写词数往上伸，但本地 pending 只填 actd 还没接手的那几秒，server 回执一到就以它为准） */
export const PENDING_TIMEOUT_MS = 10 * 60 * 1000;

/** §63.15 回执里「丢了」的判线（分钟，四舍五入）；老 daemon 无此键 = 它当年写死的 10 分钟 */
export function lostAfterMinutes(row: RecapRow): number {
  const seconds = row.generate_request?.lost_after_s;
  return typeof seconds === "number" && seconds > 0 ? Math.max(1, Math.round(seconds / 60)) : 10;
}

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

/**
 * §63.9（issue #300）**回退的回执**。回退是 detached inbox 动作，daemon 侧**没有**台账
 * （`recap_revert` 不进 §63.8 的 `generate_request`：回退不是一次生成，行上不该出现「生成中」），
 * 所以面板自己记一条最小乐观回执——按下时记 `{version, base, at}`：
 *   queued    = 已排队、新版本还没落地（面板一句「排队中」+ 每 `REVERT_POLL_MS` 补拉一次看板）；
 *   unclaimed = 按下 90 s 仍没有新版本（actd 没在跑 / 子进程起不来 / 锁等超时都长这样）——
 *               与 §63.8 **同一条判线、同一句**「actd 可能没在跑…可以再试一次」；
 *   idle      = 版本号涨了（回退落地）/ 10 分钟退场。
 * 两个时限与 §63.8 共用常量，绝不另起第二套。
 */
export type RevertPhase = "idle" | "queued" | "unclaimed";

export interface RevertPending {
  /** 要回到的那一版（文案说「回退到第 N 版」用） */
  version: number;
  /** 按下时看到的当前版本号：涨了 = 这次回退（或任何一次落地）已经到了 */
  base: number;
  at: number;
}

export function revertPhase(row: RecapRow, pending: RevertPending | null, now: number): RevertPhase {
  if (!pending) return "idle";
  if ((row.version ?? 0) > pending.base) return "idle";       // 新版本已落地
  const age = now - pending.at;
  if (age > PENDING_TIMEOUT_MS) return "idle";                // 退场：别永远挂着
  return age > PICKUP_TIMEOUT_MS ? "unclaimed" : "queued";
}

/** 回退在途时面板自己的补拉间隔——与 §63.8 页面侧 `GENERATING_POLL_MS` 同一个 5 s 口径（判例钉两者相等） */
export const REVERT_POLL_MS = 5000;

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
  const hasText = hasRecapText(row);   // §63.10：可发送长版的 en 是空的，正文在 sections_en
  if (row.partial && hasText) out.push({ id: "partial", zh: "阶段稿", en: "Partial", tone: "quiet" });
  if (row.dismissed_at) out.push({ id: "dismissed", zh: "已忽略", en: "Dismissed", tone: "quiet" });
  else if (row.sent_at) out.push({ id: "sent", zh: "已发送", en: "Sent", tone: "success" });
  else if (row.copied_at) out.push({ id: "copied", zh: "已复制", en: "Copied", tone: "quiet" });
  else if (hasText && row.status === "closed") out.push({ id: "new", zh: "新", en: "New", tone: "accent" });
  if ((row.version ?? 0) > 1 && hasText) out.push({ id: "updated", zh: "已更新", en: "Updated", tone: "info" });
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

/**
 * §63.10（issue #303）出稿形状：`lines` = 快速五行（自用便签）｜ `sections` = 可发送长版
 * （分节 + 每节语气 + 跨节连续编号）。词表逐字镜像 `act/lib/recap_text.SHAPES`；
 * 老 daemon 没有这个键 = 五行形。
 */
export type RecapShape = "lines" | "sections";

export function recapShape(row: RecapRow): RecapShape {
  return row.shape === "sections" ? "sections" : "lines";
}

/**
 * §63.10 形状选择器的初值 = **这一份出过稿的形状 > 配置的出厂形状**
 * （`act/recap.record_shape` 那条优先级链去掉按钮那一级的镜像）。
 * 只用 `recapShape(row)` 会让还没出过稿 / 本 PR 之前生成的每一行都默认「快速五行」，
 * 而面板每次都把选中的形状随请求送出去——于是「重新生成」一按就替配置做了主，
 * 而且因为形状会落到记录上，`recap.default_shape: sections` 再也回不来。
 */
export function pickShape(row: RecapRow, defaultShape?: string): RecapShape {
  if (row.shape === "sections" || row.shape === "lines") return row.shape;
  return defaultShape === "sections" ? "sections" : "lines";
}

/** 形状选择器的两项（文案仍走唯一的双语机制 text(zh, en)） */
export const RECAP_SHAPES: { id: RecapShape; zh: string; en: string; hint_zh: string; hint_en: string }[] = [
  { id: "lines", zh: "快速五行", en: "Quick five lines",
    hint_zh: "五行固定标签，自己看、随手粘。", hint_en: "Five fixed labels — the quick personal note." },
  { id: "sections", zh: "可发送长版", en: "Sendable long form",
    hint_zh: "分节、逐条编号，每节标出「已定 / 提议 / 有人提过 / 待定」——要发给别人的那一份。",
    hint_en: "Sections and numbered items, each section tagged decided / proposed / floated / open — the one you send." },
];

/**
 * §63.11（issue #302）意图问答的纯逻辑。**组成来自 wire**（`recaps[].questions`，
 * daemon 从这一版正文自己推出来的），这里只做三件事：滤掉手改坏的行、把 owner 点过的
 * 选项拼成 wire 形的 `answers`、给两版切换取正文。问法与选项文案走页面同一套
 * `text(zh, en)`（下面两张词表，add-only；词表外原样显示 wire 值）。
 */
export const RECAP_ANSWERS_MAX = 12;

export function recapQuestions(row: RecapRow): RecapQuestion[] {
  const rows = Array.isArray(row.questions) ? row.questions : [];
  return rows.filter((q): q is RecapQuestion =>
    Boolean(q) && typeof q === "object" && typeof (q as RecapQuestion).id === "string"
    && Array.isArray((q as RecapQuestion).options) && (q as RecapQuestion).options.length > 0);
}

/** 一类问题怎么问（`kind` 词表逐字镜像 `act/lib/recap_intent.OPTIONS` 的键） */
export const RECAP_QUESTION_KINDS: Record<string, { zh: string; en: string }> = {
  split: { zh: "这条分工要留在纪要里吗？写在纸上就是一份责任。",
           en: "Keep this commitment on the record? An item on the record is an obligation." },
  deadline: { zh: "要把截止日期记下来吗？",
              en: "Record the deadline?" },
  others: { zh: "要记下对方的要求、归属与进度吗？",
            en: "Record the other party's requirements, ownership and status?" },
  detail: { zh: "要写到研究级细节与保留说法，还是停在决定与行动层面？",
            en: "Include research-level detail and hedges, or stop at decision and action level?" },
  audience: { zh: "这一份是发给别人，还是自己看？两者要的语气与对冲不一样。",
              en: "Is this recap for sending to someone, or for your own memory? They want different registers." },
  own: { zh: "里面提到的那个项目 / 工作流，你想认领吗？",
         en: "That project or workstream — do you want to own it?" },
  prior: { zh: "要和上一份纪要对比吗？",
           en: "Compare against the prior recap?" },
};

/** 一个选项的按钮文案（选项词表同源；未知值原样显示——add-only 词表纪律） */
export const RECAP_ANSWER_LABELS: Record<string, { zh: string; en: string }> = {
  keep: { zh: "留着", en: "Keep" },
  drop: { zh: "删掉", en: "Drop" },
  propose: { zh: "改成提议", en: "As a proposal" },
  send: { zh: "发给别人", en: "For sending" },
  self: { zh: "自己看", en: "For myself" },
  own: { zh: "我认领", en: "I own it" },
  decline: { zh: "不是我的", en: "Not mine" },
  compare: { zh: "对比", en: "Compare" },
};

export function questionLabel(question: RecapQuestion, text: Bilingual): string {
  const wording = RECAP_QUESTION_KINDS[question.kind];
  return wording ? text(wording.zh, wording.en) : question.kind;
}

export function answerLabel(option: string, text: Bilingual): string {
  const wording = RECAP_ANSWER_LABELS[option];
  return wording ? text(wording.zh, wording.en) : option;
}

/**
 * owner 点过的选项 → wire 形 `["split1=drop", …]`。**只带点过的那几条**：没答过的问题
 * 不发一个编出来的答案（默认值会变成一句 owner 从没说过的指令）。顺序按 wire 上的问题
 * 顺序，帽 `RECAP_ANSWERS_MAX`（= daemon 侧的 `MAX_ANSWERS`）。
 */
export function answersFor(questions: RecapQuestion[], picks: Record<string, string>): string[] {
  return questions
    .filter((q) => typeof picks[q.id] === "string" && q.options.includes(picks[q.id]))
    .map((q) => `${q.id}=${picks[q.id]}`)
    .slice(0, RECAP_ANSWERS_MAX);
}

/**
 * §63.11 「转写原版」的正文（`baseline.copy_*`，daemon 出稿时渲染好的那一份）。
 * 那一版缺这门语言就退到另一门——空 `<pre>` 配一颗可用的复制键比一份英文正文差得多
 * （与 daemon 侧 Slack 草稿正文的同一条口径）。
 */
export function baselineBody(row: RecapRow, language: Language): string {
  const baseline = row.baseline;
  if (!baseline || typeof baseline !== "object") return "";
  const asked = language === "zh" ? baseline.copy_zh : baseline.copy_en;
  const other = language === "zh" ? baseline.copy_en : baseline.copy_zh;
  if (typeof asked === "string" && asked.trim()) return asked;
  return typeof other === "string" ? other : "";
}

/** 有没有一份「转写原版」可切——**两门语言任一**有正文即可（切换在语言之上） */
export function hasBaseline(row: RecapRow): boolean {
  return Boolean(baselineBody(row, "en").trim() || baselineBody(row, "zh").trim());
}

/** §63.11 视图：current = 我记录的版本（记录上这一版）｜baseline = 转写原版 */
export type RecapView = "current" | "baseline";

/** 两版切换的两项（文案仍走唯一的双语机制 text(zh, en)） */
export const RECAP_VIEWS: { id: RecapView; zh: string; en: string }[] = [
  { id: "baseline", zh: "转写原版", en: "What the transcript said" },
  { id: "current", zh: "我记录的版本", en: "What I chose to record" },
];

/** §63.10 这一版的分节正文（手改坏的 wire 上什么都可能有——只留像样的节） */
export function recapSections(row: RecapRow, language: Language): RecapSection[] {
  const raw = language === "zh" ? row.sections_zh : row.sections_en;
  const rows = Array.isArray(raw) ? raw : [];
  return rows.filter((sec): sec is RecapSection =>
    Boolean(sec) && typeof sec === "object" && Array.isArray((sec as RecapSection).items));
}

/** §63.13 面板「转写依据」的一行：条目的标签（只认**正文里真出现过的**；没有 = 空串，面板改用条目原文认它）、
 *  条目原文、戳、原话片段 */
export interface RecapEvidence {
  tag: string;
  item: string;
  at: string;
  quote: string;
}

/**
 * §63.13 这一版每一条的转写锚（**只从 `sections_en` 读**：锚是转写的事实、与条目的语言无关，
 * 两语言按位置是同一条）。`tags` / `anchors` / `items` 逐位对齐；没有锚的条目（老记录 / 模型漏了）
 * 不列；手改坏的 wire（非对象、缺字段）滤掉。标签**只认 daemon 渲染进 `body`（`copy_*`）里的那几个**
 * （`bodyTags`）——client 不再按位置自己编号：略掉的填充条目、`tag_ok` 拒掉的标签都不在正文里，
 * 自己数出来的号会指到正文里没有的一行。wire 上带着标签而正文里没有它的条目 = daemon 把它略掉了
 * （填充值「无 / none」），纸上没有的东西不给依据、整条不列；没有标签的条目（§63.12 之前的老记录）
 * 仍列，面板改用条目原文认它。
 * **纯展示层**：锚从不进 `recapBody()` / `recapClipboardText()`——§63.3 的禁项对粘出去的那一份照旧成立。
 */
export function recapAnchors(row: RecapRow, body: string): RecapEvidence[] {
  const inBody = new Set(bodyTags(body));
  const out: RecapEvidence[] = [];
  for (const sec of recapSections(row, "en")) {
    const anchors = Array.isArray(sec.anchors) ? sec.anchors : [];
    const tags = Array.isArray(sec.tags) ? sec.tags : [];
    sec.items.forEach((item, i) => {
      const anchor = anchors[i];
      if (!anchor || typeof anchor !== "object") return;
      if (typeof anchor.at !== "string" || typeof anchor.quote !== "string" || !anchor.quote.trim()) return;
      const wireTag = typeof tags[i] === "string" ? tags[i] : "";
      if (wireTag && !inBody.has(wireTag)) return;          // 有标签却不在纸上 = 被略掉的条目
      out.push({ tag: wireTag, item: typeof item === "string" ? item : "", at: anchor.at, quote: anchor.quote });
    });
  }
  return out;
}

/** 「转写依据」一行的名字：有正文里的标签就 `#D1`，否则条目原文（截到 `EVIDENCE_ITEM_CHARS` 字） */
export const EVIDENCE_ITEM_CHARS = 40;

export function evidenceLabel(entry: RecapEvidence): string {
  if (entry.tag) return `#${entry.tag}`;
  const text = entry.item.trim();
  return text.length > EVIDENCE_ITEM_CHARS ? `${text.slice(0, EVIDENCE_ITEM_CHARS)}…` : text;
}

/**
 * §63.10 这一行有没有正文——**两种形状都算**（`act/lib/recap_store.has_text` 的镜像）。
 * 只看 `en` 会让一份可发送长版在 badge、按钮、脚注里处处被当成「没出稿」。
 * 长版这一支问的是 daemon 渲染好的正文非不非空（不是 `sections_en` 列表非不非空）：
 * 「空」在这个系统里是**一个**判据，否则这里会给一个空 `<pre>` 配一颗可用的复制键。
 */
export function hasRecapText(row: RecapRow): boolean {
  if (Array.isArray(row.en) && row.en.length) return true;
  return Boolean(recapBody(row, "en").trim());
}

/** 详情默认语言：recap.default_language auto 跟随 UI 语言 */
export function pickLanguage(defaultLanguage: string | undefined, ui: Language): Language {
  return defaultLanguage === "zh" || defaultLanguage === "en" ? defaultLanguage : ui;
}

/**
 * 复制正文（issue #129 §4；`<pre>` 显示的也是它 = 所见即所复制）。
 * §63.10：**daemon 渲染好的 `copy_*` 优先**——空的那几行 / 那几节已经略掉、可发送长版
 * 的节标题与跨节编号也已经加好。渲染只有 `act/lib/recap_text` 一处，client 不实现第二套
 * （防腐 #10）；老 daemon 没有这个键时退回把五行换行拼起来（旧行为一字不变）。
 */
export function recapBody(row: RecapRow, language: Language): string {
  const body = language === "zh" ? row.copy_zh : row.copy_en;
  if (typeof body === "string" && body.trim()) return body;
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

/** §63.11 面板上**正在看的那一份**正文（切到「转写原版」= 第一版渲染好的那份） */
export function recapViewBody(row: RecapRow, view: RecapView, language: Language): string {
  return view === "baseline" ? baselineBody(row, language) : recapBody(row, language);
}

/** 剪贴板文本 = 一行表头 + 正文（§63.5 追记）；`view`（§63.11）默认是记录上这一版
 *  ——复制永远等于**屏幕上那一份**，两版切换因此不需要第二条复制路径 */
export function recapClipboardText(row: RecapRow, language: Language,
                                   view: RecapView = "current"): string {
  return `${recapHeader(row, language)}\n${recapViewBody(row, view, language)}`;
}

type Bilingual = (zh: string, en: string) => string;

/**
 * §63.9（issue #300）**行级引用标签** D / S / L / C / O：五行的标签文字与顺序是固定的
 * （`act/lib/recap_text.LABELS_EN` / `LABELS_ZH`，§63.3 的硬闸），所以**位置本身就是身份**
 * ——不需要在 wire 上给每行发一个 id 就能把一行citable。粘出去的五行正文一字不变
 * （引用串是另一次复制，chip 各自一颗）。
 * §63.12 追记（#300 的后半已落地）：可发送长版的逐条 `D1` / `S2` 形**现在存在**且跨版稳定
 * （daemon 派发、渲染进正文），所以那一形的引用 chip 由 `bodyTags` / `itemCitation` 给出——
 * 本表仍然只管五行形（它没有条目，身份是位置）。
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

/**
 * §63.12 一条渲染出来的条目行前面那个**跨版稳定的标签**（`D1. …` → `D1`）——节标题与
 * 没有标签的条目（本节之前生成的老记录）= 空串。字母表逐字镜像
 * `act/lib/recap_text.SECTION_LETTERS`（D/S/P/L/C/O）。
 */
const ITEM_TAG_LINE = /^([DSPLCO][1-9]\d?)\.\s/;

export function itemTag(line: string): string {
  const hit = ITEM_TAG_LINE.exec(line ?? "");
  return hit ? hit[1] : "";
}

/**
 * §63.12 可发送长版的两版逐行比——**按标签**而不是按位置：一条被移动 / 改写过的条目
 * 因此显示成「改」而不是「新」（位置比会把插入一条之后的每一行都判成变了）。
 * 仍然是纯字符串比较、零模型、零请求（§63.9 的同一条纪律）。
 * 没有标签的行（节标题、老记录的条目）回落到「这一行在对面那一版里出现过吗」——
 * 标题不会因为上面插了一节就被说成变了。`current` 里有、`previous` 里没有那个标签 = 新 = 变了。
 * 节与节之间的空行是**结构**（`render_sections` 加的），永不算变。
 */
export function changedItems(current: string[], previous: string[]): boolean[] {
  const byTag = new Map<string, string>();
  const seen = new Set<string>();
  for (const line of previous) {
    const tag = itemTag(line);
    if (tag && !byTag.has(tag)) byTag.set(tag, line);
    seen.add(line);
  }
  return current.map((line) => {
    if (!(line ?? "").trim()) return false;          // 节之间的空行是结构，不是内容
    const tag = itemTag(line);
    if (!tag) return !seen.has(line);
    const before = byTag.get(tag);
    return before === undefined || before !== line;
  });
}

/**
 * §63.12 一份渲染好的正文里出现过的标签（渲染顺序，去重）——面板据它给逐条引用 chip。
 * 正文本身一个字符不改：标签本来就**在**粘出去的那一份里（`D1. …`），chip 只是把
 * 引用串整理成一次复制。
 */
export function bodyTags(body: string): string[] {
  const out: string[] = [];
  for (const line of (body ?? "").split("\n")) {
    const tag = itemTag(line);
    if (tag && !out.includes(tag)) out.push(tag);
  }
  return out;
}

/**
 * §63.12 一条的引用串 `2026-08-31 Zoom #D1`（日期 / 应用与 §63.9 的行引用逐字同源）。
 * 空标签 = 空串。**纯展示层**：不进 `recapBody()` / `recapClipboardText()`。
 */
export function itemCitation(row: RecapRow, tag: string): string {
  if (!tag) return "";
  return `${dayKey(row.start)} ${appLabel(row.app)} #${tag}`;
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

/**
 * §63.3 追记：wire 上的发现行（老 daemon 无此键、手改过的文件可能是任意东西）——只留说得出话的对象：
 * `code` 非空（或至少 `text` 非空——词表外的 code 靠它兜底，§63.5 追记「永不显示一行空白」），
 * `text` 若在必须是字符串（一个对象进了 JSX 会让整块看板崩掉，宪法第 11 条）。
 */
export function recapProblems(row: RecapRow): RecapProblem[] {
  const rows = Array.isArray(row.problems) ? row.problems : [];
  return rows.filter((p): p is RecapProblem =>
    Boolean(p) && typeof p === "object" && typeof p.code === "string"
    && (p.text == null || typeof p.text === "string") && (p.code !== "" || Boolean(p.text)));
}

/** 同上，修剪台账：一行修剪必须有语言、行号与超出量三个数才说得出话（`removed` 是后来的 add-only 键，可缺） */
export function recapRepairs(row: RecapRow): RecapRepair[] {
  const rows = Array.isArray(row.repairs) ? row.repairs : [];
  return rows.filter((r): r is RecapRepair =>
    Boolean(r) && typeof r === "object" && typeof r.lang === "string"
    && typeof r.line === "number" && typeof r.over === "number");
}

/** 「英文第 3 行」/「English line 3」；整语言级的禁项没有行号（daemon 给 null）= 只说语言 */
function where(lang: unknown, line: unknown, text: Bilingual): string {
  const name = lang === "zh" ? text("中文", "Chinese") : lang === "en" ? text("英文", "English") : text("正文", "The text");
  return typeof line === "number" ? text(`${name}第 ${line} 行`, `${name} line ${line}`) : name;
}

/** §63.3 追记 一条校验原因的人话（code 词表 add-only；词表外的新 code 原样显示 daemon 那句英文） */
export function problemLabel(problem: RecapProblem, text: Bilingual): string {
  const at = where(problem.lang, problem.line, text);
  // §63.10 可发送长版的 `line` 是**条目号**（跨节连续，与粘出去的编号同一个数），不是行号
  // ——那几条自己把条目号说进句子里，前缀只说语言
  const lang = where(problem.lang, null, text);
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
    // §63.10 可发送长版的几条（issue #303）；`line` 是跨节连续的条目号，与粘出去的编号同一个数
    case "section_count":
      return text(`${lang}：分节数超过上限 ${problem.limit ?? "?"}`, `${lang}: more than ${problem.limit ?? "?"} sections`);
    case "section_key":
      return text(`${lang}：分节名不在固定表里，或顺序不对`, `${lang}: unknown section, or the sections are out of order`);
    case "section_modality":
      return text(`${lang}：语气不在固定表里（已定 / 提议 / 有人提过 / 待定）`,
                  `${lang}: modality is not one of decided / proposed / floated / open`);
    case "section_empty":
      return text(`${lang}：有一节是空的（空的那节应当整节略掉）`,
                  `${lang}: a section came back empty (an empty section is omitted instead)`);
    case "section_mismatch":
      return text("中英两版的分节必须一一对应", "The Chinese and English sections must match one another");
    case "item_count":
      return text(`${lang}：条目总数超过上限 ${problem.limit ?? "?"}`,
                  `${lang}: more than ${problem.limit ?? "?"} items in total`);
    case "item_too_long":
      return text(`${lang}：第 ${problem.line ?? "?"} 条超出上限 ${problem.over ?? "?"} 个字符（上限 ${problem.limit ?? "?"}）`,
                  `${lang}: item ${problem.line ?? "?"} is ${problem.over ?? "?"} characters over the ${problem.limit ?? "?"}-character cap`);
    case "item_numbered":
      return text(`${lang}：第 ${problem.line ?? "?"} 条自己带了编号（编号由程序统一加）`,
                  `${lang}: item ${problem.line ?? "?"} numbered itself (the numbering is added for it)`);
    // §63.13 逐条语气与转写锚（issue #440）
    case "item_modality":
      return text(`${lang}：第 ${problem.line ?? "?"} 条的语气不在固定表里（已定 / 提议 / 有人提过 / 待定），或与另一语言的那一条不一致`,
                  `${lang}: item ${problem.line ?? "?"} has a modality outside decided / proposed / floated / open, or one that differs from the other language`);
    case "item_unanchored":
      return text(`${lang}：第 ${problem.line ?? "?"} 条没有转写锚（转写那一行的时间戳 + 一段原话）`,
                  `${lang}: item ${problem.line ?? "?"} has no transcript anchor (the line's timestamp plus a verbatim fragment)`);
    case "anchor_unverified":
      return text(`${lang}：第 ${problem.line ?? "?"} 条的转写锚对不上转写（原话不是逐字的，或时间戳不是转写里的一行）`,
                  `${lang}: item ${problem.line ?? "?"}'s anchor does not match the transcript (the fragment is not verbatim, or the stamp is not a transcript line)`);
    case "item_mismatch":
      return text("中英两版有一节的条数不一致（每一条的语气与转写锚按位置共享，条数必须对齐）",
                  "A section has a different number of items in Chinese and English (modality and anchors are shared by position, so the counts must match)");
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
  const over = repair.over ?? "?";     // 过滤器已要求它是数字；直接调用方传来缺键的行也永不显示 undefined
  if (typeof repair.removed !== "number") {
    return text(`已自动修剪${at}（原来超出 ${over} 个字符）`,
                `Trimmed ${at} automatically (it was ${over} characters over)`);
  }
  return text(`已自动修剪${at}：剪掉行尾 ${repair.removed} 个字符（原来超出 ${over} 个）`,
              `Trimmed ${at} automatically: ${repair.removed} characters off the end (it was ${over} over the cap)`);
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
