// Slack「监控范围」勾选器（CONTRACT §68.1 追记；原生 SettingsSlack pickers）：加载频道和成员 → 刷新（绕过缓存）/ 加载中…；
// 两张清单 + 筛选…；勾选写目录的 list 字段（slack_channels 存 id、watch_people 存 handle）；目录失败句（no_python 前缀独立节点）；
// 保存失败「保存设置失败: 」+ 原句。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSettingsCatalog, fetchSlackDirectory, putSettingsSection } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshSettingsCatalog, resetStoreForTests } from "../../store";
import type { SettingsCatalog, SlackDirectory } from "../../types";
import { SlackDirectoryPicker } from "./SlackDirectoryPicker";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSettingsCatalog: vi.fn(), fetchSlackDirectory: vi.fn(), putSettingsSection: vi.fn() };
});

const catalog = (channels: string[] = [], people: string[] = []): SettingsCatalog => ({ sections: [{
  id: "slack", title: { zh: "Slack 接入", en: "Slack" }, help: { zh: "", en: "" },
  fields: [
    { key: "slack_channels", kind: "list", label: { zh: "监控频道", en: "Watched channels" }, help: { zh: "", en: "" }, default: [], choices: null, effective: channels, source: "default" },
    { key: "watch_people", kind: "list", label: { zh: "关注的人", en: "People to watch" }, help: { zh: "", en: "" }, default: [], choices: null, effective: people, source: "default" },
  ],
}] });
const directory: SlackDirectory = { ok: true, fetched_at: "2026-09-03T00:00:00Z",
  channels: [{ id: "C1", name: "eng" }, { id: "C2", name: "random" }],
  users: [{ id: "U1", name: "sam.rivera", real_name: "Sam Rivera" }, { id: "U2", name: "lee", real_name: "" }] };

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

beforeEach(async () => {
  resetStoreForTests();
  vi.mocked(fetchSlackDirectory).mockReset();
  vi.mocked(putSettingsSection).mockReset();
  vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog(["C2"], ["lee"]));
  await refreshSettingsCatalog();
});
afterEach(cleanup);

describe("SlackDirectoryPicker", () => {
  it("loads once (no refresh), lists channels/people with the selection floated up, then Refresh bypasses the cache", async () => {
    vi.mocked(fetchSlackDirectory).mockResolvedValue(directory);
    renderEn(<SlackDirectoryPicker />);
    const load = screen.getByRole("button", { name: "Load channels & members" });
    fireEvent.click(load);
    expect(screen.getByRole("button").textContent).toBe("Loading…");
    await screen.findByText("Channels (card only when @mentioned)");
    expect(fetchSlackDirectory).toHaveBeenCalledWith(false, "en");
    const labels = Array.from(document.querySelectorAll(".slack-picker-list label")).map((l) => l.textContent);
    expect(labels).toEqual(["#random", "#eng", "@lee", "@sam.rivera（Sam Rivera）"]);   // 已勾的浮顶
    expect(screen.getAllByPlaceholderText("Filter…")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(fetchSlackDirectory).toHaveBeenLastCalledWith(true, "en"));
  });

  it("toggling writes the list fields through PUT /api/settings/slack (ids for channels, handles for people)", async () => {
    vi.mocked(fetchSlackDirectory).mockResolvedValue(directory);
    vi.mocked(putSettingsSection).mockResolvedValue(catalog(["C2", "C1"], ["lee"]).sections[0]);
    renderEn(<SlackDirectoryPicker />);
    fireEvent.click(screen.getByRole("button", { name: "Load channels & members" }));
    await screen.findByText("#eng");
    fireEvent.click(screen.getByLabelText("#eng"));
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledWith("slack", { slack_channels: ["C2", "C1"] }));
    fireEvent.click(screen.getByLabelText("@lee"));
    await waitFor(() => expect(putSettingsSection).toHaveBeenLastCalledWith("slack", { watch_people: [] }));
  });

  it("filters by name / real name and caps the list with the native overflow sentence", async () => {
    const many = { ...directory, users: Array.from({ length: 205 }, (_, i) => ({ id: `U${i}`, name: `user${i}`, real_name: "" })) };
    vi.mocked(fetchSlackDirectory).mockResolvedValue(many);
    renderEn(<SlackDirectoryPicker />);
    fireEvent.click(screen.getByRole("button", { name: "Load channels & members" }));
    await screen.findByText("5 more — narrow it down with the filter above");
    const filters = screen.getAllByPlaceholderText("Filter…");
    fireEvent.change(filters[0], { target: { value: "Sam" } });
    fireEvent.change(filters[1], { target: { value: "user20" } });
    const lists = document.querySelectorAll(".slack-picker-list");
    expect(lists[0].querySelectorAll("li")).toHaveLength(0);   // 频道里没有 Sam
    expect(lists[1].querySelectorAll("li")).toHaveLength(6);   // user20, user200..user204
  });

  it("shows the act-side message, the no_python prefix in its own node, and save failures with the native prefix", async () => {
    vi.mocked(fetchSlackDirectory).mockResolvedValue({ ok: false, error: "no_token", message: "先粘贴并保存 token", channels: [], users: [] });
    renderEn(<SlackDirectoryPicker />);
    fireEvent.click(screen.getByRole("button", { name: "Load channels & members" }));
    await screen.findByText("先粘贴并保存 token");
    vi.mocked(fetchSlackDirectory).mockResolvedValue({ ok: false, error: "no_python", message: "[Errno 2] no python3", channels: [], users: [] });
    fireEvent.click(screen.getByRole("button", { name: "Load channels & members" }));
    await screen.findByText("No usable python (");
    expect(screen.getByRole("alert").textContent).toBe("No usable python ([Errno 2] no python3)");
    vi.mocked(fetchSlackDirectory).mockResolvedValue(directory);
    vi.mocked(putSettingsSection).mockRejectedValue(new Error("overrides not writable"));
    fireEvent.click(screen.getByRole("button", { name: "Load channels & members" }));
    fireEvent.click(await screen.findByLabelText("#eng"));
    await screen.findByText("Failed to save settings:");
    expect(screen.getAllByRole("alert").map((a) => a.textContent)).toContain("Failed to save settings: overrides not writable");
  });
});
