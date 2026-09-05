// 语气档案「当前生效」状态行（CONTRACT §68.1 追记；原生 Settings.swift voiceStatusText）：四个状态词按 (开关, 私有在, 出厂在)；
// 开关读目录 voice_enabled 的 effective；「打开档案」= POST /api/reveal {target:"voice_profile", mode:"open"}（原生 NSWorkspace.open：
// 在默认编辑器里打开，不是访达定位），两个都不在时禁用。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSettingsCatalog, fetchVoiceProfile, postRevealTarget } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshSettingsCatalog, resetStoreForTests } from "../../store";
import type { SettingsCatalog, VoiceProfileStatus } from "../../types";
import { VoiceStatus, voiceStatusText } from "./VoiceStatus";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSettingsCatalog: vi.fn(), fetchVoiceProfile: vi.fn(), postRevealTarget: vi.fn() };
});

const status = (privateExists: boolean, defaultExists: boolean): VoiceProfileStatus => ({
  enabled: true, private_path: "/Users/demo/zai/state/voice-profile.md", private_exists: privateExists,
  default_path: "/Users/demo/zai/config/voice-profile.default.md", default_exists: defaultExists,
  effective_path: privateExists ? "/Users/demo/zai/state/voice-profile.md" : defaultExists ? "/Users/demo/zai/config/voice-profile.default.md" : null,
});
const catalog = (enabled: boolean): SettingsCatalog => ({ sections: [{ id: "voice", title: { zh: "语气档案", en: "Voice" }, help: { zh: "", en: "" },
  fields: [{ key: "voice_enabled", kind: "bool", label: { zh: "启用", en: "Voice injection" }, help: { zh: "", en: "" }, default: true, choices: null, effective: enabled, source: enabled ? "default" : "override" }] }] });
const text = (zh: string, en: string) => en;

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchVoiceProfile).mockReset();
  vi.mocked(postRevealTarget).mockReset();
});
afterEach(cleanup);

describe("VoiceStatus", () => {
  it("shows the effective profile with its ~ path and opens it (mode open) through the server target", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(true));
    await refreshSettingsCatalog();
    vi.mocked(fetchVoiceProfile).mockResolvedValue(status(true, true));
    vi.mocked(postRevealTarget).mockResolvedValue({ ok: true });
    renderEn(<VoiceStatus />);
    await screen.findByText("Your private profile");
    expect(screen.getByText("~/zai/state/voice-profile.md")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open profile" }));
    await waitFor(() => expect(postRevealTarget).toHaveBeenCalledWith("voice_profile", undefined, "open"));
  });

  it("no profile at all → nothing injected + Open disabled; switch off in the catalog → Disabled", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(true));
    await refreshSettingsCatalog();
    vi.mocked(fetchVoiceProfile).mockResolvedValue(status(false, false));
    renderEn(<VoiceStatus />);
    await screen.findByText("No profile (nothing injected)");
    expect((screen.getByRole("button", { name: "Open profile" }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(false));
    await refreshSettingsCatalog();
    vi.mocked(fetchVoiceProfile).mockResolvedValue(status(false, true));
    renderEn(<VoiceStatus />);
    await screen.findByText("Disabled");
  });

  it("status words follow the native table", () => {
    expect(voiceStatusText(status(false, true), true, text)).toBe("Shipped default (author's style)");
    expect(voiceStatusText(status(true, true), false, text)).toBe("Disabled");
  });
});
