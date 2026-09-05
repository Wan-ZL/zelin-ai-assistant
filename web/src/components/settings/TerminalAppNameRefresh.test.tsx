// 「会在 <终端> 中打开」的终端名跟着「通用 · 终端应用」换（§68.7 追记）：`terminal_app_name` 是 server 在 maintainer 区算的，
// 而 PUT /api/settings/general 的回执只有 general 区——store.saveSettingsSection 在 patch 带 `terminal_app` 时整本目录再拉一次
// （§48.1 合取写的同一条 best-effort 路），MaintainerExtras 只读目录，于是这句随之换名；general 区别的键不触发重拉；
// 拉失败不影响本次保存的回执。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSettingsCatalog, putSettingsSection } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshSettingsCatalog, resetStoreForTests, saveSettingsSection } from "../../store";
import type { SettingsCatalog, SettingsSection } from "../../types";
import { MaintainerExtras } from "./MaintainerExtras";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSettingsCatalog: vi.fn(), putSettingsSection: vi.fn() };
});

function general(terminalApp: string): SettingsSection {
  return {
    id: "general", title: { zh: "通用", en: "General" }, help: { zh: "", en: "" },
    fields: [{ key: "terminal_app", kind: "enum", label: { zh: "终端应用", en: "Terminal app" }, help: { zh: "", en: "" },
      default: "auto", choices: ["auto", "ghostty", "terminal", "iterm2"], effective: terminalApp, source: "override" }],
  };
}

function maintainer(terminalAppName: string): SettingsSection {
  return { id: "maintainer", title: { zh: "开发者 · 开发会话", en: "Developer session" }, help: { zh: "", en: "" }, terminal_app_name: terminalAppName, fields: [] };
}

function catalog(terminalApp: string, terminalAppName: string): SettingsCatalog {
  return { sections: [general(terminalApp), maintainer(terminalAppName)] };
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchSettingsCatalog).mockReset();
  vi.mocked(putSettingsSection).mockReset();
});
afterEach(cleanup);

describe("saveSettingsSection(general, {terminal_app}) → 目录重拉 → 开发者区的终端名跟着换", () => {
  it("the helper names the new terminal after General's terminal_app is saved (no reload needed)", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValueOnce(catalog("terminal", "Terminal"));
    await refreshSettingsCatalog();
    render(<LanguageContext.Provider value="zh"><MaintainerExtras /></LanguageContext.Provider>);
    expect(screen.getByText("会在 Terminal 中打开（终端应用在「通用」里换）。")).toBeTruthy();

    vi.mocked(putSettingsSection).mockResolvedValueOnce(general("ghostty"));
    vi.mocked(fetchSettingsCatalog).mockResolvedValueOnce(catalog("ghostty", "Ghostty"));
    await saveSettingsSection("general", { terminal_app: "ghostty" });
    expect(putSettingsSection).toHaveBeenCalledWith("general", { terminal_app: "ghostty" });
    expect(fetchSettingsCatalog).toHaveBeenCalledTimes(2);
    await screen.findByText("会在 Ghostty 中打开（终端应用在「通用」里换）。");
    expect(screen.queryByText(/会在 Terminal 中打开/)).toBeNull();
  });

  it("saving other General keys does not refetch the catalog", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValueOnce(catalog("terminal", "Terminal"));
    await refreshSettingsCatalog();
    vi.mocked(putSettingsSection).mockResolvedValueOnce(general("terminal"));
    await saveSettingsSection("general", { language: "en" });
    expect(fetchSettingsCatalog).toHaveBeenCalledTimes(1);
  });

  it("a failed refetch still returns the PUT receipt and keeps the old name", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValueOnce(catalog("terminal", "Terminal"));
    await refreshSettingsCatalog();
    render(<LanguageContext.Provider value="en"><MaintainerExtras /></LanguageContext.Provider>);
    vi.mocked(putSettingsSection).mockResolvedValueOnce(general("iterm2"));
    vi.mocked(fetchSettingsCatalog).mockRejectedValueOnce(new Error("offline"));
    const receipt = await saveSettingsSection("general", { terminal_app: "iterm2" });
    expect(receipt.id).toBe("general");
    expect(fetchSettingsCatalog).toHaveBeenCalledTimes(2);
    await Promise.resolve();
    expect(screen.getByText("Opens in Terminal (change the terminal app under General).")).toBeTruthy();
  });
});
