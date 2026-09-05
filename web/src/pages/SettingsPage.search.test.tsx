// 设置搜索的原生语义（CONTRACT §54.4；原生 Settings.swift matches() + SettingsSearchField.esc）：
// 干草 = 目录标题 zh+en + server 目录该区 label / help zh+en（不看 UI 语言）+ 该区里渲染着的凭证行双语 label（落点由 DOM 的
// data-secret 说：Gmail 应用专用密码住 Gmail 接入区，Anthropic 密钥住凭证区）+ 渲染正文；
// 查询按空白切 token、全部命中才算（AND）；大小写 / 变音符折叠；Esc 第一下清空、第二下交还光标；输入法候选期间 Esc 不拦。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSecrets, fetchSettingsCatalog } from "../api";
import { LanguageContext, type Language } from "../i18n";
import { resetStoreForTests } from "../store";
import type { SecretsStatus, SettingsCatalog, SettingsField } from "../types";
import { foldSearchText, matchesSearch, sectionHaystack, SettingsPage } from "./SettingsPage";

// 设置页会挂二十来个区、各自拉自己的快照——除了目录与凭证两份，其余读写一律立刻拒绝（组件都有 catch / pageErrors 兜底），
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
    fields: [field("updates_check_enabled", "自动检查新版本", "Check for updates automatically")] },
  { id: "gmail", title: { zh: "Gmail 接入", en: "Gmail" }, help: { zh: "", en: "" },
    fields: [field("gmail_enabled", "启用 Gmail 雷达", "Enable the Gmail radar", "只读收件箱，未读邮件成提案卡。", "Read-only inbox; unread mail becomes proposal cards."),
      // 应用密码的 SecretRow 夹在 gmail_address 之后（GmailSection between）——没有这个 field 它不渲
      field("gmail_address", "Gmail 地址", "Gmail address", "", "", "string")] },
  { id: "flags", title: { zh: "Feature flags（§16，默认全开）", en: "Feature flags (§16, all on by default)" }, help: { zh: "", en: "" },
    fields: [field("features.gmail_radar", "Gmail 雷达", "Gmail radar")] },
  { id: "digest", title: { zh: "每周摘要", en: "Weekly digest" }, help: { zh: "", en: "" },
    fields: [field("weekly_digest_enabled", "每周自动生成「本周你都在忙什么」回顾卡（默认关）", "Auto-generate a weekly \"what you were up to\" recap (default off)")] },
] };
const secrets: SecretsStatus = { secrets: [
  { name: "anthropic-api-key.txt", label: { zh: "Anthropic API 密钥", en: "Anthropic API key" }, present: true, verifiable: true, mtime: null },
  { name: "gmail-app-password.txt", label: { zh: "Gmail 应用专用密码", en: "Gmail app password" }, present: true, verifiable: true, mtime: null },
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
  // 目录 / 凭证快照落地后干草才有双语 label——等 gmail 区从占位长出 field 与应用密码行（SecretRow 的 data-secret）
  await waitFor(() => expect(section("gmail").textContent).toContain(language === "zh" ? "启用 Gmail 雷达" : "Enable the Gmail radar"));
  await waitFor(() => expect(section("gmail").querySelector('[data-secret="gmail-app-password.txt"]')).not.toBeNull());
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
    expect(visible("general") && visible("digest") && visible("gmail")).toBe(true);
    search(input, "weekly digest");
    expect(visible("digest")).toBe(true);
    expect(visible("general")).toBe(false);
    expect(visible("gmail")).toBe(false);
    // 目录 field 的 en label 也算（原生 keywords 是双语 blob）
    search(input, "Recap");
    expect(visible("digest")).toBe(true);
    expect(visible("general")).toBe(false);
    expect(screen.queryByText("无匹配设置")).toBeNull();
  });

  it("a Chinese query on the English UI hits the zh half — both languages regardless of UI language", async () => {
    const { input } = await renderSettings("en");
    search(input, "每周摘要");
    expect(visible("digest")).toBe(true);
    expect(visible("general")).toBe(false);
    search(input, "密码");
    expect(visible("gmail")).toBe(true);         // Gmail 应用专用密码 行住这里（en UI 渲的是 "Gmail app password"）
    expect(visible("credentials")).toBe(false);  // 凭证区只有 Anthropic 那一行
    search(input, "密钥");
    expect(visible("credentials")).toBe(true);
    expect(visible("gmail")).toBe(false);
  });

  it("'gmail 密码' — whitespace tokens with AND semantics: only sections carrying both tokens stay", async () => {
    const { input } = await renderSettings("zh");
    search(input, "gmail 密码");
    expect(visible("gmail")).toBe(true);         // 凭证行 label「Gmail 应用专用密码」两词都在
    expect(visible("flags")).toBe(false);        // 只有 gmail 没有 密码
    expect(visible("credentials")).toBe(true);   // 凭证区 zh 正文「Slack token 与 Gmail 密码在各自接入区」两词也都在——渲染正文照旧算
    expect(visible("general")).toBe(false);
    expect(visible("digest")).toBe(false);
    expect(screen.queryByText("无匹配设置")).toBeNull();
    // 一个 token 命不中 → 整页无匹配
    search(input, "gmail zzz-nothing");
    expect(visible("credentials")).toBe(false);
    expect(visible("gmail")).toBe(false);
    expect(screen.getByText("无匹配设置")).toBeTruthy();
    // 清空 → 全部回来
    search(input, "   ");
    expect(visible("gmail") && visible("flags") && visible("credentials")).toBe(true);
  });

  it("Escape is two-stage: first clears the query (caret stays), second releases the caret; IME Esc passes through", async () => {
    const { input } = await renderSettings("zh");
    input.focus();
    search(input, "GMAIL");
    expect(visible("gmail")).toBe(true);
    expect(visible("digest")).toBe(false);
    // 输入法候选期间的 Esc 归输入法：查询与焦点都不动
    fireEvent.keyDown(input, { key: "Escape", isComposing: true });
    expect(input.value).toBe("GMAIL");
    expect(document.activeElement).toBe(input);
    // 第一下：清空、焦点留着、区块全部回来
    fireEvent.keyDown(input, { key: "Escape" });
    await waitFor(() => expect(input.value).toBe(""));
    expect(document.activeElement).toBe(input);
    expect(visible("digest")).toBe(true);
    // 第二下：交还光标
    fireEvent.keyDown(input, { key: "Escape" });
    expect(document.activeElement).not.toBe(input);
  });
});

describe("matchesSearch / foldSearchText / sectionHaystack (pure)", () => {
  it("folds case and diacritics on both sides, splits on any whitespace, requires every token", () => {
    expect(foldSearchText("Crème BRÛLÉE")).toBe("creme brulee");
    expect(matchesSearch("Crème brûlée recipe", "creme  RECIPE")).toBe(true);
    expect(matchesSearch("Gmail 应用专用密码", "gmail 密码")).toBe(true);
    expect(matchesSearch("Gmail 雷达", "gmail 密码")).toBe(false);
    expect(matchesSearch("anything", "")).toBe(true);
    expect(matchesSearch("anything", " \t\n")).toBe(true);
    expect(matchesSearch("", "x")).toBe(false);
  });

  it("the haystack carries both languages of the TOC title, the catalog section and the secret rows that live in the section", () => {
    const digest = sectionHaystack("digest", "rendered text", catalog, secrets);
    expect(digest).toContain("每周摘要");
    expect(digest).toContain("Weekly digest");
    expect(digest).toContain("Auto-generate a weekly");
    expect(digest).toContain("rendered text");
    expect(digest).not.toContain("Gmail app password");
    const gmail = sectionHaystack("gmail", "", catalog, secrets, ["gmail-app-password.txt"]);
    expect(gmail).toContain("Gmail app password");
    expect(gmail).toContain("Gmail 应用专用密码");
    expect(gmail).not.toContain("Anthropic API key");
    expect(sectionHaystack("credentials", "", catalog, secrets)).toContain("Credentials (stored locally");
    // 没有目录 / 凭证快照时只剩目录标题 + 正文，不抛
    expect(sectionHaystack("display", "字号", null, null)).toBe("显示 Display 字号");
    expect(sectionHaystack("no-such-section", "", null, null)).toBe("");
  });
});
