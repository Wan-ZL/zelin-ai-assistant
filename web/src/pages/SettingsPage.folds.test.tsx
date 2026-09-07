// 设置页分区的混合式开合（CONTRACT §68.1 追记，D44；原生 Settings.swift SettingsCollapseStore + CollapsibleSection 的 web 版）：
// 默认展开 通用 / 依赖检查 / 录制 / 实时字幕、其余折叠；区头是 aria-expanded 按钮，正文折叠时 hidden 但**不卸载**（草稿留着）；
// 记忆 = localStorage settings.expandedSections（JSON 数组，记忆比默认大）；搜索命中强制展开、toggle 禁用、记忆不动；
// ?anchor= / #settings-<id> 深链与目录点击 expand 并记住；深链锚点只消费一次（挂载读完即从 URL 摘掉，rail 来回不重放）、
// 高亮落在 data-anchored（折着的目标 expand 重渲也不丢）；目录点击自己滚、不留 hash；目录条目 data-expanded 反映状态。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSecrets, fetchSettingsCatalog } from "../api";
import { LanguageContext } from "../i18n";
import { EXPANDED_SECTIONS_STORAGE_KEY } from "../settingsFolds";
import { resetStoreForTests } from "../store";
import type { SecretsStatus, SettingsCatalog, SettingsField } from "../types";
import { readHashSection, SETTINGS_TOC, SettingsPage } from "./SettingsPage";

// 设置页会挂二十来个区、各自拉自己的快照——除了目录与凭证两份，其余读写一律立刻拒绝（组件都有 catch / pageErrors 兜底）
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
    fields: [field("updates_check_enabled", "自动检查新版本", "Check for updates")] },
  { id: "gmail", title: { zh: "Gmail 接入", en: "Gmail" }, help: { zh: "", en: "" },
    fields: [field("gmail_enabled", "启用 Gmail 雷达", "Enable the Gmail radar"), field("gmail_address", "Gmail 地址", "Gmail address", "string")] },
  { id: "digest", title: { zh: "每周摘要", en: "Weekly digest" }, help: { zh: "", en: "" },
    fields: [field("weekly_digest_enabled", "每周自动生成回顾卡", "Auto-generate a weekly recap")] },
  { id: "flags", title: { zh: "Feature flags（§16，默认全开）", en: "Feature flags (§16, all on by default)" }, help: { zh: "", en: "" },
    fields: [field("features.gmail_radar", "Gmail 雷达", "Gmail radar")] },
] };
const secrets: SecretsStatus = { secrets: [
  { name: "gmail-app-password.txt", label: { zh: "Gmail 应用专用密码", en: "Gmail app password" }, present: true, verifiable: true, mtime: null },
] };

function fold(id: string): HTMLElement {
  const el = document.getElementById(`settings-${id}`);
  if (!el) throw new Error(`settings-${id} not rendered`);
  return el;
}
const toggle = (id: string) => fold(id).querySelector<HTMLButtonElement>(".settings-fold-toggle")!;
const body = (id: string) => document.getElementById(`settings-${id}-body`)!;
const isOpen = (id: string) => toggle(id).getAttribute("aria-expanded") === "true" && !body(id).hidden;
/** 折着 = 区头说折着 **且** 正文真的 hidden（只看 aria-expanded 抓不到丢了 `hidden={!open}` 的回归） */
const isClosed = (id: string) => toggle(id).getAttribute("aria-expanded") === "false" && body(id).hidden === true;
const tocEntry = (id: string) => document.querySelector<HTMLAnchorElement>(`.settings-toc a[href="#settings-${id}"]`)!;
const stored = (): string[] | null => {
  const raw = window.localStorage.getItem(EXPANDED_SECTIONS_STORAGE_KEY);
  return raw === null ? null : (JSON.parse(raw) as string[]);
};

async function renderSettings(url = "/?page=settings") {
  window.history.replaceState(null, "", url);
  resetStoreForTests(); // store 在 reset 时重读 localStorage 记忆（真实启动同一路径）
  const view = render(<LanguageContext.Provider value="en"><SettingsPage /></LanguageContext.Provider>);
  await waitFor(() => expect(fold("gmail").textContent).toContain("Enable the Gmail radar"));
  return view;
}

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog);
  vi.mocked(fetchSecrets).mockResolvedValue(secrets);
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

describe("settings folds — hybrid default, per-browser memory", () => {
  it("first run: general / deps / recording / live captions open, everything else collapsed; every section has a fold with an aria-expanded button", async () => {
    await renderSettings();
    for (const id of ["general", "deps", "recording", "live_captions"]) expect(isOpen(id)).toBe(true);
    for (const id of ["display", "models", "gmail", "flags", "digest", "voice", "daily_loop"]) expect(isClosed(id)).toBe(true);
    for (const entry of SETTINGS_TOC) {
      const button = toggle(entry.id);
      expect(button.tagName).toBe("BUTTON");
      expect(button.getAttribute("aria-controls")).toBe(`settings-${entry.id}-body`);
      expect(button.closest("h3")).not.toBeNull();
      expect(button.textContent).toContain(entry.en);
      expect(tocEntry(entry.id).dataset.expanded).toBe(String(isOpen(entry.id)));
    }
    expect(stored()).toBeNull(); // 只渲染不写：默认集不落 localStorage
  });

  it("a collapsed section stays mounted: its catalog fields, secret rows and drafts survive collapse → expand", async () => {
    await renderSettings();
    // gmail 折着，正文仍在 DOM（搜索干草 / 草稿都靠它）
    expect(isClosed("gmail")).toBe(true);
    expect(fold("gmail").querySelector('[data-secret="gmail-app-password.txt"]')).not.toBeNull();
    // 通用区改一格草稿 → 折 → 展：草稿与「1 unsaved」还在（D44 不改草稿 + 保存模型）
    fireEvent.click(screen.getByRole("switch", { name: "Check for updates" }));
    expect(fold("general").textContent).toContain("1 unsaved");
    fireEvent.click(toggle("general"));
    expect(isClosed("general")).toBe(true);
    fireEvent.click(toggle("general"));
    expect(isOpen("general")).toBe(true);
    expect((screen.getByRole("switch", { name: "Check for updates" }) as HTMLInputElement).checked).toBe(true);
    expect(fold("general").textContent).toContain("1 unsaved");
  });

  it("toggling writes the remembered set; a remount restores it and the TOC mirrors it", async () => {
    await renderSettings();
    fireEvent.click(toggle("gmail"));
    expect(isOpen("gmail")).toBe(true);
    expect(tocEntry("gmail").dataset.expanded).toBe("true");
    fireEvent.click(toggle("deps"));
    expect(isClosed("deps")).toBe(true);
    expect(tocEntry("deps").dataset.expanded).toBe("false");
    expect(stored()).toEqual(["general", "gmail", "live_captions", "recording"]);
    cleanup();
    await renderSettings();
    expect(isOpen("gmail")).toBe(true);
    expect(isClosed("deps")).toBe(true);
    expect(isOpen("general")).toBe(true);
  });

  it("an explicit empty memory means everything collapsed — the default seed only applies when nothing is stored", async () => {
    window.localStorage.setItem(EXPANDED_SECTIONS_STORAGE_KEY, "[]");
    await renderSettings();
    for (const entry of SETTINGS_TOC) expect(isClosed(entry.id)).toBe(true);
  });
});

describe("settings folds — search force-expands matches without touching the memory", () => {
  it("while a query is active every match is open and its toggle disabled; clearing restores the remembered state", async () => {
    await renderSettings();
    expect(isClosed("digest")).toBe(true);
    const input = screen.getByRole("searchbox") as HTMLInputElement;
    act(() => { fireEvent.change(input, { target: { value: "weekly digest" } }); });
    expect(fold("digest").hidden).toBe(false);
    expect(isOpen("digest")).toBe(true);
    expect(toggle("digest").disabled).toBe(true);
    expect(fold("general").hidden).toBe(true); // 没命中的区整壳藏起（含区头）
    expect(tocEntry("digest").dataset.expanded).toBe("true");
    expect(stored()).toBeNull(); // 强制展开不写记忆
    act(() => { fireEvent.change(input, { target: { value: "" } }); });
    expect(fold("general").hidden).toBe(false);
    expect(isClosed("digest")).toBe(true);
    expect(toggle("digest").disabled).toBe(false);
    expect(tocEntry("digest").dataset.expanded).toBe("false");
  });
});

describe("settings folds — deep links and the TOC force-expand and remember", () => {
  it("?anchor=<id> expands the target, persists it, flashes the fold shell and is consumed from the URL", async () => {
    await renderSettings("/?page=settings&anchor=gmail");
    expect(isOpen("gmail")).toBe(true);
    expect(stored()).toContain("gmail");
    expect(fold("gmail").hasAttribute("data-anchored")).toBe(true);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    expect(window.location.search).toBe("?page=settings"); // 锚点只消费一次：读完即摘掉，rail 来回不重放
    expect(window.location.hash).toBe("");
  });

  it("a collapsed target keeps its flash even though expanding it re-renders the shell's className (catalog still pending)", async () => {
    vi.mocked(fetchSettingsCatalog).mockReturnValue(new Promise(() => undefined)); // 目录永不到：只有挂载那一次 effect
    window.history.replaceState(null, "", "/?page=settings&anchor=gmail");
    resetStoreForTests();
    render(<LanguageContext.Provider value="en"><SettingsPage /></LanguageContext.Provider>);
    expect(isOpen("gmail")).toBe(true);
    expect(fold("gmail").hasAttribute("data-anchored")).toBe(true);
    expect(fold("gmail").className).toBe("settings-fold is-expanded");
  });

  it("?page=deps (legacy deep link) lands on an open deps section even when the memory had it collapsed", async () => {
    window.localStorage.setItem(EXPANDED_SECTIONS_STORAGE_KEY, "[]");
    await renderSettings("/?page=deps");
    expect(isOpen("deps")).toBe(true);
    expect(stored()).toEqual(["deps"]);
    expect(isClosed("general")).toBe(true);
    expect(window.location.search).toBe("?page=deps"); // ?page= 不动（rail 换页时 buildAppUrl 自己重写它）
  });

  it("#settings-<id> (§68.15 sync deep link) expands too and is consumed; unknown hashes are ignored", async () => {
    await renderSettings("/?page=settings#settings-sync");
    expect(isOpen("sync")).toBe(true);
    expect(stored()).toContain("sync");
    expect(window.location.hash).toBe("");
    expect(window.location.search).toBe("?page=settings");
    expect(readHashSection("#settings-sync")).toBe("sync");
    expect(readHashSection("#settings-search")).toBeNull();   // 不是分区的 id 不算（也不进记忆）
    expect(readHashSection("#settings-gmail-body")).toBeNull();
    expect(readHashSection("")).toBeNull();
  });

  it("a consumed deep link does not override the memory on the next visit: collapse the target, remount, still collapsed", async () => {
    window.localStorage.setItem(EXPANDED_SECTIONS_STORAGE_KEY, JSON.stringify(["general"]));
    await renderSettings("/?page=settings#settings-flags");
    expect(isOpen("flags")).toBe(true);
    expect(stored()).toEqual(["flags", "general"]);
    fireEvent.click(toggle("flags")); // 用户手动折回去
    expect(stored()).toEqual(["general"]);
    cleanup();
    await renderSettings(window.location.href); // rail 看板 → 设置：buildAppUrl 带着现在的 URL（锚点已摘）回来
    expect(isClosed("flags")).toBe(true);
    expect(stored()).toEqual(["general"]);
  });

  it("clicking a TOC entry expands its section, remembers it and scrolls to the shell itself (no hash left behind)", async () => {
    await renderSettings();
    expect(isClosed("flags")).toBe(true);
    vi.mocked(Element.prototype.scrollIntoView).mockClear();
    expect(fireEvent.click(tocEntry("flags"))).toBe(false); // preventDefault：浏览器不导航到 #settings-flags
    expect(isOpen("flags")).toBe(true);
    expect(stored()).toContain("flags");
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(window.location.hash).toBe("");
    // 再点已展开的条目：零动作（不折回去）
    fireEvent.click(tocEntry("flags"));
    expect(isOpen("flags")).toBe(true);
    // 带修饰键的点击交给浏览器（新标签里由片段深链自己展开），当前页不动
    expect(fireEvent.click(tocEntry("gmail"), { metaKey: true })).toBe(true);
    expect(isClosed("gmail")).toBe(true);
  });
});
