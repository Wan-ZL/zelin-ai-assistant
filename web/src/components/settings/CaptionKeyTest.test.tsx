// 火山 BYO key 的「检测」（CONTRACT §68.2 追记 / §61.1 probeCaptionKey）：只在壳里渲染；框里有字探这个值、框空探已保存的；
// 判决经快照 captions.key_probe 推回后按 verdict 组原生 applyCaptionVerdict 的六句，ok / bad 回给凭证行做状态章；
// 老壳 UNKNOWN_METHOD → 整颗撤下；壳说 nothing to test → 先粘贴（或保存）提示。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { applyShellState, resetShellBridgeForTests, type ShellKeyProbe } from "../../shellBridge";
import { CaptionKeyTest, captionVerdictNote } from "./CaptionKeyTest";

const postMessage = vi.fn<(body: unknown) => Promise<unknown>>();
const baseState = {
  recording: { available: true, on: false, mode: "off", engine_running: false, diagnosis: null, note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen" },
  captions: { available: true, on: false, engine: "auto", paused: false, engine_dead: false, status_text: "", status_is_error: false, source: "both", translate: false, translate_direction: "auto", apple_locale: "zh", ark_model: "doubao-seed-1-6-flash", font_size: 24, opacity: 0.7, key_probe: null as ShellKeyProbe | null },
  permissions: { screen: "granted", microphone: "unknown", notifications: "unknown", vault: "unknown" },
  launch_at_login: false, hotkey: "⌃⌥Space", language: "en",
};
const probeReply = (probe: Partial<ShellKeyProbe>) => ({ ...baseState, captions: { ...baseState.captions, key_probe: { name: "volcano-ark-key.txt", state: "done", verdict: "ok", detail: "", code: "", message: "", ...probe } } });
const text = (zh: string, en: string) => en;

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

beforeEach(() => {
  resetShellBridgeForTests();
  postMessage.mockReset();
  window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
  applyShellState(baseState);
});
afterEach(() => {
  cleanup();
  delete window.webkit;
});

describe("CaptionKeyTest", () => {
  it("is absent without the shell bridge", () => {
    delete window.webkit;
    renderEn(<CaptionKeyTest name="volcano-ark-key.txt" value="" onVerdict={() => undefined} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("probes the pasted value, shows the in-flight sentence, then the ✅ verdict and reports ok", async () => {
    postMessage.mockResolvedValue(probeReply({ verdict: "ok" }));
    const onVerdict = vi.fn();
    renderEn(<CaptionKeyTest name="volcano-ark-key.txt" value=" ark-KEY " onVerdict={onVerdict} />);
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(screen.getByRole("button").textContent).toBe("Testing…");
    expect(screen.getByRole("status").textContent).toBe("Testing (one real server connection)…");
    await screen.findByText("✅ Valid (connected)");
    expect(postMessage).toHaveBeenCalledWith({ method: "probeCaptionKey", name: "volcano-ark-key.txt", value: "ark-KEY" });
    expect(onVerdict).toHaveBeenCalledWith(true);
    expect(screen.getByRole("button").textContent).toBe("Test");
  });

  it("with an empty box probes the stored secret; a bad key is an alert and reports false", async () => {
    postMessage.mockResolvedValue(probeReply({ verdict: "bad_key", detail: "HTTP 401" }));
    const onVerdict = vi.fn();
    renderEn(<CaptionKeyTest name="volcano-ark-key.txt" value="" onVerdict={onVerdict} />);
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await screen.findByText("❌ Key invalid or service not activated (HTTP 401)");
    expect(postMessage).toHaveBeenCalledWith({ method: "probeCaptionKey", name: "volcano-ark-key.txt" });
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(onVerdict).toHaveBeenCalledWith(false);
  });

  it("the verdict may arrive later via the zai-shell-state push (running first)", async () => {
    postMessage.mockResolvedValue(probeReply({ state: "running", verdict: "" }));
    const onVerdict = vi.fn();
    renderEn(<CaptionKeyTest name="volcano-ark-key.txt" value="" onVerdict={onVerdict} />);
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await Promise.resolve();
    expect(screen.getByRole("button").textContent).toBe("Testing…");
    act(() => { applyShellState(probeReply({ verdict: "network", detail: "timed out" })); });
    await screen.findByText("⚠️ Network unreachable (timed out) — click Test again later");
    expect(onVerdict).toHaveBeenCalledWith(null);   // 网络判决不指向 key 本身：状态章不动
  });

  it("ignores verdicts for the other key and handles old shells / nothing-to-test", async () => {
    postMessage.mockResolvedValue(probeReply({ name: "volcano-speech-key.txt", verdict: "ok" }));
    renderEn(<CaptionKeyTest name="volcano-ark-key.txt" value="" onVerdict={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await Promise.resolve();
    expect(screen.getByRole("button").textContent).toBe("Testing…");   // 别行的判决不算
    cleanup();
    postMessage.mockRejectedValue(new Error("INVALID_ARGS: nothing to test: paste or save the credential first"));
    renderEn(<CaptionKeyTest name="volcano-speech-key.txt" value="" onVerdict={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await screen.findByText("Paste (or save) a credential first");
    cleanup();
    postMessage.mockRejectedValue(new Error("UNKNOWN_METHOD: probeCaptionKey"));
    renderEn(<CaptionKeyTest name="volcano-speech-key.txt" value="" onVerdict={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await vi.waitFor(() => expect(screen.queryByRole("button")).toBeNull());
  });

  it("composes the six native sentences", () => {
    const base: ShellKeyProbe = { name: "x", state: "done", verdict: "", detail: "d", code: "c", message: "m" };
    expect(captionVerdictNote({ ...base, verdict: "resource_not_enabled" }, text)).toEqual({ ok: false, message: "❌ Resource not activated (c: m) — enable streaming ASR in the speech console" });
    expect(captionVerdictNote({ ...base, verdict: "model_not_found" }, text)).toEqual({ ok: null, message: "❌ Model ID not found or not opened (d) — check the translation-model field" });
    expect(captionVerdictNote({ ...base, verdict: "service_error", message: "" }, text)).toEqual({ ok: null, message: "❌ Service error c" });
    expect(captionVerdictNote({ ...base, verdict: "service_error" }, text).message).toBe("❌ Service error c: m");
  });
});
