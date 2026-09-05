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
    fields: [field("updates_check_enabled", "自动检查新版本", "Check for updates automatically")] },
  { id: "gmail", title: { zh: "Gmail 接入", en: "Gmail" }, help: { zh: "", en: "" },
    fields: [field("gmail_enabled", "启用 Gmail 雷达", "Enable the Gmail radar"),
      field("gmail_address", "Gmail 地址", "Gmail address", "string")] },
  { id: "digest", title: { zh: "每周摘要", en: "Weekly digest" }, help: { zh: "", en: "" },
    fields: [field("weekly_digest_enabled", "每周自动生成回顾卡", "Auto-generate a weekly recap")] },
] };
const secrets: SecretsStatus = { secrets: [
  { name: "gmail-app-password.txt", label: { zh: "Gmail 应用专用密码", en: "Gmail app password" }, present: true, verifiable: true, mtime: null },
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
  it("a zh secret-row label that only exists in the DOM surfaces the gmail section once the catalog lands, without another keystroke", async () => {
    const pendingCatalog = deferred<SettingsCatalog>();
    vi.mocked(fetchSettingsCatalog).mockReturnValue(pendingCatalog.promise);
    vi.mocked(fetchSecrets).mockResolvedValue(secrets);
    render(<LanguageContext.Provider value="en"><SettingsPage /></LanguageContext.Provider>);
    const input = screen.getByRole("searchbox") as HTMLInputElement;
    // 目录还没回来：gmail 区是占位（「Loading…」），干草里没有 密码 → 藏
    act(() => { fireEvent.change(input, { target: { value: "密码" } }); });
    expect(section("gmail").textContent).toContain("Loading…");
    expect(visible("gmail")).toBe(false);
    // 目录落地 → 草稿对齐 → 下一帧长出 gmail_address 后面的应用密码行（en UI 渲 "Gmail app password"，zh label 只在 secrets 快照里）
    await act(async () => { pendingCatalog.resolve(catalog); await pendingCatalog.promise; });
    await waitFor(() => expect(section("gmail").querySelector('[data-secret="gmail-app-password.txt"]')).not.toBeNull());
    // 没有再敲键：区块必须自己浮出来
    await waitFor(() => expect(visible("gmail")).toBe(true));
    expect(input.value).toBe("密码");
    expect(visible("general")).toBe(false);
    expect(visible("digest")).toBe(false);
    expect(screen.queryByText("No matching settings")).toBeNull();
  });

  it("a catalog field label that lands late surfaces its section too (haystack re-read when the catalog arrives)", async () => {
    const pendingCatalog = deferred<SettingsCatalog>();
    vi.mocked(fetchSettingsCatalog).mockReturnValue(pendingCatalog.promise);
    vi.mocked(fetchSecrets).mockResolvedValue(secrets);
    render(<LanguageContext.Provider value="zh"><SettingsPage /></LanguageContext.Provider>);
    const input = screen.getByRole("searchbox") as HTMLInputElement;
    // 「回顾卡」只在目录 field 的 zh label 里（目录标题是「每周摘要 / Weekly digest」）——目录没到之前整页无匹配
    act(() => { fireEvent.change(input, { target: { value: "回顾卡" } }); });
    expect(visible("digest")).toBe(false);
    expect(screen.getByText("无匹配设置")).toBeTruthy();
    await act(async () => { pendingCatalog.resolve(catalog); await pendingCatalog.promise; });
    await waitFor(() => expect(visible("digest")).toBe(true));
    expect(visible("gmail")).toBe(false);
    expect(screen.queryByText("无匹配设置")).toBeNull();
  });
});
