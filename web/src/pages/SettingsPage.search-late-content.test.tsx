// 设置搜索遇上晚到的区块正文（CONTRACT §54.4 / §68.1 追记）：原生 SwiftUI 每次 body 重算都重跑 matches()，所以「先打字、后到数据」
// 的区块会在数据到达那一拍自己浮出来。web 的目录区（CatalogSection）是草稿对齐 effect 之后的**下一帧**才渲 field 与凭证行——
// store 拿到目录的那一次提交里，区块正文还是「读取中…」占位、data-secret 还不在 DOM，只在那一拍过滤一次就会把它错藏到下一次击键。
// 本判例钉：目录 / 凭证快照在查询已经输入之后才落地，命中的区块必须不靠再敲一个键就自己显示出来（含只在 DOM 里的凭证行双语 label）。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSecrets, fetchSettingsCatalog } from "../api";
import { LanguageContext } from "../i18n";
import { resetStoreForTests } from "../store";
import type { SecretsStatus, SettingsCatalog, SettingsField } from "../types";
import { SettingsPage } from "./SettingsPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  const mocked: Record<string, unknown> = { ...actual };
  for (const key of Object.keys(actual)) {
    if (/^(fetch|post|put|verify)/.test(key)) mocked[key] = vi.fn().mockRejectedValue(new Error(`${key}: not stubbed here`));
  }
  return mocked;
});

const field = (key: string, zh: string, en: string, kind = "bool"): SettingsField => ({
  key, kind, label: { zh, en }, help: { zh: "", en: "" }, default: kind === "bool" ? false : "", choices: null,
  effective: kind === "bool" ? false : "", source: "default",
});
const catalog: SettingsCatalog = { sections: [
  { id: "general", title: { zh: "通用", en: "General" }, help: { zh: "", en: "" },
    fields: [field("language", "界面语言", "Interface language", "string")] },
  { id: "redaction", title: { zh: "脱敏（发给 AI 前本地打码）", en: "Redaction (local masking before sending to AI)" }, help: { zh: "", en: "" },
    fields: [field("redaction_enabled", "启用词表脱敏", "Enable term-list redaction")] },
  { id: "updates", title: { zh: "更新", en: "Updates" }, help: { zh: "", en: "" },
    fields: [field("updates_check_enabled", "自动检查新版本", "Check for updates automatically")] },
] };
const secrets: SecretsStatus = { secrets: [
  { name: "anthropic-api-key.txt", label: { zh: "Anthropic API 密钥", en: "Anthropic API key" }, present: true, verifiable: true, mtime: null },
] };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

function section(id: string): HTMLElement {
  const el = document.getElementById(`settings-${id}`);
  if (!el) throw new Error(`settings-${id} not rendered`);
  return el;
}
const visible = (id: string) => !section(id).hidden;

beforeEach(() => {
  resetStoreForTests();
  window.history.replaceState(null, "", "/?page=settings");
});
afterEach(cleanup);

describe("settings search — sections whose content lands after the query was typed", () => {
  it("a zh secret-row label that only exists in the snapshot surfaces the credentials section once secrets land, without another keystroke", async () => {
    const pendingSecrets = deferred<SecretsStatus>();
    vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog);
    vi.mocked(fetchSecrets).mockReturnValue(pendingSecrets.promise);
    render(<LanguageContext.Provider value="en"><SettingsPage /></LanguageContext.Provider>);
    const input = screen.getByRole("searchbox") as HTMLInputElement;
    // 凭证快照还没回来：en UI 渲的是 "Anthropic API key"，干草里没有 密钥 → 藏
    act(() => { fireEvent.change(input, { target: { value: "密钥" } }); });
    expect(visible("credentials")).toBe(false);
    // 凭证快照落地 → data-secret 的行带着 zh label 进干草
    await act(async () => { pendingSecrets.resolve(secrets); await pendingSecrets.promise; });
    // 没有再敲键：区块必须自己浮出来
    await waitFor(() => expect(visible("credentials")).toBe(true));
    expect(input.value).toBe("密钥");
    expect(visible("general")).toBe(false);
    expect(visible("updates")).toBe(false);
    expect(screen.queryByText("No matching settings")).toBeNull();
  });

  it("a catalog field label that lands late surfaces its section too (haystack re-read when the catalog arrives)", async () => {
    const pendingCatalog = deferred<SettingsCatalog>();
    vi.mocked(fetchSettingsCatalog).mockReturnValue(pendingCatalog.promise);
    vi.mocked(fetchSecrets).mockResolvedValue(secrets);
    render(<LanguageContext.Provider value="zh"><SettingsPage /></LanguageContext.Provider>);
    const input = screen.getByRole("searchbox") as HTMLInputElement;
    // 「词表」只在目录 field 的 zh label 里（目录标题是「脱敏（发给 AI 前本地打码）」）——目录没到之前整页无匹配
    act(() => { fireEvent.change(input, { target: { value: "词表" } }); });
    expect(section("redaction").textContent).toContain("读取中…");
    expect(visible("redaction")).toBe(false);
    expect(screen.getByText("无匹配设置")).toBeTruthy();
    await act(async () => { pendingCatalog.resolve(catalog); await pendingCatalog.promise; });
    await waitFor(() => expect(visible("redaction")).toBe(true));
    expect(visible("updates")).toBe(false);
    expect(screen.queryByText("无匹配设置")).toBeNull();
  });
});
