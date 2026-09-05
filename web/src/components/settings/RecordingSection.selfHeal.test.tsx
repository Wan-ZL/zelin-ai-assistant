// 设置 → 录制区的两句原生说明（CONTRACT §68.3 追记，parity 批 `recording-consent-header-ui`；原生 Settings.swift:709-721）：
//   1) 三档单选下面固定一句「打开 App 时自动按此模式启动 Screenpipe 持续录制。」（次要色 settings-helper）；
//   2) consent-race 自愈成功句（self_heal_note）以绿色 ✓ 行出现在引擎行之前，与拒绝说明（note，拼在引擎行里）并存——
//      原生是两个并列的 if，不是 else-if；空则不渲；
//   3) 浏览器（无桥）里两句都不出——整块退成「只在看板 app 里可控」。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { LanguageContext } from "../../i18n";
import { applyShellState, resetShellBridgeForTests, type ShellState } from "../../shellBridge";
import { RecordingSection } from "./RecordingSection";

const base: ShellState = {
  recording: { available: true, on: true, mode: "screen", engine_running: true, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen", self_heal_note: "", log_tail: "" },
  captions: { available: true, on: false, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false, source: "both", translate: false, translate_direction: "auto", apple_locale: "zh", ark_model: "", font_size: 24, opacity: 0.7 },
  permissions: { screen: "granted", microphone: "unknown", notifications: "unknown", vault: "unknown" },
  launch_at_login: false, hotkey: "⌃⌥Space",
};

function installShell(recording: Partial<ShellState["recording"]> = {}) {
  const state: ShellState = { ...base, recording: { ...base.recording, ...recording } };
  (window as Window & { webkit?: unknown }).webkit = { messageHandlers: { zaiShell: { postMessage: async () => state } } };
  applyShellState(state);
}

const renderSection = (language: "zh" | "en" = "en") =>
  render(<LanguageContext.Provider value={language}><RecordingSection /></LanguageContext.Provider>);

const AUTOSTART_ZH = "打开 App 时自动按此模式启动 Screenpipe 持续录制。";
const AUTOSTART_EN = "On app launch, Screenpipe recording starts automatically in this mode.";

beforeEach(() => {
  resetShellBridgeForTests();
});

afterEach(() => {
  cleanup();
  delete (window as Window & { webkit?: unknown }).webkit;
});

describe("RecordingSection · 自动启动说明句", () => {
  it("renders the autostart sentence right under the mode radios, in both languages", () => {
    installShell();
    const { container } = renderSection("zh");
    const radios = container.querySelector(".settings-radio-row")!;
    const next = radios.nextElementSibling!;
    expect(next.className).toBe("settings-helper");
    expect(next.textContent).toBe(AUTOSTART_ZH);
    cleanup();
    installShell();
    renderSection("en");
    expect(screen.getByText(AUTOSTART_EN)).toBeTruthy();
  });

  it("no bridge → neither sentence (the whole block is the honest browser note)", () => {
    const { container } = renderSection();
    expect(screen.queryByText(AUTOSTART_EN)).toBeNull();
    expect(container.querySelector(".self-heal-note")).toBeNull();
    expect(container.querySelector(".settings-warning")?.textContent).toContain("only controllable inside the board app");
  });
});

describe("RecordingSection · consent-race 自愈成功句（Settings.swift:713-721）", () => {
  it("self_heal_note renders as a green ✓ status line between the autostart sentence and the engine line, alongside the refusal note", () => {
    installShell({ self_heal_note: "屏幕权限已生效，录制引擎已自动重启", note: "拒绝了这次切换" });
    const { container } = renderSection();
    const line = screen.getByRole("status");
    expect(line.className).toBe("settings-helper is-ok self-heal-note");
    expect(line.textContent).toBe("✓ 屏幕权限已生效，录制引擎已自动重启");
    expect(line.previousElementSibling?.textContent).toBe(AUTOSTART_EN);
    const engine = line.nextElementSibling!;
    expect(engine.textContent).toContain("Engine: ");
    expect(engine.textContent).toContain("拒绝了这次切换"); // 两句并存，不是 else-if
    expect(container.querySelectorAll(".self-heal-note")).toHaveLength(1);
  });

  it("empty self_heal_note renders no status line", () => {
    installShell({ self_heal_note: "" });
    const { container } = renderSection();
    expect(screen.queryByRole("status")).toBeNull();
    expect(container.querySelector(".self-heal-note")).toBeNull();
  });
});
