// 设置搜索的原生语义（CONTRACT §54.4；原生 Settings.swift matches() + SettingsSearchField.esc）：
// 干草 = 目录标题 zh+en + server 目录该区 label / help zh+en（不看 UI 语言）+ 该区里渲染着的凭证行双语 label（落点由 DOM 的
// data-secret 说：Anthropic 密钥住凭证区）+ 渲染正文；
// 查询按空白切 token、全部命中才算（AND）；大小写 / 变音符折叠；Esc 第一下清空、第二下交还光标；输入法候选期间 Esc 不拦。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSecrets, fetchSettingsCatalog } from "../api";
import { LanguageContext, type Language } from "../i18n";
import { resetStoreForTests } from "../store";
import type { SecretsStatus, SettingsCatalog, SettingsField } from "../types";
import { foldSearchText, matchesSearch, sectionHaystack, SettingsPage } from "./SettingsPage";

// 设置页会挂十来个区、各自拉自己的快照——除了目录与凭证两份，其余读写一律立刻拒绝（组件都有 catch / pageErrors 兜底），
// 本判例只看搜索框怎么过滤区块
vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  const mocked: Record<string, unknown> = { ...actual };
  for (const key of Object.keys(actual)) {
    if (/^(fetch|post|put|verify)/.test(key)) mocked[key] = vi.fn().mockRejectedValue(new Error(`${key}: not stubbed here`));
  }
  return mocked;
});

const field = (key: string, zh: string, en: string, helpZh = "", helpEn = "", kind = "bool"): SettingsField => ({
  key, kind, label: { zh, en }, help: { zh: helpZh, en: helpEn }, default: kind === "bool" ? false : "", choices: null,
  effective: kind === "bool" ? false : "", source: "default",
});
const catalog: SettingsCatalog = { sections: [
  { id: "general", title: { zh: "通用", en: "General" }, help: { zh: "", en: "" },
    fields: [field("language", "界面语言", "Interface language", "", "", "string")] },
  { id: "redaction", title: { zh: "脱敏（发给 AI 前本地打码）", en: "Redaction (local masking before sending to AI)" }, help: { zh: "", en: "" },
    fields: [field("redaction_enabled", "启用词表脱敏", "Enable term-list redaction", "打开会改变 AI 看到的内容。", "Changes what the AI sees.")] },
  { id: "updates", title: { zh: "更新", en: "Updates" }, help: { zh: "", en: "" },
    fields: [field("updates_check_enabled", "自动检查新版本（每天最多一次）", "Check for updates automatically (at most once a day)")] },
  { id: "notifications", title: { zh: "通知", en: "Notifications" }, help: { zh: "", en: "" },
    fields: [field("quiet_hours_enabled", "安静时段", "Quiet hours")] },
] };
const secrets: SecretsStatus = { secrets: [
  { name: "anthropic-api-key.txt", label: { zh: "Anthropic API 密钥", en: "Anthropic API key" }, present: true, verifiable: true, mtime: null },
] };

function section(id: string): HTMLElement {
  const el = document.getElementById(`settings-${id}`);
  if (!el) throw new Error(`settings-${id} not rendered`);
  return el;
}
const visible = (id: string) => !section(id).hidden;

async function renderSettings(language: Language) {
  window.history.replaceState(null, "", "/?page=settings");
  const view = render(<LanguageContext.Provider value={language}><SettingsPage /></LanguageContext.Provider>);
  // 目录 / 凭证快照落地后干草才有双语 label——等脱敏区从占位长出 field、凭证区长出 Anthropic 行（SecretRow 的 data-secret）
  await waitFor(() => expect(section("redaction").textContent).toContain(language === "zh" ? "启用词表脱敏" : "Enable term-list redaction"));
  await waitFor(() => expect(section("credentials").querySelector('[data-secret="anthropic-api-key.txt"]')).not.toBeNull());
  const input = screen.getByRole("searchbox") as HTMLInputElement;
  return { view, input };
}

function search(input: HTMLInputElement, value: string) {
  act(() => { fireEvent.change(input, { target: { value } }); });
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog);
  vi.mocked(fetchSecrets).mockResolvedValue(secrets);
});
afterEach(cleanup);

describe("settings search — bilingual AND-token match (native matches())", () => {
  it("an English query on the Chinese UI hits through the en half of the haystack", async () => {
    const { input } = await renderSettings("zh");
    expect(visible("general") && visible("updates") && visible("redaction")).toBe(true);
    search(input, "term list");
    expect(visible("redaction")).toBe(true);
    expect(visible("general")).toBe(false);
    expect(visible("updates")).toBe(false);
    // 目录 field 的 en label 也算（原生 keywords 是双语 blob）
    search(input, "once a day");
    expect(visible("updates")).toBe(true);
    expect(visible("redaction")).toBe(false);
    expect(screen.queryByText("无匹配设置")).toBeNull();
  });

  it("a Chinese query on the English UI hits the zh half — both languages regardless of UI language", async () => {
    const { input } = await renderSettings("en");
    search(input, "词表");
    expect(visible("redaction")).toBe(true);
    expect(visible("general")).toBe(false);
    search(input, "密钥");
    expect(visible("credentials")).toBe(true);   // 凭证行 zh label 只在 secrets 快照里（en UI 渲的是 "Anthropic API key"）
    expect(visible("redaction")).toBe(false);
  });

  it("'anthropic 密钥' — whitespace tokens with AND semantics: only sections carrying both tokens stay", async () => {
    const { input } = await renderSettings("zh");
    search(input, "anthropic 密钥");
    expect(visible("credentials")).toBe(true);
    expect(visible("general")).toBe(false);
    expect(visible("updates")).toBe(false);
    expect(screen.queryByText("无匹配设置")).toBeNull();
    // 一个 token 命不中 → 整页无匹配
    search(input, "anthropic zzz-nothing");
    expect(visible("credentials")).toBe(false);
    expect(screen.getByText("无匹配设置")).toBeTruthy();
    // 清空 → 全部回来
    search(input, "   ");
    expect(visible("redaction") && visible("updates") && visible("credentials")).toBe(true);
  });

  it("Escape is two-stage: first clears the query (caret stays), second releases the caret; IME Esc passes through", async () => {
    const { input } = await renderSettings("zh");
    input.focus();
    search(input, "REDACTION");
    expect(visible("redaction")).toBe(true);
    expect(visible("updates")).toBe(false);
    // 输入法候选期间的 Esc 归输入法：查询与焦点都不动
    fireEvent.keyDown(input, { key: "Escape", isComposing: true });
    expect(input.value).toBe("REDACTION");
    expect(document.activeElement).toBe(input);
    // 第一下：清空、焦点留着、区块全部回来
    fireEvent.keyDown(input, { key: "Escape" });
    await waitFor(() => expect(input.value).toBe(""));
    expect(document.activeElement).toBe(input);
    expect(visible("updates")).toBe(true);
    // 第二下：交还光标
    fireEvent.keyDown(input, { key: "Escape" });
    expect(document.activeElement).not.toBe(input);
  });
});

describe("matchesSearch / foldSearchText / sectionHaystack (pure)", () => {
  it("folds case and diacritics on both sides, splits on any whitespace, requires every token", () => {
    expect(foldSearchText("Crème BRÛLÉE")).toBe("creme brulee");
    expect(matchesSearch("Crème brûlée recipe", "creme  RECIPE")).toBe(true);
    expect(matchesSearch("Anthropic API 密钥", "anthropic 密钥")).toBe(true);
    expect(matchesSearch("Anthropic API key", "anthropic 密钥")).toBe(false);
    expect(matchesSearch("anything", "")).toBe(true);
    expect(matchesSearch("anything", " \t\n")).toBe(true);
    expect(matchesSearch("", "x")).toBe(false);
  });

  it("the haystack carries both languages of the TOC title, the catalog section and the secret rows that live in the section", () => {
    const updates = sectionHaystack("updates", "rendered text", catalog, secrets);
    expect(updates).toContain("更新");
    expect(updates).toContain("Updates");
    expect(updates).toContain("at most once a day");
    expect(updates).toContain("rendered text");
    expect(updates).not.toContain("Anthropic API key");
    const credentials = sectionHaystack("credentials", "", catalog, secrets, ["anthropic-api-key.txt"]);
    expect(credentials).toContain("Anthropic API key");
    expect(credentials).toContain("Anthropic API 密钥");
    expect(credentials).toContain("Credentials (stored locally");
    // 没有目录 / 凭证快照时只剩目录标题 + 正文，不抛
    expect(sectionHaystack("display", "字号", null, null)).toBe("显示 Display 字号");
    expect(sectionHaystack("no-such-section", "", null, null)).toBe("");
  });
});
