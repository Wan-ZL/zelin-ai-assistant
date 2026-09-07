// 向导第 1 步「界面语言」单选写回 server（CONTRACT §15 追记 2026-09-06，D37；§68.5）：点一下 = store.chooseLanguage——UI 立刻切
// （不等 PUT）+ PUT /api/settings/general {language}，与顶栏切换 / `/lang` / 设置区「保存」同一把开关（此前这里是唯一的双写点，
// 现在住 store）；PUT 失败不影响本次切换；URL 上一次性的 ?lang= 随手摘掉。四个写者的第四条判例——另三条见 LanguageToggle.persist /
// composerCommands.lang / CatalogSection.languageSave。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchHealth, fetchPermissions, fetchSecrets, fetchSetup, fetchSetupEngine, putSettingsSection } from "../api";
import { LanguageContext } from "../i18n";
import { getState, resetStoreForTests, setLanguage } from "../store";
import type { PermissionsSnapshot, SetupSnapshot } from "../types";
import { SetupPage } from "./SetupPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    fetchHealth: vi.fn(), fetchPermissions: vi.fn(), fetchSetup: vi.fn(), fetchSecrets: vi.fn(), fetchSetupEngine: vi.fn(),
    putSettingsSection: vi.fn(), fetchSettingsCatalog: vi.fn().mockResolvedValue({ sections: [] }),
  };
});

const receipt = (language: string) => ({
  id: "general", title: { zh: "通用", en: "General" }, help: { zh: "", en: "" },
  fields: [{ key: "language", kind: "enum", label: { zh: "界面语言", en: "Interface language" }, help: { zh: "", en: "" },
    default: "zh", choices: ["zh", "en"], effective: language, source: "override" }],
});

const HEALTH_OK = { verdict: "ok", heartbeat: { age_s: 3, phase: "dashboard", pid: 1, interval: 10, stale_after_s: 90, stale: false }, dashboard: { generated_at: "2026-09-02T00:00:00Z", age_s: 30, stale: false }, loop_health: { consecutive_failures: 0, last_error: null }, checked_at: "x" };
const ENGINE_READY = { cli_path: "/usr/local/bin/claude", version: "1.0.99 (Claude Code)", auth: "oauth", auth_sources: { oauth: true, env_key: false, secrets_file: false, legacy_file: false }, ready: true };

function permissions(): PermissionsSnapshot {
  return {
    home: "/h", on_external_volume: false,
    fda: { needed: false, pane: "x", executables: [] },
    panes: { full_disk: "x", screen: "y", microphone: "z", notifications: "n" },
    doctor: [], doctor_ran_at: "2026-09-02T00:00:00Z", doctor_ok: true,
    vault: { status: "unknown", root: "/Users/demo/Documents/Obsidian Vault" },
  };
}

function setup(): SetupSnapshot {
  return { needed: true, done: false, config_exists: true, config_example_exists: true, secrets: {}, home: "/h", protected_location: false };
}

function renderWelcome(language: "zh" | "en", search = "/?page=setup&step=welcome") {
  window.history.replaceState(null, "", search);
  setLanguage(language);
  return render(<LanguageContext.Provider value={language}><SetupPage /></LanguageContext.Provider>);
}

beforeEach(() => {
  resetStoreForTests();
  window.localStorage.clear();
  window.sessionStorage.clear();
  vi.mocked(putSettingsSection).mockReset();
  vi.mocked(fetchSetup).mockResolvedValue(setup());
  vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [] });
  vi.mocked(fetchSetupEngine).mockResolvedValue(ENGINE_READY);
  vi.mocked(fetchHealth).mockResolvedValue(HEALTH_OK);
  vi.mocked(fetchPermissions).mockResolvedValue(permissions());
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("SetupPage · 界面语言单选写回 server", () => {
  it("点 English → store 立刻 en（不等 PUT）+ PUT general {language: en}", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("en"));
    renderWelcome("zh");
    const radio = await screen.findByRole("radio", { name: "English" });
    fireEvent.click(radio);
    expect(getState().language).toBe("en");
    expect(window.localStorage.getItem("zai.lang")).toBe("en");
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["general", { language: "en" }]);
  });

  it("点 中文 同款", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("zh"));
    renderWelcome("en");
    fireEvent.click(await screen.findByRole("radio", { name: "中文" }));
    expect(getState().language).toBe("zh");
    await waitFor(() => expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["general", { language: "zh" }]));
  });

  it("PUT 失败（离线 / 浏览器会话无 token）：本次切换照样生效，向导不报错", async () => {
    vi.mocked(putSettingsSection).mockRejectedValue(new ApiError(0, { error: { code: "SERVICE_UNAVAILABLE", message: "offline" } }));
    renderWelcome("zh");
    fireEvent.click(await screen.findByRole("radio", { name: "English" }));
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(getState().language).toBe("en");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("摘掉一次性的 ?lang= 覆写（?page= / ?step= 留着）", async () => {
    vi.mocked(putSettingsSection).mockResolvedValue(receipt("en"));
    renderWelcome("zh", "/?page=setup&step=welcome&lang=zh");
    fireEvent.click(await screen.findByRole("radio", { name: "English" }));
    expect(window.location.search).toBe("?page=setup&step=welcome");
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
  });
});
