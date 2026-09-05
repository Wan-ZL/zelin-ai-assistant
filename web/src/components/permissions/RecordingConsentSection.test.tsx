// 屏幕记录同意块 / 状态行的键盘与说明句判例（CONTRACT §68.3 追记，parity 批 `recording-consent-header-ui`；原生
// Permissions.swift:415-421 `.keyboardShortcut(.defaultAction)` 与 :466-486 的 else-if 三句）：
//   1) 披露块挂载即把焦点放在「开启」上（Return 立刻就是它）；
//   2) 块内焦点不在按钮 / 链接上时 Return = 点「开启」（preventDefault 给 §68.5 向导让路），IME 组字 / 修饰键 / 焦点在「暂不」
//      或链接上不抢——「暂不」照旧只有点击 / Tab；
//   3) 状态行：自愈成功句（self_heal_note，绿 role=status）> 拒绝说明（note，橙）> TCC 收回句，同一时刻只出一句；
//   4) 「开启」只发 `setRecording {on:true, mode:"screen"}`——TCC 提示是桥的活（§61.1 追记 (a)），web 不发 requestPermission。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { applyShellState, resetShellBridgeForTests, type ShellState } from "../../shellBridge";
import { resetStoreForTests } from "../../store";
import { consentReturnActivates, RecordingConsentSection } from "./RecordingConsentSection";

const base: ShellState = {
  recording: { available: true, on: false, mode: "off", engine_running: false, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen", self_heal_note: "", log_tail: "" },
  captions: { available: true, on: false, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false, source: "both", translate: false, translate_direction: "auto", apple_locale: "zh", ark_model: "", font_size: 24, opacity: 0.7 },
  permissions: { screen: "granted", microphone: "unknown", notifications: "unknown", vault: "unknown", screen_requested: false },
  launch_at_login: false, hotkey: "⌃⌥Space",
};

const postMessage = vi.fn<(body: unknown) => Promise<unknown>>();

function installShell(recording: Partial<ShellState["recording"]> = {}) {
  const state: ShellState = { ...base, recording: { ...base.recording, ...recording } };
  postMessage.mockReset();
  postMessage.mockImplementation(async () => state);
  (window as Window & { webkit?: unknown }).webkit = { messageHandlers: { zaiShell: { postMessage } } };
  applyShellState(state);
}

const renderSection = (language: "zh" | "en" = "en") =>
  render(<LanguageContext.Provider value={language}><RecordingConsentSection /></LanguageContext.Provider>);

const setRecordingCalls = () => postMessage.mock.calls.filter(([body]) => (body as { method: string }).method === "setRecording");

beforeEach(() => {
  resetStoreForTests();      // setup 为 null → 录制关着就还没答过一次性同意
  resetShellBridgeForTests();
});

afterEach(() => {
  cleanup();
  delete (window as Window & { webkit?: unknown }).webkit;
});

describe("consentReturnActivates（原生 defaultAction 的判据）", () => {
  const ev = (over: Partial<Parameters<typeof consentReturnActivates>[0]> = {}) => ({
    key: "Enter", altKey: false, ctrlKey: false, metaKey: false, shiftKey: false, isComposing: false, target: document.createElement("div"), ...over,
  });

  it("plain Enter on a non-owner target activates; other keys, IME composition and modifiers do not", () => {
    expect(consentReturnActivates(ev())).toBe(true);
    expect(consentReturnActivates(ev({ key: " " }))).toBe(false);
    expect(consentReturnActivates(ev({ isComposing: true }))).toBe(false);
    expect(consentReturnActivates(ev({ metaKey: true }))).toBe(false);
    expect(consentReturnActivates(ev({ shiftKey: true }))).toBe(false);
  });

  it("Enter belongs to buttons / links / inputs when they have focus (native button + link semantics win)", () => {
    for (const tag of ["button", "a", "input", "textarea", "select"]) {
      expect(consentReturnActivates(ev({ target: document.createElement(tag) }))).toBe(false);
    }
  });
});

describe("RecordingConsentSection · 披露块（Permissions.swift:394-443）", () => {
  it("mounts with focus on 「开启」 (autoFocus = the block's default action)", () => {
    installShell();
    renderSection();
    const turnOn = screen.getByRole("button", { name: "Turn On" });
    expect(document.activeElement).toBe(turnOn);
    expect(turnOn.className).toContain("btn-primary");
  });

  it("Enter with focus on the block itself turns recording on (setRecording on:true mode:screen), preventDefault so the wizard's Return yields", () => {
    installShell();
    const { container } = renderSection();
    const block = container.querySelector<HTMLElement>(".perm-consent-block")!;
    expect(block.tabIndex).toBe(-1);
    block.focus();
    const notPrevented = fireEvent.keyDown(block, { key: "Enter" });
    expect(notPrevented).toBe(false);
    expect(setRecordingCalls()).toEqual([[{ method: "setRecording", on: true, mode: "screen" }]]);
    expect(postMessage.mock.calls.some(([body]) => (body as { method: string }).method === "requestPermission")).toBe(false);
    // 答过了：块退场，状态行接手
    expect(container.querySelector(".perm-consent-block")).toBeNull();
    expect(container.querySelector(".perm-consent[data-state='answered']")).not.toBeNull();
  });

  it("Enter during IME composition or with a modifier does nothing", () => {
    installShell();
    const { container } = renderSection();
    const block = container.querySelector<HTMLElement>(".perm-consent-block")!;
    expect(fireEvent.keyDown(block, { key: "Enter", isComposing: true })).toBe(true);
    expect(fireEvent.keyDown(block, { key: "Enter", metaKey: true })).toBe(true);
    expect(setRecordingCalls()).toEqual([]);
    expect(container.querySelector(".perm-consent-block")).not.toBeNull();
  });

  it("Enter with focus on 「暂不」 or the privacy link is not hijacked (暂不 stays click / Tab only)", () => {
    installShell();
    const { container } = renderSection();
    const notNow = screen.getByRole("button", { name: "Not Now" });
    notNow.focus();
    expect(fireEvent.keyDown(notNow, { key: "Enter" })).toBe(true);
    const link = screen.getByRole("link", { name: "Privacy Details…" });
    link.focus();
    expect(fireEvent.keyDown(link, { key: "Enter" })).toBe(true);
    expect(setRecordingCalls()).toEqual([]);
    expect(container.querySelector(".perm-consent-block")).not.toBeNull();
    // 「暂不」的点击照旧：答过、不开
    fireEvent.click(notNow);
    expect(setRecordingCalls()).toEqual([]);
    expect(container.querySelector(".perm-consent-block")).toBeNull();
  });

  it("中文：开启 / 暂不 逐字，开启带焦点", () => {
    installShell();
    renderSection("zh");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "开启" }));
    expect(screen.getByRole("button", { name: "暂不" })).toBeTruthy();
  });
});

describe("RecordingConsentSection · 状态行三句（Permissions.swift:466-486 else-if）", () => {
  it("self_heal_note renders green (role=status) and suppresses the refusal note and the TCC-lost sentence", () => {
    installShell({ on: true, mode: "screen", engine_running: true, self_heal_note: "屏幕权限已生效，录制引擎已自动重启", note: "拒绝了这次切换", tcc_lost: true });
    const { container } = renderSection();
    const line = screen.getByRole("status");
    expect(line.className).toBe("settings-helper is-ok self-heal-note");
    expect(line.textContent).toBe("屏幕权限已生效，录制引擎已自动重启");
    expect(container.querySelectorAll(".settings-warning")).toHaveLength(0);
  });

  it("without a self-heal note the refusal note shows; without both, the TCC-lost sentence shows", () => {
    installShell({ on: true, mode: "screen", engine_running: false, self_heal_note: "", note: "拒绝了这次切换", tcc_lost: true });
    const first = renderSection();
    expect(screen.queryByRole("status")).toBeNull();
    expect(first.container.querySelector(".settings-warning")?.textContent).toBe("拒绝了这次切换");
    cleanup();

    installShell({ on: true, mode: "screen", engine_running: false, self_heal_note: "", note: "", tcc_lost: true });
    const second = renderSection();
    expect(second.container.querySelector(".settings-warning")?.textContent).toContain("macOS revoked the Screen Recording permission");
  });

  it("all three empty → no status line, no warning (the calm row)", () => {
    installShell({ on: true, mode: "screen", engine_running: true });
    const { container } = renderSection();
    expect(screen.queryByRole("status")).toBeNull();
    expect(container.querySelectorAll(".settings-warning")).toHaveLength(0);
  });
});
