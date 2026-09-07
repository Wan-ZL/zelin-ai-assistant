// 向导终章「登录时自动启动」一行（决策 D39，CONTRACT §28 追记）：默认勾选；只在壳报 launch_at_login_available 时可用；
// 浏览器（无桥）/ 开发版 / 快照未到 → 禁用 + 原因句；applyLaunchAtLoginChoice 是 diff-write（与壳真相一致不打桥），
// 走的是 设置 → 关于 同一条桥方法 `setLaunchAtLogin {on}`，拒绝 → 原生 loginItemAlert 同款「标题: 壳原句」。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { applyShellState, resetShellBridgeForTests, type ShellState } from "../../shellBridge";
import { applyLaunchAtLoginChoice, LaunchAtLoginChoice, launchAtLoginOffer } from "./LaunchAtLoginChoice";

const postMessage = vi.fn<(body: unknown) => Promise<unknown>>();
const text = (zh: string, en: string) => en;

function state(over: Partial<ShellState> = {}): ShellState {
  return {
    recording: { available: true, on: false, mode: "off", engine_running: false, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen" },
    captions: { available: true, on: false, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false, source: "both", translate: false, translate_direction: "auto", apple_locale: "zh", ark_model: "m", font_size: 24, opacity: 0.7 },
    permissions: { screen: "granted", microphone: "unknown", notifications: "unknown", vault: "unknown" },
    launch_at_login: false,
    launch_at_login_available: true,
    hotkey: "⌃⌥Space",
    language: "en",
    ...over,
  };
}

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

function installShell() {
  window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
}

beforeEach(() => {
  resetShellBridgeForTests();
  postMessage.mockReset();
});
afterEach(() => {
  cleanup();
  delete window.webkit;
});

describe("launchAtLoginOffer", () => {
  it("browser (no bridge) → unavailable, reason names the app's Settings → About toggle", () => {
    const offer = launchAtLoginOffer(false, null, text);
    expect(offer.available).toBe(false);
    expect(offer.reason).toContain("browser");
    expect(offer.reason).toContain("Settings → About");
  });

  it("shell present but no snapshot yet → unavailable (waiting), never a guess", () => {
    const offer = launchAtLoginOffer(true, null, text);
    expect(offer.available).toBe(false);
    expect(offer.reason).toContain("Reading");
  });

  it("shell says not an installed bundle (dev build / old shell without the key) → unavailable, dev-build reason", () => {
    expect(launchAtLoginOffer(true, state({ launch_at_login_available: false }), text)).toEqual(expect.objectContaining({ available: false }));
    expect(launchAtLoginOffer(true, state({ launch_at_login_available: false }), text).reason).toContain("/Applications");
    const old = { ...state(), launch_at_login_available: undefined };
    expect(launchAtLoginOffer(true, old, text).available).toBe(false);
  });

  it("installed shell → available, no reason", () => {
    expect(launchAtLoginOffer(true, state(), text)).toEqual({ available: true, reason: null });
  });
});

describe("LaunchAtLoginChoice", () => {
  it("default checked and enabled in an installed shell; unchecking reports through onChange", () => {
    installShell();
    const shell = applyShellState(state());
    const onChange = vi.fn();
    renderEn(<LaunchAtLoginChoice checked onChange={onChange} shell={shell} />);
    const box = screen.getByRole("checkbox", { name: /Launch at login/ }) as HTMLInputElement;
    expect(box.checked).toBe(true);
    expect(box.disabled).toBe(false);
    expect(screen.queryByTestId("setup-launch-at-login-reason")).toBeNull();
    fireEvent.click(box);
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("browser: disabled, shown unchecked even though the choice defaults to true, with the reason", () => {
    renderEn(<LaunchAtLoginChoice checked onChange={() => undefined} shell={null} />);
    const box = screen.getByRole("checkbox", { name: /Launch at login/ }) as HTMLInputElement;
    expect(box.disabled).toBe(true);
    expect(box.checked).toBe(false);
    expect(screen.getByTestId("setup-launch-at-login-reason").textContent).toContain("browser");
  });

  it("dev build (shell present, launch_at_login_available false): disabled with the /Applications reason", () => {
    installShell();
    const shell = applyShellState(state({ launch_at_login_available: false }));
    renderEn(<LaunchAtLoginChoice checked onChange={() => undefined} shell={shell} />);
    expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByTestId("setup-launch-at-login-reason").textContent).toContain("/Applications");
  });

  it("renders the Chinese copy with the reboot rationale", () => {
    installShell();
    const shell = applyShellState(state());
    render(<LanguageContext.Provider value="zh"><LaunchAtLoginChoice checked onChange={() => undefined} shell={shell} /></LanguageContext.Provider>);
    expect(screen.getByText("登录时自动启动（录制才能在重启后恢复）")).toBeTruthy();
  });
});

describe("applyLaunchAtLoginChoice (diff-write through setLaunchAtLogin)", () => {
  it("checked and the shell already has it on → no bridge call", async () => {
    installShell();
    expect(await applyLaunchAtLoginChoice(true, state({ launch_at_login: true }), text)).toBeNull();
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("unchecked and the shell has it off → no bridge call (never unregisters what was never on)", async () => {
    installShell();
    expect(await applyLaunchAtLoginChoice(false, state({ launch_at_login: false }), text)).toBeNull();
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("checked and the shell has it off → setLaunchAtLogin {on:true}, same method as Settings → About", async () => {
    installShell();
    postMessage.mockResolvedValue(state({ launch_at_login: true }));
    expect(await applyLaunchAtLoginChoice(true, state({ launch_at_login: false }), text)).toBeNull();
    expect(postMessage).toHaveBeenCalledTimes(1);
    expect(postMessage).toHaveBeenCalledWith({ method: "setLaunchAtLogin", on: true });
  });

  it("unchecked while the shell has it on (re-running the wizard) → setLaunchAtLogin {on:false}", async () => {
    installShell();
    postMessage.mockResolvedValue(state({ launch_at_login: false }));
    expect(await applyLaunchAtLoginChoice(false, state({ launch_at_login: true }), text)).toBeNull();
    expect(postMessage).toHaveBeenCalledWith({ method: "setLaunchAtLogin", on: false });
  });

  it("shell rejection → native alert title + the shell's own sentence (bridge prefix stripped)", async () => {
    installShell();
    postMessage.mockRejectedValue(new Error("INVALID_ARGS: launch at login: SMAppService: Operation not permitted"));
    const err = await applyLaunchAtLoginChoice(true, state({ launch_at_login: false }), text);
    expect(err).toBe("Failed to enable launch at login: SMAppService: Operation not permitted");
    postMessage.mockRejectedValue(new Error("INVALID_ARGS: launch at login: not an app bundle"));
    expect(await applyLaunchAtLoginChoice(true, state({ launch_at_login: false }), text)).toBe("Can't enable launch at login: not an app bundle");
  });
});
