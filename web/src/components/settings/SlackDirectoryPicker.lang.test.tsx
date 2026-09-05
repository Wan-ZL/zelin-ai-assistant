// Slack「监控范围」勾选器把当前 UI 语言交给目录请求（CONTRACT §68.1 2026-09-05 追记；parity gap settings-python-copy-ui-language）：
// 原生 SettingsSlack.fetchDirectory 起 python 时带 env AIASSISTANT_UI_LANG = LanguageMirror.current，act 侧的双语失败句
// （先粘贴并保存 token / Paste and save the token first…）才与 app 语言一致。web：每次 fetchSlackDirectory 都带 useI18n().language
// ——手点加载 / 刷新、验证成功后的自动加载 都一样；切语言不重拉（原生 .onChange(of: i18n.lang) 只 refreshStatus）；
// 失败句仍是 server 回的 message 原文（挑句在 act 侧，web 不翻译）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSettingsCatalog, fetchSlackDirectory, putSettingsSection } from "../../api";
import { LanguageContext, type Language } from "../../i18n";
import { markSlackTokenVerified, refreshSettingsCatalog, resetStoreForTests } from "../../store";
import type { SettingsCatalog, SlackDirectory } from "../../types";
import { SlackDirectoryPicker } from "./SlackDirectoryPicker";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSettingsCatalog: vi.fn(), fetchSlackDirectory: vi.fn(), putSettingsSection: vi.fn() };
});

const catalog: SettingsCatalog = { sections: [{
  id: "slack", title: { zh: "Slack 接入", en: "Slack" }, help: { zh: "", en: "" },
  fields: [
    { key: "slack_channels", kind: "list", label: { zh: "监控频道", en: "Watched channels" }, help: { zh: "", en: "" }, default: [], choices: null, effective: [], source: "default" },
    { key: "watch_people", kind: "list", label: { zh: "关注的人", en: "People to watch" }, help: { zh: "", en: "" }, default: [], choices: null, effective: [], source: "default" },
  ],
}] };
const directory: SlackDirectory = { ok: true, fetched_at: "2026-09-05T00:00:00Z",
  channels: [{ id: "C1", name: "eng" }], users: [] };

function renderIn(language: Language) {
  return render(<LanguageContext.Provider value={language}><SlackDirectoryPicker /></LanguageContext.Provider>);
}

beforeEach(async () => {
  resetStoreForTests();
  vi.mocked(fetchSlackDirectory).mockReset();
  vi.mocked(putSettingsSection).mockReset();
  vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog);
  await refreshSettingsCatalog();
});
afterEach(cleanup);

describe("SlackDirectoryPicker sends the board language with every directory request", () => {
  it("zh board → lang zh on load and on refresh", async () => {
    vi.mocked(fetchSlackDirectory).mockResolvedValue(directory);
    renderIn("zh");
    fireEvent.click(screen.getByRole("button", { name: "加载频道和成员" }));
    await screen.findByText("频道（@你 才建卡）");
    expect(fetchSlackDirectory).toHaveBeenCalledWith(false, "zh");
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(fetchSlackDirectory).toHaveBeenLastCalledWith(true, "zh"));
  });

  it("en board → lang en", async () => {
    vi.mocked(fetchSlackDirectory).mockResolvedValue(directory);
    renderIn("en");
    fireEvent.click(screen.getByRole("button", { name: "Load channels & members" }));
    await screen.findByText("Channels (card only when @mentioned)");
    expect(fetchSlackDirectory).toHaveBeenCalledWith(false, "en");
  });

  it("the post-verification autoload carries the language too (native loadDirectory(refresh:true) under the same env)", async () => {
    vi.mocked(fetchSlackDirectory).mockResolvedValue(directory);
    renderIn("zh");
    expect(fetchSlackDirectory).not.toHaveBeenCalled();
    markSlackTokenVerified();
    await screen.findByText("频道（@你 才建卡）");
    expect(fetchSlackDirectory).toHaveBeenCalledTimes(1);
    expect(fetchSlackDirectory).toHaveBeenLastCalledWith(true, "zh");
  });

  it("the act-side failure sentence is shown verbatim — picking the language happens in the subprocess, not here", async () => {
    vi.mocked(fetchSlackDirectory).mockResolvedValue({ ok: false, error: "no_token", message: "Paste and save the token first", channels: [], users: [] });
    renderIn("en");
    fireEvent.click(screen.getByRole("button", { name: "Load channels & members" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Paste and save the token first");
    expect(fetchSlackDirectory).toHaveBeenCalledWith(false, "en");
  });

  it("switching the language does not refetch by itself (native onChange(i18n.lang) only refreshStatus)", async () => {
    vi.mocked(fetchSlackDirectory).mockResolvedValue(directory);
    const view = renderIn("en");
    fireEvent.click(screen.getByRole("button", { name: "Load channels & members" }));
    await screen.findByText("Channels (card only when @mentioned)");
    view.rerender(<LanguageContext.Provider value="zh"><SlackDirectoryPicker /></LanguageContext.Provider>);
    await screen.findByText("频道（@你 才建卡）");
    expect(fetchSlackDirectory).toHaveBeenCalledTimes(1);
    // 下一次手点才带新语言
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(fetchSlackDirectory).toHaveBeenLastCalledWith(true, "zh"));
  });
});
