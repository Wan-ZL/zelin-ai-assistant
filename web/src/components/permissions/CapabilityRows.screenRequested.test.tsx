// 屏幕录制行按钮的两个词（CONTRACT §68.3 追记，parity 批 `recording-consent-header-ui`；原生 Permissions.swift:548-549
// `Prefs.bool("screenPermissionRequested") ? 打开系统设置 : 去授权`）：一次性系统提示还没弹过 → 「去授权」；弹过（壳快照
// `permissions.screen_requested`）→ 「打开系统设置」——按钮说的与壳接下来做的（深链）一致。已授权只剩 ✓，别的行不受这把键影响；
// 老壳没有这把键（normalize 补 false）→ 照旧「去授权」。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { LanguageContext } from "../../i18n";
import { applyShellState, resetShellBridgeForTests, type ShellState } from "../../shellBridge";
import { resetStoreForTests } from "../../store";
import { CapabilityRows, capabilityButtonLabel } from "./CapabilityRows";

const text = (zh: string, en: string) => en;

const base: ShellState = {
  recording: { available: true, on: false, mode: "off", engine_running: false, diagnosis: null, note: "", tcc_lost: false, screen_permission: false, resume_mode: "screen" },
  captions: { available: true, on: false, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false, source: "both", translate: false, translate_direction: "auto", apple_locale: "zh", ark_model: "", font_size: 24, opacity: 0.7 },
  permissions: { screen: "denied", microphone: "unknown", notifications: "unknown", vault: "unknown" },
  launch_at_login: false, hotkey: "⌃⌥Space",
};

function installShell(permissions: Partial<ShellState["permissions"]> = {}) {
  const state: ShellState = { ...base, permissions: { ...base.permissions, ...permissions } };
  (window as Window & { webkit?: unknown }).webkit = { messageHandlers: { zaiShell: { postMessage: async () => state } } };
  applyShellState(state);
}

const renderRows = (language: "zh" | "en" = "en") =>
  render(<LanguageContext.Provider value={language}><CapabilityRows /></LanguageContext.Provider>);

const screenRow = () => document.querySelector<HTMLElement>(".perm-capability[data-kind='screen']")!;

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
});

afterEach(() => {
  cleanup();
  delete (window as Window & { webkit?: unknown }).webkit;
});

describe("capabilityButtonLabel（原生 buttonLabel）", () => {
  it("screen: 去授权 until the one-shot prompt has been used, then 打开系统设置", () => {
    expect(capabilityButtonLabel("screen", "denied", text)).toBe("Grant…");
    expect(capabilityButtonLabel("screen", "denied", text, false)).toBe("Grant…");
    expect(capabilityButtonLabel("screen", "denied", text, true)).toBe("Open System Settings");
    expect(capabilityButtonLabel("screen", "unknown", text, true)).toBe("Open System Settings");
  });

  it("the flag is screen-only: vault / notifications / microphone keep their own rules", () => {
    expect(capabilityButtonLabel("vault", "unknown", text, true)).toBe("Grant…");
    expect(capabilityButtonLabel("vault", "denied", text, true)).toBe("Open System Settings");
    expect(capabilityButtonLabel("notifications", "unknown", text, true)).toBe("Request…");
    expect(capabilityButtonLabel("microphone", "denied", text, true)).toBe("Open System Settings");
  });
});

describe("CapabilityRows · 屏幕录制行", () => {
  it("screen_requested:false (or absent on an older shell) → 「去授权」", () => {
    installShell();
    renderRows();
    expect(screenRow().querySelector("button")?.textContent).toBe("Grant…");
  });

  it("screen_requested:true → 「打开系统设置」 in both languages; other rows unchanged", () => {
    installShell({ screen_requested: true });
    renderRows("zh");
    expect(screenRow().querySelector("button")?.textContent).toBe("打开系统设置");
    expect(document.querySelector(".perm-capability[data-kind='vault'] button")?.textContent).toBe("去授权");
    expect(document.querySelector(".perm-capability[data-kind='notifications'] button")?.textContent).toBe("请求权限");
    cleanup();
    installShell({ screen_requested: true });
    renderRows("en");
    expect(screenRow().querySelector("button")?.textContent).toBe("Open System Settings");
  });

  it("granted screen shows only ✓ regardless of the flag", () => {
    installShell({ screen: "granted", screen_requested: true });
    renderRows();
    expect(screenRow().querySelector("button")).toBeNull();
    expect(screen.getAllByLabelText("Granted").length).toBeGreaterThan(0);
  });
});
