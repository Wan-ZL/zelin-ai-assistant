// header 录制菜单的键盘导航判例（CONTRACT §68.3 追记，parity 批 `recording-consent-header-ui`；原生 SwiftUI Menu → NSMenu，
// DashboardView.swift:27-110：打开即高亮当前档、↑↓ 移动、Return 激活、Esc 收起且焦点回按钮）：
//   1) 打开菜单 → 焦点落在勾着的 menuitemradio 上；
//   2) ↓ / ↑ 在可用项间循环（禁用的「重启录制引擎」跳过）、Home / End 到两端、无关键不拦；
//   3) Esc 关菜单并把焦点还给触发按钮；点选一项也还；Tab 关菜单让焦点自然走；
//   4) 菜单里 consent-race 自愈成功句（self_heal_note，绿）排在拒绝说明之前。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { resetShellBridgeForTests, type ShellRecordingState } from "../../shellBridge";
import { RecordingControl } from "./RecordingControl";

const postMessage = vi.fn<(body: unknown) => Promise<unknown>>();

function rec(over: Partial<ShellRecordingState> = {}): ShellRecordingState {
  return {
    available: true, on: true, mode: "screen", engine_running: true, diagnosis: null, note: "", tcc_lost: false,
    screen_permission: true, resume_mode: "screen", self_heal_note: "", log_tail: "", ...over,
  };
}

function renderControl(state: ShellRecordingState) {
  return render(<LanguageContext.Provider value="en"><RecordingControl state={state} /></LanguageContext.Provider>);
}

const trigger = () => screen.getByRole("button", { name: "Recording controls" });
const menu = () => screen.queryByRole("menu");
const item = (name: string) => screen.getByRole("menuitemradio", { name }) as HTMLButtonElement;
const action = (name: string) => screen.getByRole("menuitem", { name }) as HTMLButtonElement;
const key = (k: string) => fireEvent.keyDown(document.activeElement ?? document.body, { key: k });

beforeEach(() => {
  resetShellBridgeForTests();
  postMessage.mockReset();
  postMessage.mockImplementation(async () => ({ recording: rec(), captions: {}, permissions: {} }));
  (window as Window & { webkit?: unknown }).webkit = { messageHandlers: { zaiShell: { postMessage } } };
});

afterEach(() => {
  cleanup();
  delete (window as Window & { webkit?: unknown }).webkit;
});

describe("RecordingControl · 打开即聚焦", () => {
  it("opening the menu moves focus to the checked menuitemradio", () => {
    renderControl(rec({ mode: "screen_audio" }));
    fireEvent.click(trigger());
    expect(menu()).not.toBeNull();
    expect(document.activeElement).toBe(item("Screen + audio"));
    expect(item("Screen + audio").getAttribute("aria-checked")).toBe("true");
  });

  it("off state: focus lands on 「Off」 (the checked one), never on the disabled restart item", () => {
    renderControl(rec({ on: false, mode: "off", engine_running: false }));
    fireEvent.click(trigger());
    expect(document.activeElement).toBe(item("Off"));
    expect(action("Restart recording engine").disabled).toBe(true);
  });
});

describe("RecordingControl · ↑↓ 循环 / Home / End", () => {
  it("ArrowDown walks Off → Screen only → Screen + audio → Restart → wraps to Off; ArrowUp walks back and wraps", () => {
    renderControl(rec({ mode: "off", on: false, engine_running: false, screen_permission: false }));
    fireEvent.click(trigger());
    // 关态：重启禁用、跳过；缺权限 → 「打开系统设置」在场
    expect(document.activeElement).toBe(item("Off"));
    expect(key("ArrowDown")).toBe(false); // preventDefault
    expect(document.activeElement).toBe(item("Screen only"));
    key("ArrowDown");
    expect(document.activeElement).toBe(item("Screen + audio"));
    key("ArrowDown");
    expect(document.activeElement).toBe(action("Open System Settings → Screen Recording"));
    key("ArrowDown");
    expect(document.activeElement).toBe(item("Off")); // wrap
    key("ArrowUp");
    expect(document.activeElement).toBe(action("Open System Settings → Screen Recording")); // wrap the other way
    key("ArrowUp");
    expect(document.activeElement).toBe(item("Screen + audio"));
  });

  it("Home / End jump to the first / last enabled item; an unrelated key is left alone", () => {
    renderControl(rec({ mode: "screen" }));
    fireEvent.click(trigger());
    expect(document.activeElement).toBe(item("Screen only"));
    key("End");
    expect(document.activeElement).toBe(action("Restart recording engine"));
    key("Home");
    expect(document.activeElement).toBe(item("Off"));
    expect(key("a")).toBe(true); // not prevented
    expect(document.activeElement).toBe(item("Off"));
    expect(menu()).not.toBeNull();
  });
});

describe("RecordingControl · 关菜单与焦点归还", () => {
  it("Escape closes the menu and returns focus to the trigger", () => {
    renderControl(rec());
    fireEvent.click(trigger());
    expect(document.activeElement).toBe(item("Screen only"));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it("picking an item closes the menu, returns focus to the trigger and sends setRecording", async () => {
    renderControl(rec({ mode: "screen" }));
    fireEvent.click(trigger());
    key("ArrowDown");
    expect(document.activeElement).toBe(item("Screen + audio"));
    fireEvent.click(document.activeElement!);
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(trigger());
    await vi.waitFor(() => expect(postMessage).toHaveBeenCalledWith({ method: "setRecording", on: true, mode: "screen_audio" }));
  });

  it("Tab closes the menu without trapping focus (default not prevented)", () => {
    renderControl(rec());
    fireEvent.click(trigger());
    expect(key("Tab")).toBe(true);
    expect(menu()).toBeNull();
  });
});

describe("RecordingControl · 菜单里的自愈成功句", () => {
  it("self_heal_note renders green (role=status) ahead of the refusal note", () => {
    renderControl(rec({ self_heal_note: "屏幕权限已生效，录制引擎已自动重启", note: "拒绝了这次切换" }));
    fireEvent.click(trigger());
    const ok = screen.getByRole("status");
    expect(ok.className).toBe("shell-menu-note is-ok");
    expect(ok.textContent).toBe("屏幕权限已生效，录制引擎已自动重启");
    const notes = Array.from(menu()!.querySelectorAll(".shell-menu-note")).map((el) => el.className);
    expect(notes.indexOf("shell-menu-note is-ok")).toBeLessThan(notes.indexOf("shell-menu-note is-warn"));
  });

  it("empty self_heal_note renders nothing", () => {
    renderControl(rec());
    fireEvent.click(trigger());
    expect(screen.queryByRole("status")).toBeNull();
    expect(menu()!.querySelector(".shell-menu-note.is-ok")).toBeNull();
  });
});
