// 通用设置区（§68）：server 目录驱动渲染；只 PUT 改动过的键；server 400 原文 toast；
// 目录缺 section 时诚实说明；凭证行 write-only（保存后清空、值永不显示、验证结果分网络/凭证）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchSecrets, fetchSettingsCatalog, fetchSetup, putSecret, putSettingsSection, verifySecret } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { SettingsCatalog, SettingsSection } from "../../types";
import { CatalogSection, changedKeys } from "./CatalogSection";
import { SecretRow } from "./SecretRow";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchSettingsCatalog: vi.fn(),
    putSettingsSection: vi.fn(),
    fetchSecrets: vi.fn(),
    putSecret: vi.fn(),
    verifySecret: vi.fn(),
    fetchSetup: vi.fn(),
  };
});

function section(over: Partial<SettingsSection> = {}): SettingsSection {
  return {
    id: "general",
    title: { zh: "通用", en: "General" },
    help: { zh: "", en: "General knobs." },
    fields: [
      { key: "language", kind: "enum", label: { zh: "界面语言", en: "Interface language" }, help: { zh: "", en: "" },
        default: "zh", choices: ["zh", "en"], effective: "zh", source: "default" },
      { key: "updates_check_enabled", kind: "bool", label: { zh: "自动检查新版本", en: "Check for updates" }, help: { zh: "", en: "" },
        default: true, choices: null, effective: true, source: "config" },
      { key: "default_target_repo", kind: "string", label: { zh: "目录", en: "Folder" }, help: { zh: "", en: "" },
        default: "~/Projects/your-workbench", choices: null, effective: "~/Projects/your-workbench", source: "default" },
    ],
    ...over,
  };
}

const catalog = (): SettingsCatalog => ({ sections: [section()] });

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchSettingsCatalog).mockReset();
  vi.mocked(putSettingsSection).mockReset();
  vi.mocked(fetchSecrets).mockReset();
  vi.mocked(putSecret).mockReset();
  vi.mocked(verifySecret).mockReset();
  vi.mocked(fetchSetup).mockReset();
  vi.mocked(fetchSetup).mockResolvedValue({ needed: false, done: true, config_exists: true, config_example_exists: true, secrets: {}, home: "/h", protected_location: false });
});

afterEach(cleanup);

describe("CatalogSection", () => {
  it("renders server-owned labels and source chips, saves only the changed keys", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog());
    vi.mocked(putSettingsSection).mockResolvedValue(section({
      fields: section().fields.map((f) => (f.key === "updates_check_enabled" ? { ...f, effective: false, source: "override" } : f)),
    }));
    renderEn(<CatalogSection sectionId="general" />);
    await screen.findByText("Interface language");
    expect(screen.getByText("from config.yaml")).toBeTruthy();
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.click(screen.getByRole("switch", { name: "Check for updates" }));
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["general", { updates_check_enabled: false }]);
    await screen.findByRole("status");
    expect(screen.getByRole("status").textContent).toMatch(/^Saved \d\d:\d\d:\d\d$/);   // 原生 noteSaved：「Saved HH:mm:ss」
    expect(screen.getByText("set here")).toBeTruthy();
  });

  it("shows the server's 400 sentence as an alert toast", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog());
    vi.mocked(putSettingsSection).mockRejectedValue(new ApiError(400, { error: { code: "INVALID_FIELD", message: "language must be one of zh, en" } }));
    renderEn(<CatalogSection sectionId="general" />);
    await screen.findByText("Interface language");
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "en" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toBe("Failed to save settings: language must be one of zh, en");
  });

  it("is honest when the server catalog lacks the section", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog());
    renderEn(<CatalogSection sectionId="voice" />);
    await screen.findByText(/no such section/);
  });

  it("changedKeys compares strings trimmed and others strictly", () => {
    const s = section();
    expect(changedKeys(s, { language: "zh", updates_check_enabled: true, default_target_repo: " ~/Projects/your-workbench " })).toEqual([]);
    expect(changedKeys(s, { language: "en", updates_check_enabled: false, default_target_repo: "x" })).toEqual(["language", "updates_check_enabled", "default_target_repo"]);
  });
});

describe("SecretRow", () => {
  it("saves write-only and verifies on save（原生「保存即验证」）: input cleared, value never rendered, status refreshed", async () => {
    vi.mocked(fetchSecrets)
      .mockResolvedValueOnce({ secrets: [{ name: "slack-user-token.txt", label: { zh: "Slack", en: "Slack token" }, present: false, verifiable: true, mtime: null }] })
      .mockResolvedValue({ secrets: [{ name: "slack-user-token.txt", label: { zh: "Slack", en: "Slack token" }, present: true, verifiable: true, mtime: 1 }] });
    vi.mocked(putSecret).mockResolvedValue({ name: "slack-user-token.txt", label: { zh: "Slack", en: "Slack token" }, present: true, verifiable: true, mtime: 1 });
    vi.mocked(verifySecret).mockResolvedValue({ ok: true, network: false, detail: "auth.test ok", extra: {} });
    renderEn(<SecretRow name="slack-user-token.txt" />);
    const { refreshSecrets } = await import("../../store");
    await refreshSecrets();
    await screen.findByText("Not set");
    const input = screen.getByLabelText("Slack token value") as HTMLInputElement;
    expect(input.placeholder).toBe("Paste, then Save (stored locally; verified on save)");
    fireEvent.change(input, { target: { value: "xoxp-SECRET" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putSecret).toHaveBeenCalledWith("slack-user-token.txt", "xoxp-SECRET"));
    await screen.findByText(/Saved ✓ verified/);
    expect(verifySecret).toHaveBeenCalledWith("slack-user-token.txt");
    expect(screen.getByText("verified ✓")).toBeTruthy();
    expect(input.value).toBe("");
    expect(document.body.textContent).not.toContain("xoxp-SECRET");
  });

  it("a key without a probe says 已保存（App 内管理）and never shows Verify", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [{ name: "volcano-ark-key.txt", label: { zh: "Ark", en: "Ark key" }, present: true, verifiable: false, mtime: 1 }] });
    renderEn(<SecretRow name="volcano-ark-key.txt" />);
    const { refreshSecrets } = await import("../../store");
    await refreshSecrets();
    await screen.findByText("Saved (managed in-app)");
    expect(screen.queryByRole("button", { name: "Verify" })).toBeNull();
    expect((screen.getByLabelText("Ark key value") as HTMLInputElement).placeholder).toBe("Paste, then Save (stored locally; no network)");
  });

  it("verify separates network errors from credential rejections", async () => {
    vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [{ name: "anthropic-api-key.txt", label: { zh: "k", en: "Anthropic key" }, present: true, verifiable: true, mtime: 1 }] });
    vi.mocked(verifySecret).mockResolvedValueOnce({ ok: false, network: true, detail: "network error: dns", extra: {} })
      .mockResolvedValueOnce({ ok: false, network: false, detail: "HTTP 401", extra: {} });
    renderEn(<SecretRow name="anthropic-api-key.txt" />);
    const { refreshSecrets } = await import("../../store");
    await refreshSecrets();
    const verify = await screen.findByRole("button", { name: "Verify" });
    fireEvent.click(verify);
    await screen.findByText(/Network error \(not the credential\): network error: dns/);
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByText((_, el) => el?.tagName === "P" && /Verification failed: HTTP 401/.test(el.textContent ?? ""));
    expect(screen.getByText("verification failed")).toBeTruthy();
  });
});
