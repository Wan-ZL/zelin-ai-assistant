// 录制日程 wire 的镜像判例（CONTRACT §61.7；§61.1 add-only `recording.schedule`）：normalize 后永远在场
// （老壳缺席 → 关 / 09:00–19:00 / 周一至周五 / 未暂停），壳给的值逐字落下，脏值退默认；`schedulePaused` 是页面侧唯一判据
// （mode off 恒 false）；`setRecordingSchedule` 在方法词表里、经 callShell 原样发出。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { callShell, DEFAULT_RECORDING_SCHEDULE, normalizeShellState, resetShellBridgeForTests, schedulePaused } from "./shellBridge";
import { daysLabel, scheduledPauseSentence, scheduleSummary } from "./components/shell/recordingSchedule";

const en = (_zh: string, e: string) => e;
const zh = (z: string) => z;

describe("shellBridge · §61.7 recording.schedule", () => {
  beforeEach(() => resetShellBridgeForTests());
  afterEach(() => {
    delete window.webkit;
  });

  it("老壳缺席 → 默认日程（关、09:00–19:00、周一至周五、未暂停）；整个快照缺也一样", () => {
    const s = normalizeShellState({ recording: { mode: "screen" } });
    expect(s.recording.schedule).toEqual({ enabled: false, start: "09:00", end: "19:00", days: [2, 3, 4, 5, 6], paused: false });
    expect(normalizeShellState(null).recording.schedule).toEqual(DEFAULT_RECORDING_SCHEDULE);
    // 默认对象不被共享改坏（days 每次是新数组）
    expect(normalizeShellState(null).recording.schedule!.days).not.toBe(DEFAULT_RECORDING_SCHEDULE.days);
  });

  it("壳给的值逐字落下（days 去重排序）", () => {
    const s = normalizeShellState({ recording: { mode: "screen", schedule: { enabled: true, start: "22:00", end: "02:00", days: [6, 2, 2], paused: true } } });
    expect(s.recording.schedule).toEqual({ enabled: true, start: "22:00", end: "02:00", days: [2, 6], paused: true });
  });

  it("脏值退默认（LLM 式脏值不崩页面）：非 HH:MM、非数组 / 越界 / 空 days、字串布尔", () => {
    const s = normalizeShellState({ recording: { mode: "screen", schedule: { enabled: "yes", start: "9am", end: 1900, days: [0, 8, "2"], paused: "true" } } });
    expect(s.recording.schedule).toEqual({ enabled: false, start: "09:00", end: "19:00", days: [2, 3, 4, 5, 6], paused: false });
    expect(normalizeShellState({ recording: { schedule: "nope" } }).recording.schedule).toEqual(DEFAULT_RECORDING_SCHEDULE);
  });

  it("schedulePaused：mode off 恒 false；开着且壳说 paused 才 true；缺 schedule = false", () => {
    const sched = { ...DEFAULT_RECORDING_SCHEDULE, enabled: true, paused: true };
    expect(schedulePaused({ mode: "off", schedule: sched })).toBe(false);
    expect(schedulePaused({ mode: "screen", schedule: sched })).toBe(true);
    expect(schedulePaused({ mode: "screen", schedule: { ...sched, paused: false } })).toBe(false);
    expect(schedulePaused({ mode: "screen", schedule: undefined })).toBe(false);
  });

  it("setRecordingSchedule 在词表里、参数原样发出，回执成为快照", async () => {
    const postMessage = vi.fn(async () => ({ recording: { mode: "screen", schedule: { enabled: true, start: "08:30", end: "17:45", days: [2, 4], paused: false } }, captions: {} }));
    window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
    const state = await callShell("setRecordingSchedule", { enabled: true, start: "08:30", end: "17:45", days: [2, 4] });
    expect(postMessage).toHaveBeenCalledWith({ method: "setRecordingSchedule", enabled: true, start: "08:30", end: "17:45", days: [2, 4] });
    expect(state.recording.schedule).toEqual({ enabled: true, start: "08:30", end: "17:45", days: [2, 4], paused: false });
  });
});

describe("recordingSchedule · 文案表", () => {
  it("daysLabel：连续 = 区间、不连续 = 逐日、七天 = 每天（两种语言）", () => {
    expect(daysLabel([2, 3, 4, 5, 6], en)).toBe("Mon–Fri");
    expect(daysLabel([2, 3, 4, 5, 6], zh)).toBe("周一至周五");
    expect(daysLabel([2, 4, 6], en)).toBe("Mon, Wed, Fri");
    expect(daysLabel([2, 4, 6], zh)).toBe("周一、三、五");
    expect(daysLabel([1, 2, 3, 4, 5, 6, 7], en)).toBe("every day");
    expect(daysLabel([7, 1], en)).toBe("Sat–Sun");   // 周六→周日在渲染序里相邻
    expect(daysLabel([2], en)).toBe("Mon");
  });

  it("scheduleSummary / scheduledPauseSentence：同日与跨午夜", () => {
    const day = { enabled: true, start: "09:00", end: "19:00", days: [2, 3, 4, 5, 6], paused: true };
    expect(scheduleSummary(day, en)).toBe("09:00–19:00 · Mon–Fri");
    expect(scheduledPauseSentence(day, en)).toBe("Paused by schedule — recording only 09:00–19:00 · Mon–Fri; resumes automatically");
    expect(scheduledPauseSentence(day, zh)).toBe("按日程暂停中——只在 09:00–19:00 · 周一至周五 录制，到点自动恢复");
    const night = { ...day, start: "22:00", end: "02:00", days: [6] };
    expect(scheduleSummary(night, en)).toBe("22:00–02:00 next day · Fri");
    expect(scheduleSummary(night, zh)).toBe("22:00–次日 02:00 · 周五");
  });
});
