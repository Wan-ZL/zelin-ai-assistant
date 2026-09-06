// 设置 → 录制区的日程块（CONTRACT §61.7）：开关 / 星期即改即发、时刻失焦 / 回车才发且只发改动的键；坏时刻页面先拦、
// 至少留一天；桥 reject 原文照印 + 时刻回滚到壳的真相；老壳（UNKNOWN_METHOD）退成说明句；暂停中引擎行说日程句、
// 「重启录制引擎」禁用、不是橙色警告；浏览器（无桥）整块不出。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { applyShellState, resetShellBridgeForTests, type ShellState } from "../../shellBridge";
import { isClock, RecordingSection } from "./RecordingSection";

const base: ShellState = {
  recording: {
    available: true, on: true, mode: "screen", engine_running: true, diagnosis: null, note: "", tcc_lost: false, screen_permission: true,
    resume_mode: "screen", self_heal_note: "", log_tail: "",
    schedule: { enabled: false, start: "09:00", end: "19:00", days: [2, 3, 4, 5, 6], paused: false },
  },
  captions: { available: true, on: false, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false, source: "both", translate: false, translate_direction: "auto", apple_locale: "zh", ark_model: "", font_size: 24, opacity: 0.7 },
  permissions: { screen: "granted", microphone: "unknown", notifications: "unknown", vault: "unknown" },
  launch_at_login: false, hotkey: "⌃⌥Space",
};

const postMessage = vi.fn<(body: unknown) => Promise<unknown>>();

function installShell(recording: Partial<ShellState["recording"]> = {}, schedule: Partial<NonNullable<ShellState["recording"]["schedule"]>> = {}) {
  const state: ShellState = { ...base, recording: { ...base.recording, ...recording, schedule: { ...base.recording.schedule!, ...schedule } } };
  postMessage.mockReset();
  postMessage.mockImplementation(async (body: unknown) => {
    const { method, ...args } = body as { method: string } & Record<string, unknown>;
    if (method === "setRecordingSchedule") {
      return { ...state, recording: { ...state.recording, schedule: { ...state.recording.schedule!, ...args } } };
    }
    return state;
  });
  window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
  applyShellState(state);
  return state;
}

const renderEn = () => render(<LanguageContext.Provider value="en"><RecordingSection /></LanguageContext.Provider>);
const scheduleCalls = () => postMessage.mock.calls.map((c) => c[0] as Record<string, unknown>).filter((b) => b.method === "setRecordingSchedule");

beforeEach(() => resetShellBridgeForTests());
afterEach(() => {
  cleanup();
  delete window.webkit;
});

describe("RecordingSection · schedule block (§61.7)", () => {
  it("isClock：严格 HH:MM", () => {
    expect(isClock("09:00")).toBe(true);
    expect(isClock("23:59")).toBe(true);
    for (const bad of ["9:00", "24:00", "09:60", "0900", ""]) expect(isClock(bad)).toBe(false);
  });

  it("浏览器里（无桥）整块不出", () => {
    renderEn();
    expect(screen.queryByRole("switch", { name: "Record only between…" })).toBeNull();
  });

  it("默认关：开关 off、说明句 = 现状；时间窗 fieldset 禁用", () => {
    installShell();
    renderEn();
    const toggle = screen.getByRole("switch", { name: "Record only between…" }) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    expect(screen.getByText("Off = records whenever recording is on (current behavior).")).toBeTruthy();
    expect((screen.getByRole("group", { name: "Recording window" }) as HTMLFieldSetElement).disabled).toBe(true);
    // 引擎行照旧「正在录制」，不带暂停标记
    expect(screen.getByText(/^Engine: /).getAttribute("data-paused")).toBeNull();
  });

  it("开关即发 {enabled}，壳回执把 fieldset 解锁并说出日程摘要", async () => {
    installShell();
    renderEn();
    fireEvent.click(screen.getByRole("switch", { name: "Record only between…" }));
    expect(scheduleCalls()).toEqual([{ method: "setRecordingSchedule", enabled: true }]);
    await waitFor(() => expect((screen.getByRole("switch", { name: "Record only between…" }) as HTMLInputElement).checked).toBe(true));
    expect((screen.getByRole("group", { name: "Recording window" }) as HTMLFieldSetElement).disabled).toBe(false);
    expect(screen.getByText("Recording only 09:00–19:00 · Mon–Fri; the engine stays off outside that window and resumes on its own.")).toBeTruthy();
  });

  it("时刻：输入中不发、失焦才发且只发改动的键；回车也发；未改动不发", () => {
    installShell({}, { enabled: true });
    renderEn();
    const start = screen.getByLabelText("From") as HTMLInputElement;
    fireEvent.change(start, { target: { value: "08:30" } });
    expect(scheduleCalls()).toEqual([]);
    fireEvent.blur(start, { target: { value: "08:30" } });
    expect(scheduleCalls()).toEqual([{ method: "setRecordingSchedule", start: "08:30" }]);
    const end = screen.getByLabelText("to") as HTMLInputElement;
    fireEvent.change(end, { target: { value: "17:45" } });
    fireEvent.keyDown(end, { key: "Enter", target: { value: "17:45" } });
    expect(scheduleCalls().at(-1)).toEqual({ method: "setRecordingSchedule", end: "17:45" });
    // 失焦时值等于壳的真相 → 不发
    fireEvent.blur(start, { target: { value: "09:00" } });
    expect(scheduleCalls()).toHaveLength(2);
  });

  it("坏时刻页面先拦（不打桥）并回滚输入（type=time 把乱写清成空串——jsdom 与 WebKit 同样）", async () => {
    installShell({}, { enabled: true });
    renderEn();
    const start = screen.getByLabelText("From") as HTMLInputElement;
    fireEvent.change(start, { target: { value: "9am" } });
    expect(start.value).toBe("");
    fireEvent.blur(start, { target: { value: "" } });
    expect(scheduleCalls()).toEqual([]);
    expect(screen.getByRole("alert").textContent).toBe("Time must be HH:MM (for example 09:00)");
    // 回滚 = 输入重挂（key 变），所以要重新取元素
    await waitFor(() => expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("09:00"));
  });

  it("星期勾选即发整份 days；取消最后一天被拦（至少留一天）", () => {
    installShell({}, { enabled: true, days: [2] });
    renderEn();
    fireEvent.click(screen.getByRole("checkbox", { name: "Wed" }));
    expect(scheduleCalls()).toEqual([{ method: "setRecordingSchedule", days: [2, 4] }]);
    fireEvent.click(screen.getByRole("checkbox", { name: "Mon" }));
    expect(scheduleCalls()).toHaveLength(1);
    expect(screen.getByRole("alert").textContent).toBe("Keep at least one day");
  });

  it("桥 reject：原文照印，时刻回滚到壳的真相", async () => {
    installShell({}, { enabled: true });
    postMessage.mockImplementation(async (body: unknown) => {
      if ((body as { method: string }).method === "setRecordingSchedule") throw new Error("INVALID_ARGS: start and end must differ");
      return base;
    });
    renderEn();
    const start = screen.getByLabelText("From") as HTMLInputElement;
    fireEvent.change(start, { target: { value: "19:00" } });
    fireEvent.blur(start, { target: { value: "19:00" } });
    await screen.findByText("INVALID_ARGS: start and end must differ");
    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("09:00");
  });

  it("老壳（UNKNOWN_METHOD）→ 控件让位给说明句", async () => {
    installShell();
    postMessage.mockImplementation(async (body: unknown) => {
      if ((body as { method: string }).method === "setRecordingSchedule") throw new Error("UNKNOWN_METHOD: setRecordingSchedule");
      return base;
    });
    renderEn();
    fireEvent.click(screen.getByRole("switch", { name: "Record only between…" }));
    await screen.findByText("This board app build does not know recording schedules yet — update the shell and come back.");
    expect(screen.queryByRole("group", { name: "Recording window" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("暂停中：引擎行说日程句（不是橙色警告）、重启禁用；mode off 时不算暂停", () => {
    installShell({ engine_running: false }, { enabled: true, paused: true });
    renderEn();
    const engine = screen.getByText(/^Engine: /);
    expect(engine.textContent).toBe("Engine: Paused by schedule — recording only 09:00–19:00 · Mon–Fri; resumes automatically");
    expect(engine.className).not.toContain("is-warning");
    expect(engine.getAttribute("data-paused")).toBe("true");
    expect((screen.getByRole("button", { name: "Restart engine" }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    installShell({ mode: "off", on: false, engine_running: false }, { enabled: true, paused: true });
    renderEn();
    expect(screen.getByText(/^Engine: /).textContent).toBe("Engine: Off");
  });
});
