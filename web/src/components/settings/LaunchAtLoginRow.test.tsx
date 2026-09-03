// 「登录时启动」一行（CONTRACT §68.6 / §68.13；原生 Settings.setLaunchAtLogin + loginItemAlert）：只在壳里渲染；
// 桥拒绝 → 弹窗标题三选一（not an app bundle → 无法开启登录时启动；开失败；关失败）+ 壳原句 + 「好」关掉。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { applyShellState, resetShellBridgeForTests } from "../../shellBridge";
import { LaunchAtLoginRow, launchAtLoginAlertTitle } from "./LaunchAtLoginRow";

const postMessage = vi.fn<(body: unknown) => Promise<unknown>>();
const state = (launch: boolean) => ({
  recording: { available: true, on: false, mode: "off", engine_running: false, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen" },
  captions: { available: true, on: false, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false },
  permissions: { screen: "granted", microphone: "unknown", notifications: "unknown", vault: "unknown" },
  launch_at_login: launch, hotkey: "⌃⌥Space", language: "en",
});
const text = (zh: string, en: string) => en;

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

beforeEach(() => {
  resetShellBridgeForTests();
  postMessage.mockReset();
  window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
  }
});
afterEach(() => {
  cleanup();
  delete window.webkit;
});

describe("LaunchAtLoginRow", () => {
  it("renders nothing without the bridge / state", () => {
    delete window.webkit;
    renderEn(<LaunchAtLoginRow />);
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("toggles through the bridge and mirrors the shell truth", async () => {
    applyShellState(state(false));
    postMessage.mockResolvedValue(state(true));
    renderEn(<LaunchAtLoginRow />);
    const toggle = screen.getByRole("switch", { name: "Launch at login" }) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    fireEvent.click(toggle);
    expect(postMessage).toHaveBeenCalledWith({ method: "setLaunchAtLogin", on: true });
    await vi.waitFor(() => expect((screen.getByRole("switch") as HTMLInputElement).checked).toBe(true));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("a shell rejection opens the native alert: dev build title, shell sentence, OK closes it", async () => {
    applyShellState(state(false));
    postMessage.mockRejectedValue(new Error("INVALID_ARGS: launch at login: not an app bundle"));
    renderEn(<LaunchAtLoginRow />);
    fireEvent.click(screen.getByRole("switch"));
    await screen.findByText("Can't enable launch at login");
    expect(screen.getByText("not an app bundle")).toBeTruthy();   // 桥前缀剥掉、壳原句留下
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(screen.queryByText("Can't enable launch at login")).toBeNull();
  });

  it("titles: enable failure vs disable failure", () => {
    expect(launchAtLoginAlertTitle(true, "SMAppService: Operation not permitted", text)).toBe("Failed to enable launch at login");
    expect(launchAtLoginAlertTitle(true, "not an app bundle", text)).toBe("Can't enable launch at login");
    expect(launchAtLoginAlertTitle(false, "anything", text)).toBe("Failed to disable launch at login");
  });
});
