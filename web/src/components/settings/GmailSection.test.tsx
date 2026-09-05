// Gmail 接入区（§14bis / §48.1 / §68.1 追记；原生 SettingsGmail.swift setUseCommand / validateAddress / stepCard）：
//   1) 「抓取方式」A/B 派生自生效的 gmail_fetch_command：命令生效着 = B（不是本地单选）；
//   2) 选 A 而命令生效着 → PUT {gmail_fetch_command: ""} + 「已切回 A：…」；命令来自 config.yaml 清不掉 → 如实说；PUT 被拒 → 「保存设置失败: 」+ 原句、单选留在 B；
//   3) 选 B 而命令空着 → 「填好下面的抓取命令并点「保存」即生效。」+ 命令字段出现；本会话切回过 A 的命令再选 B 直接写回；
//   4) Gmail 地址不合 email 形状 → server-owned 那句就地出现、「保存」不放行、不发 PUT；改对即放行；
//      config.yaml 里一个坏地址（没改过、不进 PUT）只就地亮那句，不锁住同区的开关；
//   5) 第 ① 步的两步验证前提与 Workspace 提示回来了；
//   6) 开关翻开 → PUT 后整本目录再拉一次（§48.1 合取写的另一半住 flags 区）；
//   7) 选 A 的旁路 PUT 换了本区快照，没保存的地址草稿不被吞（原生各 TextField 互不相干）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchRadarAgents, fetchSecrets, fetchSettingsCatalog, fetchSetup, putSettingsSection } from "../../api";
import { LanguageContext, type Language } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { SettingsCatalog, SettingsSection } from "../../types";
import { GmailSection } from "./GmailSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchSettingsCatalog: vi.fn(),
    putSettingsSection: vi.fn(),
    fetchSecrets: vi.fn(),
    fetchSetup: vi.fn(),
    fetchRadarAgents: vi.fn(),
  };
});

const EMAIL_CHECK = {
  kind: "email",
  message: {
    zh: "邮箱格式不对——例：you@gmail.com（公司 Google Workspace 邮箱也可以）",
    en: "That email doesn't look right — e.g. you@gmail.com (a Google Workspace address works too)",
  },
};

function gmail(command = "", commandSource = "override", address = "", addressSource = "override"): SettingsSection {
  return {
    id: "gmail",
    title: { zh: "Gmail 接入", en: "Gmail" },
    help: { zh: "", en: "" },
    fields: [
      { key: "gmail_enabled", kind: "bool", label: { zh: "启用 Gmail 雷达", en: "Enable the Gmail radar" }, help: { zh: "", en: "" },
        default: true, choices: null, effective: true, source: "default" },
      { key: "gmail_address", kind: "string", label: { zh: "Gmail 地址", en: "Gmail address" }, help: { zh: "", en: "" },
        default: "", choices: null, effective: address, source: address ? addressSource : "default",
        placeholder: { zh: "例：you@gmail.com", en: "e.g. you@gmail.com" }, check: EMAIL_CHECK },
      { key: "gmail_fetch_command", kind: "string", label: { zh: "自定义抓取命令（B 路径）", en: "Custom fetch command (path B)" }, help: { zh: "", en: "" },
        default: "", choices: null, effective: command, source: command ? commandSource : "default",
        placeholder: { zh: "例：/Users/you/bin/gmail-fetch.sh", en: "e.g. /Users/you/bin/gmail-fetch.sh" } },
    ],
  };
}

const flags = (): SettingsSection => ({
  id: "flags", title: { zh: "Feature flags", en: "Feature flags" }, help: { zh: "", en: "" },
  fields: [{ key: "features.gmail_radar", kind: "bool", label: { zh: "gmail_radar — Gmail 捕获", en: "gmail_radar — Gmail capture" }, help: { zh: "", en: "" },
    default: true, choices: null, effective: false, source: "config" }],
});

const catalog = (section: SettingsSection): SettingsCatalog => ({ sections: [section, flags()] });

function renderIn(language: Language = "en") {
  return render(<LanguageContext.Provider value={language}><GmailSection /></LanguageContext.Provider>);
}

const radio = (value: "app_password" | "command") =>
  document.querySelector<HTMLInputElement>(`input[name="gmail-fetch-path"][value="${value}"]`)!;

/** 本区的「保存」（settings-actions 行里的 btn-primary）——凭证行自己那颗「保存」住 settings-knob-controls */
const sectionSave = (name: string) =>
  screen.getAllByRole("button", { name }).find((b) => b.classList.contains("btn-primary") && b.closest(".settings-actions") !== null) as HTMLButtonElement;

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchSettingsCatalog).mockReset();
  vi.mocked(putSettingsSection).mockReset();
  vi.mocked(fetchSecrets).mockReset();
  vi.mocked(fetchSetup).mockReset();
  vi.mocked(fetchRadarAgents).mockReset();
  vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [] });
  vi.mocked(fetchSetup).mockResolvedValue({ needed: false, done: true, config_exists: true, config_example_exists: true, secrets: {}, home: "/h", protected_location: false });
  vi.mocked(fetchRadarAgents).mockResolvedValue({ radars: {} });
});

afterEach(cleanup);

describe("GmailSection fetch path A/B (§14bis, native setUseCommand)", () => {
  it("derives B from the effective command; choosing A issues the clearing PUT and says 已切回 A", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(gmail("/Users/you/bin/gmail-fetch.sh")));
    vi.mocked(putSettingsSection).mockResolvedValue(gmail(""));
    renderIn("zh");
    await screen.findByText("抓取方式");
    expect(radio("command").checked).toBe(true);
    expect(screen.getByDisplayValue("/Users/you/bin/gmail-fetch.sh")).toBeTruthy();   // B 遍：命令字段在场
    expect(screen.queryByText(/① 生成应用专用密码/)).toBeNull();                          // 引导卡只在 A 遍

    fireEvent.click(radio("app_password"));
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["gmail", { gmail_fetch_command: "" }]);
    await screen.findByRole("status");
    expect(screen.getByRole("status").textContent).toBe("已切回 A：走应用专用密码通道（抓取命令已停用，命令文本保留着，切回 B 随时恢复）。");
    expect(radio("app_password").checked).toBe(true);
    expect(screen.queryByDisplayValue("/Users/you/bin/gmail-fetch.sh")).toBeNull();
    expect(screen.getByText(/① 生成应用专用密码/)).toBeTruthy();
  });

  it("switching back to B re-activates the kept command with one PUT (native saveFetchCommand)", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(gmail("/Users/you/bin/gmail-fetch.sh")));
    vi.mocked(putSettingsSection).mockResolvedValueOnce(gmail("")).mockResolvedValueOnce(gmail("/Users/you/bin/gmail-fetch.sh"));
    renderIn("en");
    await screen.findByText("Fetch path");
    fireEvent.click(radio("app_password"));
    await screen.findByRole("status");
    fireEvent.click(radio("command"));
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(2));
    expect(vi.mocked(putSettingsSection).mock.calls[1]).toEqual(["gmail", { gmail_fetch_command: "/Users/you/bin/gmail-fetch.sh" }]);
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/^Saved ✓ From the next round/));
    expect(radio("command").checked).toBe(true);
    expect(screen.getByDisplayValue("/Users/you/bin/gmail-fetch.sh")).toBeTruthy();
  });

  it("choosing B with no command shows the fill-in hint and the command field, without a PUT", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(gmail("")));
    renderIn("zh");
    await screen.findByText("抓取方式");
    expect(radio("app_password").checked).toBe(true);
    expect(screen.queryByPlaceholderText("例：/Users/you/bin/gmail-fetch.sh")).toBeNull();
    fireEvent.click(radio("command"));
    expect(radio("command").checked).toBe(true);
    expect(screen.getByText("填好下面的抓取命令并点「保存」即生效。")).toBeTruthy();
    expect(screen.getByPlaceholderText("例：/Users/you/bin/gmail-fetch.sh")).toBeTruthy();
    expect(putSettingsSection).not.toHaveBeenCalled();
    // 回到 A：没有要停用的命令，同样不发请求
    fireEvent.click(radio("app_password"));
    expect(radio("app_password").checked).toBe(true);
    expect(putSettingsSection).not.toHaveBeenCalled();
  });

  it("a rejected clearing PUT keeps the picker on B and shows 保存设置失败: + the server sentence", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(gmail("/x/fetch.sh")));
    vi.mocked(putSettingsSection).mockRejectedValue(new ApiError(409, { error: { code: "CONFLICT", message: "settings_overrides.json is not valid JSON" } }));
    renderIn("zh");
    await screen.findByText("抓取方式");
    fireEvent.click(radio("app_password"));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toBe("保存设置失败: settings_overrides.json is not valid JSON");
    expect(screen.getByRole("alert").firstElementChild?.textContent).toBe("保存设置失败: ");   // 前缀独立节点
    expect(radio("command").checked).toBe(true);
  });

  it("is honest when the command survives the clear because config.yaml holds it", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(gmail("/x/fetch.sh", "config")));
    vi.mocked(putSettingsSection).mockResolvedValue(gmail("/x/fetch.sh", "config"));
    renderIn("en");
    await screen.findByText("Fetch path");
    fireEvent.click(radio("app_password"));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toMatch(/comes from config\.yaml/);
    expect(radio("command").checked).toBe(true);
  });

  it("keeps an unsaved address draft across the A click (the clearing PUT only realigns the command)", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(gmail("/x/fetch.sh")));
    vi.mocked(putSettingsSection).mockResolvedValue(gmail(""));
    renderIn("en");
    const input = await screen.findByPlaceholderText("e.g. you@gmail.com") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "new@gmail.com" } });
    fireEvent.click(radio("app_password"));
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    await screen.findByRole("status");
    expect(radio("app_password").checked).toBe(true);
    expect((screen.getByPlaceholderText("e.g. you@gmail.com") as HTMLInputElement).value).toBe("new@gmail.com");
    expect(screen.getByText("1 unsaved")).toBeTruthy();
    // 草稿还在、还能保存：PUT 只带地址（命令已在旁路里落盘）
    fireEvent.click(sectionSave("Save"));
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(2));
    expect(vi.mocked(putSettingsSection).mock.calls[1]).toEqual(["gmail", { gmail_address: "new@gmail.com" }]);
  });
});

describe("GmailSection address validation + step ① copy (native validateAddress / stepCard)", () => {
  it("blocks Save on a malformed address with the server-owned sentence, no PUT; a valid one unblocks", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(gmail("")));
    vi.mocked(putSettingsSection).mockResolvedValue(gmail("", "override", "you@gmail.com"));
    renderIn("zh");
    const input = await screen.findByPlaceholderText("例：you@gmail.com") as HTMLInputElement;
    expect(input.type).toBe("email");
    const save = sectionSave("保存");
    fireEvent.change(input, { target: { value: "foo" } });
    expect(screen.getByText(EMAIL_CHECK.message.zh)).toBeTruthy();
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(save.disabled).toBe(true);
    fireEvent.click(save);
    expect(putSettingsSection).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "you@gmail.com" } });
    expect(screen.queryByText(EMAIL_CHECK.message.zh)).toBeNull();
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["gmail", { gmail_address: "you@gmail.com" }]);
  });

  it("does not let a malformed config.yaml address (not dirty, not sent) lock the switch", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(gmail("", "override", "me@gmail", "config")));
    vi.mocked(putSettingsSection).mockResolvedValue({ ...gmail("", "override", "me@gmail", "config"), fields: gmail("", "override", "me@gmail", "config").fields.map((f) => (f.key === "gmail_enabled" ? { ...f, effective: false, source: "override" } : f)) });
    renderIn("en");
    const toggle = await screen.findByRole("switch", { name: "Enable the Gmail radar" });
    expect(screen.getByText(EMAIL_CHECK.message.en)).toBeTruthy();   // 那句仍就地亮着
    fireEvent.click(toggle);
    const save = sectionSave("Save");
    expect(screen.getByText("1 unsaved")).toBeTruthy();
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["gmail", { gmail_enabled: false }]);
  });

  it("uses the English sentence under the English UI", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(gmail("")));
    renderIn("en");
    const input = await screen.findByPlaceholderText("e.g. you@gmail.com");
    fireEvent.change(input, { target: { value: "not-an-address" } });
    expect(screen.getByText(EMAIL_CHECK.message.en)).toBeTruthy();
  });

  it("restores the 2-Step prerequisite and the Workspace caveat in step ①", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(gmail("")));
    renderIn("zh");
    await screen.findByText("① 生成应用专用密码（一次性，~1 分钟）");
    expect(screen.getByText(/要求账号已开两步验证。页面里 App name 随便填/)).toBeTruthy();
    expect(screen.getByText(/The setting you are looking for is not available for your account/)).toBeTruthy();
    expect(screen.getByText("公司 Workspace 禁用了应用专用密码时走 B——雷达定时调你自己的命令去抓邮件，抓回来的分诊完全相同。")).toBeTruthy();
    expect(screen.getByRole("link", { name: "打开 Google 应用专用密码页" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "打不开？先开两步验证" })).toBeTruthy();
    expect(screen.getByText("② 填 Gmail 地址")).toBeTruthy();
  });
});

describe("GmailSection radar switch (§48.1 conjunction, client half)", () => {
  it("turning the switch on re-pulls the catalog so the flags row can show the server-written other half", async () => {
    const off = catalog({ ...gmail(""), fields: gmail("").fields.map((f) => (f.key === "gmail_enabled" ? { ...f, effective: false, source: "override" } : f)) });
    const on = catalog(gmail(""));
    on.sections[1] = { ...flags(), fields: flags().fields.map((f) => ({ ...f, effective: true, source: "override" })) };
    vi.mocked(fetchSettingsCatalog).mockResolvedValueOnce(off).mockResolvedValueOnce(on);
    vi.mocked(putSettingsSection).mockResolvedValue(gmail(""));
    renderIn("en");
    const toggle = await screen.findByRole("switch", { name: "Enable the Gmail radar" }) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    fireEvent.click(toggle);
    fireEvent.click(sectionSave("Save"));
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["gmail", { gmail_enabled: true }]);
    await waitFor(() => expect(fetchSettingsCatalog).toHaveBeenCalledTimes(2));
  });

  it("turning the switch off does not re-pull (the off write is a single key)", async () => {
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(gmail("")));
    vi.mocked(putSettingsSection).mockResolvedValue({ ...gmail(""), fields: gmail("").fields.map((f) => (f.key === "gmail_enabled" ? { ...f, effective: false, source: "override" } : f)) });
    renderIn("en");
    const toggle = await screen.findByRole("switch", { name: "Enable the Gmail radar" });
    fireEvent.click(toggle);
    fireEvent.click(sectionSave("Save"));
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    await screen.findByRole("status");
    expect(fetchSettingsCatalog).toHaveBeenCalledTimes(1);
  });
});
