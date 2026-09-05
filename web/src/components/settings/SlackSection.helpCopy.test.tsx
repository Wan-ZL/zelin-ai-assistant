// Slack 接入区的三句原生文案（parity 批 catalog-help-copy；原生 SettingsSlack.swift body 首段 / step ② / copyManifest）：
//   1) 区首导语来自 server 目录的 section help（server-owned，web 只渲染）——目录给了就出现在标题之下；
//   2) 第 ② 步末尾的「公司要求管理员审批的话，等批下来再做第 ③ 步——期间雷达会用只读 MCP 兜底扫描，不会干等。」；
//   3) 「复制 App Manifest」遇 GET /api/slack/manifest 404（repo 里没有那份文件）→ 「找不到 <path>——repo 不完整？重装一次即可。」
//      （路径取 server details.path，缺了就用 repo 相对路径）；其它错误仍是原句。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchRadarAgents, fetchSecrets, fetchSettingsCatalog, fetchSetup, fetchSlackManifest } from "../../api";
import { getI18n, LanguageContext, type Language } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { SettingsCatalog } from "../../types";
import { manifestErrorMessage, SlackSection } from "./SlackSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchSettingsCatalog: vi.fn(),
    fetchSecrets: vi.fn(),
    fetchSetup: vi.fn(),
    fetchRadarAgents: vi.fn(),
    fetchSlackManifest: vi.fn(),
  };
});

const INTRO_ZH = "把「别人在 Slack 上找你的事」（DM / 群 / @提及）自动变成提案卡。3 步全在这里完成，不用改任何文件；对外只出草稿，永远你自己发。此区改动即时生效。";
const INTRO_EN = "Turns \"people needing you on Slack\" (DMs / groups / @mentions) into proposal cards automatically. All 3 setup steps happen right here — no files to edit; outbound replies are drafts only, you always send them yourself. Changes apply immediately.";

const catalog = (): SettingsCatalog => ({ sections: [{
  id: "slack", title: { zh: "Slack 接入", en: "Slack" }, help: { zh: INTRO_ZH, en: INTRO_EN },
  fields: [
    { key: "slack_enabled", kind: "bool", label: { zh: "启用 Slack 雷达", en: "Enable the Slack radar" }, help: { zh: "", en: "" },
      default: true, choices: null, effective: true, source: "default" },
  ],
}] });

const notFound = (details?: unknown) =>
  new ApiError(404, { error: { code: "NOT_FOUND", message: "slack app manifest not found", details } });

function renderIn(language: Language) {
  return render(<LanguageContext.Provider value={language}><SlackSection /></LanguageContext.Provider>);
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchSettingsCatalog).mockReset();
  vi.mocked(fetchSlackManifest).mockReset();
  vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog());
  vi.mocked(fetchSecrets).mockResolvedValue({ secrets: [] });
  vi.mocked(fetchSetup).mockResolvedValue({ needed: false, done: true, config_exists: true, config_example_exists: true, secrets: {}, home: "/h", protected_location: false });
  vi.mocked(fetchRadarAgents).mockResolvedValue({ radars: {} });
});

afterEach(cleanup);

describe("manifestErrorMessage (native copyManifest)", () => {
  it("maps the 404 to the plain sentence with the server's path, in both languages", () => {
    const err = notFound({ path: "config/slack-app-manifest.json" });
    expect(manifestErrorMessage(err, getI18n("zh").text)).toBe("找不到 config/slack-app-manifest.json——repo 不完整？重装一次即可。");
    expect(manifestErrorMessage(err, getI18n("en").text)).toBe("Missing config/slack-app-manifest.json — incomplete repo? Reinstall to fix.");
  });

  it("falls back to the repo-relative path when the 404 carries no details", () => {
    expect(manifestErrorMessage(notFound(), getI18n("zh").text)).toBe("找不到 config/slack-app-manifest.json——repo 不完整？重装一次即可。");
    expect(manifestErrorMessage(notFound({ path: 7 }), getI18n("en").text)).toBe("Missing config/slack-app-manifest.json — incomplete repo? Reinstall to fix.");
  });

  it("leaves every other error as its own sentence", () => {
    const denied = new ApiError(403, { error: { code: "FORBIDDEN", message: "origin not allowed" } });
    expect(manifestErrorMessage(denied, getI18n("zh").text)).toBe("origin not allowed");
    expect(manifestErrorMessage(new Error("clipboard blocked"), getI18n("en").text)).toBe("clipboard blocked");
  });
});

describe("SlackSection copy", () => {
  it("renders the server-owned intro under the title and the admin-approval / MCP-fallback sentence in step ②", async () => {
    renderIn("zh");
    await screen.findByText(INTRO_ZH);
    const step2 = screen.getByText(/② 安装授权/).parentElement!;
    expect(step2.textContent).toContain("公司要求管理员审批的话，等批下来再做第 ③ 步——期间雷达会用只读 MCP 兜底扫描，不会干等。");
  });

  it("renders the intro and step ② in English too", async () => {
    renderIn("en");
    await screen.findByText(INTRO_EN);
    const step2 = screen.getByText(/② Install & authorize/).parentElement!;
    expect(step2.textContent).toContain("If your company requires admin approval, do step ③ once it's granted — meanwhile the radar falls back to read-only MCP scanning instead of waiting idle.");
  });

  it("Copy App Manifest on a 404 shows the plain missing-file sentence as an alert", async () => {
    vi.mocked(fetchSlackManifest).mockRejectedValue(notFound({ path: "config/slack-app-manifest.json" }));
    renderIn("zh");
    await screen.findByText(INTRO_ZH);
    fireEvent.click(screen.getByRole("button", { name: "复制 App Manifest" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("找不到 config/slack-app-manifest.json——repo 不完整？重装一次即可。");
  });

  it("Copy App Manifest on any other failure keeps the original sentence", async () => {
    vi.mocked(fetchSlackManifest).mockRejectedValue(new ApiError(500, { error: { code: "INTERNAL", message: "disk on fire" } }));
    renderIn("en");
    await screen.findByText(INTRO_EN);
    fireEvent.click(screen.getByRole("button", { name: "Copy App Manifest" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("disk on fire");
  });
});
