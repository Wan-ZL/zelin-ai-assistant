// 左侧导航栏判例（CONTRACT §54.4 / §66.2 rail:*）：六项同序同名（zh / en；原生八页去掉 D29 问问助手、D30 依赖检查——
// 清单里 owner=retired 的 rail 项不再上栏）+ data-rail-item 锚、web 自有的会议纪要紧跟任务台列第二且不带锚（D32，
// 分隔线退役）、选中态跟 ?page=（deps / diagnostics 旧深链归设置）、深链正确、折叠持久化到 localStorage
// `sidebarCollapsed`（原生 UserDefaults 同名）、收起态只剩图标 + tooltip、⌘1…⌘7 换页（连续重编）、宽度钳制 160–320。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { navigate } from "../../route";
import { activeRailSlug, clampSidebarWidth, NavRail, RAIL_PAGE, rememberMainSection, restoreMainSection } from "./NavRail";

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

// 原生清单经 import.meta.glob 读（不许静态 import 仓库根——install.sh 在 web/ 的仓外镜像里编，
// CONTRACT §56.5；tests/test_web_build_self_contained.py 钉），找不到即抛错。
const inventoryGlob = import.meta.glob("../../../../ui/parity/native-inventory.json", { eager: true, import: "default" });
const inventory = inventoryGlob["../../../../ui/parity/native-inventory.json"] as
  { rail: { items: Array<{ slug: string; zh: string; en: string; gated: boolean; owner: string }> } } | undefined;
if (!inventory) throw new Error("ui/parity/native-inventory.json not found — this suite runs from the repo's web/ dir");
// 栏上只放清单里仍判（gated）的项；ask / deps 在清单 attribution.rail_owner 里标 retired（D29 / D30）
const nativeRail = inventory.rail.items.filter((r) => r.gated);
const retiredRail = inventory.rail.items.filter((r) => !r.gated);

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(cleanup);

describe("NavRail — 原生 sidebar 的 web 落点", () => {
  it("六个条目按原生顺序渲染，data-rail-item 与双语标题逐字镜像清单；退役的 ask / deps 不上栏", () => {
    expect(nativeRail.map((r) => r.slug)).toEqual(["dashboard", "ingest", "trash", "archive", "settings", "about"]);
    expect(retiredRail.map((r) => [r.slug, r.owner])).toEqual([["ask", "retired"], ["deps", "retired"]]);
    renderRail("en");
    const items = Array.from(document.querySelectorAll<HTMLAnchorElement>("[data-rail-item]"));
    expect(items.map((el) => el.dataset.railItem)).toEqual(nativeRail.map((r) => r.slug));
    expect(items.map((el) => el.querySelector(".rail-label")?.textContent)).toEqual(nativeRail.map((r) => r.en));
    expect(document.querySelector('[data-rail="left"]')).toBeTruthy();
    expect(document.querySelector('[data-rail-item="ask"]')).toBeNull();
    expect(document.querySelector('[data-rail-item="deps"]')).toBeNull();
    cleanup();
    renderRail("zh");
    const zh = Array.from(document.querySelectorAll<HTMLAnchorElement>("[data-rail-item] .rail-label"));
    expect(zh.map((el) => el.textContent)).toEqual(nativeRail.map((r) => r.zh));
    expect(zh.map((el) => el.textContent)).not.toContain("问问助手");
  });

  it("会议纪要紧跟任务台列第二（D32）：web 自有页不带 data-rail-item、不算进原生六项；分隔线不再渲染", () => {
    renderRail("zh");
    const all = Array.from(document.querySelectorAll<HTMLAnchorElement>(".rail-item"));
    expect(all.map((el) => el.querySelector(".rail-label")?.textContent)).toEqual(
      ["任务台", "会议纪要", "录制与数据接入", "回收站", "永久性完成", "设置", "关于"],
    );
    const recaps = all[1];
    expect(recaps.dataset.railExtra).toBe("recaps");
    expect(recaps.hasAttribute("data-rail-item")).toBe(false);
    expect(new URL(recaps.href).searchParams.get("page")).toBe("recaps");
    expect(document.querySelector(".rail-divider")).toBeNull();
    // 原生六项（带锚的）相对顺序仍 = 清单顺序，探针 rail:order 读的就是这个
    expect(Array.from(document.querySelectorAll<HTMLElement>("[data-rail-item]")).map((el) => el.dataset.railItem))
      .toEqual(nativeRail.map((r) => r.slug));
    cleanup();
    renderRail("en", "?page=recaps");
    expect(document.querySelector('[data-rail-extra="recaps"]')?.getAttribute("aria-current")).toBe("page");
    expect(document.querySelector('[data-rail-item="dashboard"]')?.getAttribute("aria-current")).toBeNull();
    expect(activeRailSlug("recaps")).toBeNull(); // 不进 mainSection（原生 UserDefaults 没有这一页）
  });

  it("深链：回收站 → ?page=trash，设置 → ?page=settings，任务台 → 无 page 参数", () => {
    renderRail();
    const href = (slug: string) => new URL((document.querySelector(`[data-rail-item="${slug}"]`) as HTMLAnchorElement).href);
    expect(href("trash").searchParams.get("page")).toBe("trash");
    expect(href("settings").searchParams.get("page")).toBe("settings");
    expect(href("about").searchParams.get("page")).toBe("about");
    expect(href("dashboard").searchParams.get("page")).toBeNull();
  });

  it("选中态跟 ?page=：看板点亮任务台，deps / diagnostics 旧深链点亮设置（D30），permissions 不点亮任何项", () => {
    renderRail("en", "?page=settings");
    expect(document.querySelector('[data-rail-item="settings"]')?.getAttribute("aria-current")).toBe("page");
    expect(document.querySelector('[data-rail-item="dashboard"]')?.getAttribute("aria-current")).toBeNull();
    cleanup();
    renderRail("en", "?page=deps");
    expect(document.querySelector('[data-rail-item="settings"]')?.getAttribute("aria-current")).toBe("page");
    expect(activeRailSlug("board")).toBe("dashboard");
    expect(activeRailSlug("diagnostics")).toBe("settings");
    expect(activeRailSlug("deps")).toBe("settings");
    expect(activeRailSlug("permissions")).toBeNull();
    expect(RAIL_PAGE).not.toHaveProperty("ask");
    expect(RAIL_PAGE).not.toHaveProperty("deps");
  });

  it("折叠：按钮翻转 sidebarCollapsed 并持久化；收起态只剩图标，tooltip 仍是双语标题", () => {
    renderRail("en");
    expect(document.querySelector(".rail")?.classList.contains("is-collapsed")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Collapse/expand sidebar" }));
    expect(window.localStorage.getItem("sidebarCollapsed")).toBe("true");
    expect(document.querySelector(".rail")?.classList.contains("is-collapsed")).toBe(true);
    expect(document.querySelectorAll(".rail-label").length).toBe(0);
    expect(document.querySelector('[data-rail-item="trash"]')?.getAttribute("title")).toBe("Trash (⌘4)");
    expect(document.querySelector('[data-rail-extra="recaps"]')?.getAttribute("title")).toBe("Recaps (⌘2)");
    cleanup();
    // 重开：读回持久化的收起态
    renderRail("zh");
    expect(document.querySelector(".rail")?.classList.contains("is-collapsed")).toBe(true);
    expect(document.querySelector('[data-rail-item="trash"]')?.getAttribute("title")).toBe("回收站 (⌘4)");
    expect(document.querySelector('[data-rail-extra="recaps"]')?.getAttribute("title")).toBe("会议纪要 (⌘2)");
  });

  it("⌘1…⌘7 换页（原生 keyboardShortcut 连续重编，会议纪要占 ⌘2——D32）；⌘8 / ⌘9 没有页；输入框里不劫持", () => {
    const nav = vi.mocked(navigate);
    nav.mockReset();
    renderRail();
    const titles = Array.from(document.querySelectorAll<HTMLAnchorElement>(".rail-item")).map((el) => el.getAttribute("title"));
    expect(titles).toEqual([
      "Workbench (⌘1)", "Recaps (⌘2)", "Recording & Data Sources (⌘3)", "Trash (⌘4)", "Done for good (⌘5)", "Settings (⌘6)", "About (⌘7)",
    ]);
    fireEvent.keyDown(window, { key: "6", metaKey: true });
    expect(nav).toHaveBeenCalledTimes(1);
    expect(new URL(String(nav.mock.calls[0][0])).searchParams.get("page")).toBe("settings");
    fireEvent.keyDown(window, { key: "2", metaKey: true });
    expect(nav).toHaveBeenCalledTimes(2);
    expect(new URL(String(nav.mock.calls[1][0])).searchParams.get("page")).toBe("recaps");
    fireEvent.keyDown(window, { key: "3", metaKey: true });
    expect(nav).toHaveBeenCalledTimes(3);
    expect(new URL(String(nav.mock.calls[2][0])).searchParams.get("page")).toBe("ingest");
    fireEvent.keyDown(window, { key: "7", metaKey: true });
    expect(nav).toHaveBeenCalledTimes(4);
    expect(new URL(String(nav.mock.calls[3][0])).searchParams.get("page")).toBe("about");
    const input = document.createElement("input");
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: "4", metaKey: true });
    expect(nav).toHaveBeenCalledTimes(4);
    fireEvent.keyDown(window, { key: "8", metaKey: true });
    fireEvent.keyDown(window, { key: "9", metaKey: true });
    expect(nav).toHaveBeenCalledTimes(4);
  });

  it("侧栏宽度钳制在 160–320（原生 clampSidebar）", () => {
    expect(clampSidebarWidth(100)).toBe(160);
    expect(clampSidebarWidth(200)).toBe(200);
    expect(clampSidebarWidth(900)).toBe(320);
  });

  it("mainSection：记住上次的 rail 页（原生 UserDefaults 同名），冷启动且 URL 没指定去处时回到它", () => {
    rememberMainSection("trash");
    expect(window.localStorage.getItem("mainSection")).toBe("trash");
    rememberMainSection("diagnostics"); // deps / diagnostics 旧深链归设置（D30）
    expect(window.localStorage.getItem("mainSection")).toBe("settings");
    rememberMainSection("permissions"); // 非 rail 页不记
    expect(window.localStorage.getItem("mainSection")).toBe("settings");
    rememberMainSection("recaps"); // web 自有页（D32 列第二）也不记：原生 UserDefaults 没有这个值
    expect(window.localStorage.getItem("mainSection")).toBe("settings");
    // 冷启动：无 ?page= / ?card= → 回上次的页
    expect(restoreMainSection("")).toBe("settings");
    // 同一窗口会话里再整页加载（← 返回看板）不再跳
    expect(restoreMainSection("")).toBeNull();
    window.sessionStorage.clear();
    // URL 指定了去处 → 尊重 URL
    expect(restoreMainSection("?page=trash")).toBeNull();
    window.sessionStorage.clear();
    expect(restoreMainSection("?card=R-1")).toBeNull();
    window.sessionStorage.clear();
    // 上次在看板（dashboard 是缺省）→ 不导航；没记过 → 不导航；坏值 → 不导航
    rememberMainSection("board");
    expect(restoreMainSection("")).toBeNull();
    window.sessionStorage.clear();
    window.localStorage.setItem("mainSection", "nowhere");
    expect(restoreMainSection("")).toBeNull();
    window.sessionStorage.clear();
    // 旧版本残留的 ask / deps（D29 / D30 前记下的）→ 查不到页，不导航、不崩
    for (const stale of ["ask", "deps"]) {
      window.localStorage.setItem("mainSection", stale);
      expect(restoreMainSection("")).toBeNull();
      window.sessionStorage.clear();
    }
    window.localStorage.removeItem("mainSection");
    expect(restoreMainSection("")).toBeNull();
  });
});
