// 录制日程的文案表（CONTRACT §61.7）：header 状态词 / 菜单说明 / 设置区 / 录制页共用，
// 数据本体是壳快照 `recording.schedule`（shellBridge.ts ShellRecordingSchedule），这里只把它说成人话。
import type { ShellRecordingSchedule } from "../../shellBridge";

type Text = (zh: string, en: string) => string;

/** Calendar weekday 1 = 周日 … 7 = 周六（与壳 / UserDefaults 同一编号；渲染顺序从周一起） */
export const WEEKDAYS: ReadonlyArray<{ id: number; zh: string; en: string }> = [
  { id: 2, zh: "一", en: "Mon" },
  { id: 3, zh: "二", en: "Tue" },
  { id: 4, zh: "三", en: "Wed" },
  { id: 5, zh: "四", en: "Thu" },
  { id: 6, zh: "五", en: "Fri" },
  { id: 7, zh: "六", en: "Sat" },
  { id: 1, zh: "日", en: "Sun" },
];

/** 状态词：与「关」「未在录制」并列的第三个非录制态——引擎是被日程**故意**停着的 */
export function scheduledPauseWord(text: Text): string {
  return text("按日程暂停", "Paused by schedule");
}

/** 勾选日的人话："周一至周五" / "Mon–Fri"；不连续时逐日列（"周一、三、五" / "Mon, Wed, Fri"）；七天 = "每天" */
export function daysLabel(days: number[], text: Text): string {
  const picked = WEEKDAYS.filter((d) => days.includes(d.id));
  if (picked.length === 7) return text("每天", "every day");
  if (picked.length === 0) return text("（未选日子）", "(no days)");
  const consecutive = picked.length >= 2
    && picked.every((d, i) => i === 0 || WEEKDAYS.indexOf(d) === WEEKDAYS.indexOf(picked[i - 1]) + 1);
  if (consecutive) {
    return text(`周${picked[0].zh}至周${picked[picked.length - 1].zh}`, `${picked[0].en}–${picked[picked.length - 1].en}`);
  }
  return text(`周${picked.map((d) => d.zh).join("、")}`, picked.map((d) => d.en).join(", "));
}

/** "09:00–19:00 · 周一至周五" / "09:00–19:00 · Mon–Fri"；跨午夜窗口带「次日」提示 */
export function scheduleSummary(s: ShellRecordingSchedule, text: Text): string {
  const overnight = s.start > s.end;
  const range = overnight ? text(`${s.start}–次日 ${s.end}`, `${s.start}–${s.end} next day`) : `${s.start}–${s.end}`;
  return `${range} · ${daysLabel(s.days, text)}`;
}

/** 暂停中的整句（菜单说明行 / 设置区引擎行）："按日程暂停中——只在 09:00–19:00 · 周一至周五 录制，到点自动恢复" */
export function scheduledPauseSentence(s: ShellRecordingSchedule, text: Text): string {
  return text(
    `按日程暂停中——只在 ${scheduleSummary(s, text)} 录制，到点自动恢复`,
    `Paused by schedule — recording only ${scheduleSummary(s, text)}; resumes automatically`,
  );
}
