// 左侧导航栏判例（CONTRACT §54.4）：v2.0.0 栏上四项同序同名（会议纪要 / 录制与导入 / 设置 / 关于，zh / en）+
// data-rail-item 锚、选中态跟 ?page=（deps / diagnostics 旧深链归设置）、深链正确、折叠持久化到 localStorage
// `sidebarCollapsed`（原生 UserDefaults 同名）、收起态只剩图标 + tooltip、⌘1…⌘4 换页、宽度钳制 160–320、
// mainSection 冷启动回跳（退役 slug 查不到就不导航）。任务台 / 回收站 / 永久性完成 / 技能随 Act 管线退役（全文见 tag full）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { navigate } from "../../route";
import { activeRailSlug, clampSidebarWidth, NavRail, RAIL_ORDER, RAIL_PAGE, rememberMainSection, restoreMainSection } from "./NavRail";

vi.mock("../../route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../route")>();
  return { ...actual, navigate: vi.fn() };
});

function renderRail(language: "zh" | "en" = "en", search = "") {
  window.history.replaceState(null, "", `/${search}`);
  return render(
    <LanguageContext.Provider value={language}>
      <NavRail />
    </LanguageContext.Provider>,
  );
}

const RAIL = [
  { slug: "recaps", zh: "会议纪要", en: "Recaps" },
  { slug: "ingest", zh: "录制与导入", en: "Recording & import" },
  { slug: "settings", zh: "设置", en: "Settings" },
  { slug: "about", zh: "关于", en: "About" },
];

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(cleanup);

describe("NavRail — 栏上四项", () => {
  it("四个条目按栏序渲染，data-rail-item 与双语标题逐字；退役页不上栏", () => {
    renderRail("en");
    const items = Array.from(document.querySelectorAll<HTMLAnchorElement>("[data-rail-item]"));
    expect(items.map((el) => el.dataset.railItem)).toEqual(RAIL.map((r) => r.slug));
    expect(items.map((el) => el.querySelector(".rail-label")?.textContent)).toEqual(RAIL.map((r) => r.en));
    expect(document.querySelectorAll(".rail-item")).toHaveLength(4); // 没有不带锚的 web 自有项了
    expect(document.querySelector('[data-rail="left"]')).toBeTruthy();
    for (const retired of ["dashboard", "trash", "archive", "skills", "ask", "deps"]) {
      expect(document.querySelector(`[data-rail-item="${retired}"]`)).toBeNull();
    }
    expect(document.querySelector("[data-rail-extra]")).toBeNull();
    cleanup();
    renderRail("zh");
    const zh = Array.from(document.querySelectorAll<HTMLAnchorElement>("[data-rail-item] .rail-label"));
    expect(zh.map((el) => el.textContent)).toEqual(RAIL.map((r) => r.zh));
  });

  it("深链：会议纪要 → 无 page 参数（缺省页），其余三项 → ?page=<slug>", () => {
    renderRail();
    const href = (slug: string) => new URL((document.querySelector(`[data-rail-item="${slug}"]`) as HTMLAnchorElement).href);
    expect(href("recaps").searchParams.get("page")).toBeNull();
    expect(href("ingest").searchParams.get("page")).toBe("ingest");
    expect(href("settings").searchParams.get("page")).toBe("settings");
    expect(href("about").searchParams.get("page")).toBe("about");
  });

  it("选中态跟 ?page=：缺省点亮会议纪要，deps / diagnostics 旧深链点亮设置（D30），permissions / setup 不点亮任何项", () => {
    renderRail("en");
    expect(document.querySelector('[data-rail-item="recaps"]')?.getAttribute("aria-current")).toBe("page");
    cleanup();
    renderRail("en", "?page=settings");
    expect(document.querySelector('[data-rail-item="settings"]')?.getAttribute("aria-current")).toBe("page");
    expect(document.querySelector('[data-rail-item="recaps"]')?.getAttribute("aria-current")).toBeNull();
    cleanup();
    renderRail("en", "?page=deps");
    expect(document.querySelector('[data-rail-item="settings"]')?.getAttribute("aria-current")).toBe("page");
    expect(activeRailSlug("recaps")).toBe("recaps");
    expect(activeRailSlug("diagnostics")).toBe("settings");
    expect(activeRailSlug("deps")).toBe("settings");
    expect(activeRailSlug("permissions")).toBeNull();
    expect(activeRailSlug("setup")).toBeNull();
    expect(Object.keys(RAIL_PAGE)).toEqual(["recaps", "ingest", "settings", "about"]);
  });

  it("折叠：按钮翻转 sidebarCollapsed 并持久化；收起态只剩图标，tooltip 仍是双语标题", () => {
    renderRail("en");
    expect(document.querySelector(".rail")?.classList.contains("is-collapsed")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Collapse/expand sidebar" }));
    expect(window.localStorage.getItem("sidebarCollapsed")).toBe("true");
    expect(document.querySelector(".rail")?.classList.contains("is-collapsed")).toBe(true);
    expect(document.querySelectorAll(".rail-label").length).toBe(0);
    expect(document.querySelector('[data-rail-item="recaps"]')?.getAttribute("title")).toBe("Recaps (⌘1)");
    expect(document.querySelector('[data-rail-item="about"]')?.getAttribute("title")).toBe("About (⌘4)");
    cleanup();
    // 重开：读回持久化的收起态
    renderRail("zh");
    expect(document.querySelector(".rail")?.classList.contains("is-collapsed")).toBe(true);
    expect(document.querySelector('[data-rail-item="ingest"]')?.getAttribute("title")).toBe("录制与导入 (⌘2)");
  });

  it("⌘1…⌘4 换页（与栏序同）；⌘5 / ⌘L 不归我们；输入框里不劫持", () => {
    const nav = vi.mocked(navigate);
    nav.mockReset();
    renderRail();
    const titles = Array.from(document.querySelectorAll<HTMLAnchorElement>(".rail-item")).map((el) => el.getAttribute("title"));
    expect(titles).toEqual(["Recaps (⌘1)", "Recording & import (⌘2)", "Settings (⌘3)", "About (⌘4)"]);
    expect(RAIL_ORDER).toEqual(["recaps", "ingest", "settings", "about"]);
    fireEvent.keyDown(window, { key: "3", metaKey: true });
    expect(nav).toHaveBeenCalledTimes(1);
    expect(new URL(String(nav.mock.calls[0][0])).searchParams.get("page")).toBe("settings");
    fireEvent.keyDown(window, { key: "1", metaKey: true });
    expect(nav).toHaveBeenCalledTimes(2);
    expect(new URL(String(nav.mock.calls[1][0])).searchParams.get("page")).toBeNull();
    fireEvent.keyDown(window, { key: "4", metaKey: true });
    expect(new URL(String(nav.mock.calls[2][0])).searchParams.get("page")).toBe("about");
    const input = document.createElement("input");
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: "2", metaKey: true });
    expect(nav).toHaveBeenCalledTimes(3);
    fireEvent.keyDown(window, { key: "5", metaKey: true });
    fireEvent.keyDown(window, { key: "l", metaKey: true }); // 退役的 ⌘L 聚焦捕获框（§54.4）
    expect(nav).toHaveBeenCalledTimes(3);
  });

  it("侧栏宽度钳制在 160–320（原生 clampSidebar）", () => {
    expect(clampSidebarWidth(100)).toBe(160);
    expect(clampSidebarWidth(200)).toBe(200);
    expect(clampSidebarWidth(900)).toBe(320);
  });

  it("mainSection：记住上次的 rail 页（原生 UserDefaults 同名），冷启动且 URL 没指定去处时回到它", () => {
    rememberMainSection("ingest");
    expect(window.localStorage.getItem("mainSection")).toBe("ingest");
    rememberMainSection("diagnostics"); // deps / diagnostics 旧深链归设置（D30）
    expect(window.localStorage.getItem("mainSection")).toBe("settings");
    rememberMainSection("permissions"); // 非 rail 页不记
    expect(window.localStorage.getItem("mainSection")).toBe("settings");
    // 冷启动：无 ?page= → 回上次的页
    expect(restoreMainSection("")).toBe("settings");
    // 同一窗口会话里再整页加载不再跳
    expect(restoreMainSection("")).toBeNull();
    window.sessionStorage.clear();
    // URL 指定了去处 → 尊重 URL
    expect(restoreMainSection("?page=about")).toBeNull();
    window.sessionStorage.clear();
    // 上次在会议纪要（缺省页）→ 不导航；坏值 → 不导航
    rememberMainSection("recaps");
    expect(restoreMainSection("")).toBeNull();
    window.sessionStorage.clear();
    window.localStorage.setItem("mainSection", "nowhere");
    expect(restoreMainSection("")).toBeNull();
    window.sessionStorage.clear();
    // 1.x 残留的退役 slug（任务台 / 回收站 / 永久性完成 / 技能 / 问问助手 / 依赖检查）→ 查不到页，不导航、不崩
    for (const stale of ["dashboard", "trash", "archive", "skills", "ask", "deps"]) {
      window.localStorage.setItem("mainSection", stale);
      expect(restoreMainSection("")).toBeNull();
      window.sessionStorage.clear();
    }
    window.localStorage.removeItem("mainSection");
    expect(restoreMainSection("")).toBeNull();
  });
});
