// 设置 → 实时字幕区的原生说明句（CONTRACT §68.2 追记，parity 批 `captions-settings-notes`；原生 SettingsLiveCaptions.swift）：
//   1) 状态行按 CaptionDisplayState.statusLine 的先后：已暂停（+「在悬浮窗上点 ▶ 继续」）压过引擎状态 / 错误；
//      暂停时壳的 status_text 是 ""，行不能消失；非暂停按 status_text / status_is_error；都没有不出行；
//   2) source_note 橙色挂在「声音来源」下、暂停时不出；translation_note 次要色挂在翻译开关下、与 translation_active 无关；
//   3) 引擎脚注两支按 apple_engine_available 选句；老壳缺键 → normalize 补 false → 「没有 Apple 本地识别可用」那支，两句 note 不出；
//   4) 其余脚注：豆包中英混识 / Ark 模型 placeholder / 两种凭证格式 / 两个控制台 + 只存本机 + 检测 / 费用与「字幕文本永不离开这台 Mac」；
//   5) 浏览器（无桥）里只剩凭证行的脚注 + 费用句，引擎相关的行与 note 一个不出。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { LanguageContext } from "../../i18n";
import { applyShellState, resetShellBridgeForTests, type ShellState } from "../../shellBridge";
import { CaptionsSection, captionsStatusLine, engineFootnote } from "./CaptionsSection";

const base: ShellState = {
  recording: { available: true, on: false, mode: "off", engine_running: false, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen", self_heal_note: "", log_tail: "" },
  captions: {
    available: true, on: true, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false,
    source: "both", translate: false, translate_direction: "auto", apple_locale: "zh", ark_model: "doubao-seed-1-6-flash", font_size: 24, opacity: 0.7,
    key_probe: null, translation_note: "", translation_active: false, source_note: "", apple_engine_available: true,
  },
  permissions: { screen: "granted", microphone: "unknown", notifications: "unknown", vault: "unknown", screen_requested: false },
  launch_at_login: false, hotkey: "⌃⌥Space",
};

/** 装一个假壳并落快照；raw 给了就原样喂 normalize（老壳缺键的判例用） */
function installShell(captions: Partial<ShellState["captions"]> = {}, raw?: unknown) {
  const state: ShellState = { ...base, captions: { ...base.captions, ...captions } };
  const snapshot = raw ?? state;
  window.webkit = { messageHandlers: { zaiShell: { postMessage: async () => snapshot } } };
  applyShellState(snapshot);
}

const renderSection = (language: "zh" | "en" = "en") =>
  render(<LanguageContext.Provider value={language}><CaptionsSection /></LanguageContext.Provider>);

const text = (zh: string, en: string) => en;
const PAUSED_EN = "Paused — nothing is captured or billed — click ▶ on the overlay to resume";
const PAUSED_ZH = "已暂停 — 未在采集，也不计费；在悬浮窗上点 ▶ 继续";
const APPLE_OK_EN = "Auto = Doubao when a key is saved (better zh/en mixing and punctuation), otherwise Apple on-device (free, offline).";
const APPLE_NO_EN = "Auto = Doubao when a key is saved. This Mac is below macOS 26, so Apple on-device recognition is unavailable.";

/** 某个 select 所在的 .settings-field（脚注 / note 的位置断言用） */
function fieldOf(container: HTMLElement, id: string): Element {
  return container.querySelector(`#${id}`)!.closest(".settings-field")!;
}

beforeEach(() => {
  resetShellBridgeForTests();
});

afterEach(() => {
  cleanup();
  delete window.webkit;
});

describe("captionsStatusLine · CaptionDisplayState.statusLine 的镜像（review G）", () => {
  it("paused wins over an empty status (the shell clears status_text on pause) and over an engine error", () => {
    expect(captionsStatusLine({ paused: true, status_text: "", status_is_error: false }, text))
      .toEqual({ parts: ["Paused — nothing is captured or billed", " — click ▶ on the overlay to resume"], isError: false });
    expect(captionsStatusLine({ paused: true, status_text: "doubao: 401", status_is_error: true }, text)?.isError).toBe(false);
    expect(captionsStatusLine({ paused: true, status_text: "doubao: 401", status_is_error: true }, text)?.parts.join("")).toBe(PAUSED_EN);
  });

  it("not paused → the engine status with its error flag; nothing to say → null (listening normally)", () => {
    expect(captionsStatusLine({ paused: false, status_text: "Connecting to the recognizer…", status_is_error: false }, text))
      .toEqual({ parts: ["Connecting to the recognizer…"], isError: false });
    expect(captionsStatusLine({ paused: false, status_text: "doubao: 401", status_is_error: true }, text))
      .toEqual({ parts: ["doubao: 401"], isError: true });
    expect(captionsStatusLine({ paused: false, status_text: "", status_is_error: false }, text)).toBeNull();
  });
});

describe("engineFootnote · SettingsLiveCaptions.engineFootnote 的两支", () => {
  it("picks the Apple-available sentence or the below-macOS-26 sentence", () => {
    expect(engineFootnote(true, text)).toBe(APPLE_OK_EN);
    expect(engineFootnote(false, text)).toBe(APPLE_NO_EN);
    expect(engineFootnote(false, (zh) => zh)).toBe("自动 = 有豆包 Key 就用豆包。这台 Mac 低于 macOS 26，没有 Apple 本地识别可用。");
  });
});

describe("CaptionsSection · 状态行先后", () => {
  it("paused with an empty status_text still shows the paused label (secondary, not warning) as two adjacent nodes", () => {
    installShell({ paused: true, status_text: "" });
    const { container } = renderSection("zh");
    const row = container.querySelector(".captions-status")!;
    expect(row.className).toBe("settings-helper captions-status");
    expect(row.getAttribute("data-paused")).toBe("true");
    expect(row.textContent).toBe(PAUSED_ZH);
    expect(row.querySelectorAll("span")).toHaveLength(2); // 原生 pausedLabel + L("；在悬浮窗上点 ▶ 继续") 两条清单标签
    expect(row.closest(".settings-field")!.querySelector("#captions-on")).not.toBeNull(); // 挂在开关那一行
  });

  it("paused outranks an engine error: the error text is not shown while paused", () => {
    installShell({ paused: true, status_text: "doubao: 401", status_is_error: true });
    const { container } = renderSection();
    const row = container.querySelector(".captions-status")!;
    expect(row.textContent).toBe(PAUSED_EN);
    expect(row.className).not.toContain("is-warning");
    expect(screen.queryByText("doubao: 401")).toBeNull();
  });

  it("not paused: status_text renders as before (warning when status_is_error), nothing when empty", () => {
    installShell({ paused: false, status_text: "doubao: 401", status_is_error: true });
    let { container } = renderSection();
    let row = container.querySelector(".captions-status")!;
    expect(row.textContent).toBe("doubao: 401");
    expect(row.className).toBe("settings-helper captions-status is-warning");
    expect(row.getAttribute("data-paused")).toBeNull();
    cleanup();
    installShell({ paused: false, status_text: "Connecting to the recognizer…", status_is_error: false });
    ({ container } = renderSection());
    row = container.querySelector(".captions-status")!;
    expect(row.className).toBe("settings-helper captions-status");
    cleanup();
    installShell({ paused: false, status_text: "" });
    ({ container } = renderSection());
    expect(container.querySelector(".captions-status")).toBeNull();
  });
});

describe("CaptionsSection · source_note / translation_note", () => {
  it("source_note renders orange right under the audio-source select, and is hidden while paused", () => {
    installShell({ source_note: "缺屏幕录制权限，听不到系统声音；先只听麦克风", paused: false });
    const { container } = renderSection();
    const note = container.querySelector(".captions-source-note")!;
    expect(note.className).toBe("settings-helper is-warning captions-source-note");
    expect(note.textContent).toBe("缺屏幕录制权限，听不到系统声音；先只听麦克风");
    expect(fieldOf(container, "captions-source").nextElementSibling).toBe(note);
    cleanup();
    installShell({ source_note: "缺屏幕录制权限，听不到系统声音；先只听麦克风", paused: true });
    expect(renderSection().container.querySelector(".captions-source-note")).toBeNull();
  });

  it("translation_note renders (secondary) right under the translate switch, whatever translation_active says", () => {
    const note = "还没有 Ark API Key——翻译要单独的 Ark 控制台 Key（和语音 Key 不是同一个）";
    installShell({ translate: true, translation_note: note, translation_active: false });
    let { container } = renderSection();
    let el = container.querySelector(".captions-translation-note")!;
    expect(el.className).toBe("settings-helper captions-translation-note");
    expect(el.textContent).toBe(note);
    expect(fieldOf(container, "captions-translate").nextElementSibling).toBe(el);
    cleanup();
    // Ark 途中报错：translation_active 仍 true，note 照样出（原生同款——note 不是活性信号）
    installShell({ translate: true, translation_note: "Ark API Key 无效——翻译暂停", translation_active: true });
    ({ container } = renderSection());
    el = container.querySelector(".captions-translation-note")!;
    expect(el.textContent).toBe("Ark API Key 无效——翻译暂停");
  });

  it("empty notes render nothing", () => {
    installShell({ source_note: "", translation_note: "", translate: true, translation_active: true });
    const { container } = renderSection();
    expect(container.querySelector(".captions-source-note")).toBeNull();
    expect(container.querySelector(".captions-translation-note")).toBeNull();
  });
});

describe("CaptionsSection · 引擎脚注两支", () => {
  it("apple_engine_available:true → the Apple-available sentence right under the engine select", () => {
    installShell({ apple_engine_available: true });
    const { container } = renderSection();
    const note = container.querySelector(".captions-engine-footnote")!;
    expect(note.textContent).toBe(APPLE_OK_EN);
    expect(fieldOf(container, "captions-engine").nextElementSibling).toBe(note);
  });

  it("apple_engine_available:false → the below-macOS-26 sentence, in both languages", () => {
    installShell({ apple_engine_available: false });
    expect(renderSection().container.querySelector(".captions-engine-footnote")!.textContent).toBe(APPLE_NO_EN);
    cleanup();
    installShell({ apple_engine_available: false });
    expect(renderSection("zh").container.querySelector(".captions-engine-footnote")!.textContent)
      .toBe("自动 = 有豆包 Key 就用豆包。这台 Mac 低于 macOS 26，没有 Apple 本地识别可用。");
  });

  it("old shell without the §61.1 keys: no notes; the footnote takes normalize's false default (no Apple engine)", () => {
    const { translation_note: _tn, translation_active: _ta, source_note: _sn, apple_engine_available: _ae, key_probe: _kp, ...oldCaptions } = base.captions;
    installShell({}, { ...base, captions: { ...oldCaptions, paused: false, status_text: "" } });
    const { container } = renderSection();
    expect(container.querySelector(".captions-status")).toBeNull();
    expect(container.querySelector(".captions-source-note")).toBeNull();
    expect(container.querySelector(".captions-translation-note")).toBeNull();
    expect(container.querySelector(".captions-engine-footnote")!.textContent).toBe(APPLE_NO_EN);
  });
});

describe("CaptionsSection · 其余脚注（copy，事实照原生）", () => {
  it("Doubao code-switch note under the on-device-language select; Ark model input carries the native placeholder", () => {
    installShell();
    const { container } = renderSection();
    const localeField = fieldOf(container, "captions-apple_locale");
    expect(localeField.nextElementSibling!.textContent).toBe("The Doubao engine code-switches zh/en automatically — no choice needed.");
    expect(localeField.nextElementSibling!.className).toBe("settings-helper");
    expect((container.querySelector("#captions-ark-model") as HTMLInputElement).placeholder).toBe("doubao-seed-1-6-flash");
  });

  it("credential helpers carry the two speech-key formats and the two-consoles / local-only / Test facts; costs paragraph closes the section", () => {
    installShell();
    const { container } = renderSection("zh");
    const speech = container.querySelector('[data-secret="volcano-speech-key.txt"] .settings-helper')!.textContent!;
    expect(speech).toContain("支持两种");
    expect(speech).toContain("\"AppID:Token\"");
    expect(speech).toContain("\"App ID:\"");
    const ark = container.querySelector('[data-secret="volcano-ark-key.txt"] .settings-helper')!.textContent!;
    expect(ark).toContain("两个不同控制台");
    expect(ark).toContain("只存本机 config/secrets/");
    expect(ark).toContain("保存只存本机、不联网");
    expect(ark).toContain("点「检测」才真连一次");
    const costs = container.querySelector(".captions-costs")!;
    expect(costs.textContent).toContain("¥1/小时");
    expect(costs.textContent).toContain("20 小时");
    expect(costs.textContent).toContain("50 万 token");
    expect(costs.textContent).toContain("需 macOS 26+");
    expect(costs.textContent).toContain("字幕文本永不离开这台 Mac");
    expect(container.querySelector("section")!.lastElementChild).toBe(costs);
    cleanup();
    installShell();
    const en = renderSection("en").container;
    expect(en.querySelector(".captions-costs")!.textContent).toContain("Caption text never leaves this Mac");
    expect(en.querySelector('[data-secret="volcano-speech-key.txt"] .settings-helper')!.textContent).toContain("Two formats work");
    expect(en.querySelector('[data-secret="volcano-ark-key.txt"] .settings-helper')!.textContent).toContain("two different Volcano consoles");
  });

  it("no bridge → engine rows and notes are absent; credential footnotes and the costs paragraph stay", () => {
    const { container } = renderSection();
    expect(container.querySelector(".settings-warning")!.textContent).toContain("only controllable inside the board app");
    for (const cls of [".captions-status", ".captions-engine-footnote", ".captions-source-note", ".captions-translation-note"]) {
      expect(container.querySelector(cls)).toBeNull();
    }
    expect(screen.queryByText(/code-switches zh\/en automatically/)).toBeNull();
    expect(container.querySelector('[data-secret="volcano-speech-key.txt"] .settings-helper')!.textContent).toContain("Two formats work");
    expect(container.querySelector(".captions-costs")).not.toBeNull();
  });
});
