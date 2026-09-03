// 左侧导航栏判例（CONTRACT §54.4 / §66.2 rail:*）：八页同序同名（zh / en）+ data-rail-item 锚、
// 选中态跟 ?page=（diagnostics 归 deps）、深链正确、折叠持久化到 localStorage `sidebarCollapsed`
// （原生 UserDefaults 同名）、收起态只剩图标 + tooltip、⌘1…⌘8 换页、宽度钳制 160–320。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { navigate } from "../../route";
import { activeRailSlug, clampSidebarWidth, NavRail } from "./NavRail";

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
  { rail: { items: Array<{ slug: string; zh: string; en: string }> } } | undefined;
if (!inventory) throw new Error("ui/parity/native-inventory.json not found — this suite runs from the repo's web/ dir");
const nativeRail = inventory.rail.items;

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(cleanup);

describe("NavRail — 原生 sidebar 的 web 落点", () => {
  it("八个条目按原生顺序渲染，data-rail-item 与双语标题逐字镜像清单", () => {
    renderRail("en");
    const items = Array.from(document.querySelectorAll<HTMLAnchorElement>("[data-rail-item]"));
    expect(items.map((el) => el.dataset.railItem)).toEqual(nativeRail.map((r) => r.slug));
    expect(items.map((el) => el.querySelector(".rail-label")?.textContent)).toEqual(nativeRail.map((r) => r.en));
    expect(document.querySelector('[data-rail="left"]')).toBeTruthy();
    cleanup();
    renderRail("zh");
    const zh = Array.from(document.querySelectorAll<HTMLAnchorElement>("[data-rail-item] .rail-label"));
    expect(zh.map((el) => el.textContent)).toEqual(nativeRail.map((r) => r.zh));
  });

  it("深链：回收站 → ?page=trash，设置 → ?page=settings，任务台 → 无 page 参数", () => {
    renderRail();
    const href = (slug: string) => new URL((document.querySelector(`[data-rail-item="${slug}"]`) as HTMLAnchorElement).href);
    expect(href("trash").searchParams.get("page")).toBe("trash");
    expect(href("settings").searchParams.get("page")).toBe("settings");
    expect(href("about").searchParams.get("page")).toBe("about");
    expect(href("dashboard").searchParams.get("page")).toBeNull();
  });

  it("选中态跟 ?page=：看板点亮任务台，diagnostics 归依赖检查，permissions 不点亮任何项", () => {
    renderRail("en", "?page=settings");
    expect(document.querySelector('[data-rail-item="settings"]')?.getAttribute("aria-current")).toBe("page");
    expect(document.querySelector('[data-rail-item="dashboard"]')?.getAttribute("aria-current")).toBeNull();
    expect(activeRailSlug("board")).toBe("dashboard");
    expect(activeRailSlug("diagnostics")).toBe("deps");
    expect(activeRailSlug("permissions")).toBeNull();
  });

  it("折叠：按钮翻转 sidebarCollapsed 并持久化；收起态只剩图标，tooltip 仍是双语标题", () => {
    renderRail("en");
    expect(document.querySelector(".rail")?.classList.contains("is-collapsed")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Collapse/expand sidebar" }));
    expect(window.localStorage.getItem("sidebarCollapsed")).toBe("true");
    expect(document.querySelector(".rail")?.classList.contains("is-collapsed")).toBe(true);
    expect(document.querySelectorAll(".rail-label").length).toBe(0);
    expect(document.querySelector('[data-rail-item="trash"]')?.getAttribute("title")).toBe("Trash (⌘5)");
    cleanup();
    // 重开：读回持久化的收起态
    renderRail("zh");
    expect(document.querySelector(".rail")?.classList.contains("is-collapsed")).toBe(true);
    expect(document.querySelector('[data-rail-item="trash"]')?.getAttribute("title")).toBe("回收站 (⌘5)");
  });

  it("⌘1…⌘8 换页（原生 keyboardShortcut）；输入框里不劫持", () => {
    const nav = vi.mocked(navigate);
    nav.mockReset();
    renderRail();
    fireEvent.keyDown(window, { key: "7", metaKey: true });
    expect(nav).toHaveBeenCalledTimes(1);
    expect(new URL(String(nav.mock.calls[0][0])).searchParams.get("page")).toBe("settings");
    const input = document.createElement("input");
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: "5", metaKey: true });
    expect(nav).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "9", metaKey: true });
    expect(nav).toHaveBeenCalledTimes(1);
  });

  it("侧栏宽度钳制在 160–320（原生 clampSidebar）", () => {
    expect(clampSidebarWidth(100)).toBe(160);
    expect(clampSidebarWidth(200)).toBe(200);
    expect(clampSidebarWidth(900)).toBe(320);
  });
});
