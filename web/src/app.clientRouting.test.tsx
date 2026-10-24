// App 级判例（D40 客户端路由，CONTRACT §49 / §54.4 2026-09-06 追记）：换页 = pushState + 从路由订阅重渲染，**不重载**——
//   · rail 点击（文档级链接委托）→ ?page=settings、document.title 跟页、设置页渲染；会议纪要 store 一直是同一份：/api/recaps
//     不再拉、SSE（realtime.start）只起一次；回到会议纪要不闪「读取中…」（行还在）；
//   · ⌘1…⌘4 = 栏上四项（会议纪要 / 录制与导入 / 设置 / 关于）；
//   · popstate（后退 / 前进）→ 页跟 URL；退役页的旧深链（?page=board / trash / ?card=）回落会议纪要；
//   · 壳的 open_page {page, anchor?} 命令 → 同一条路（不认识的 page 回落会议纪要；anchor 过校验、到了设置页即被消费）；
//   · 设置页 anchor 深链：App 不抢滚动（SettingsPage 滚到那一区）、anchor 不漏进下一页的 URL、回会议纪要照样还原滚动位置；
//     已在设置页时再点一条带 anchor 的链接 → 不重挂也滚过去；
//   · 焦点：权限体检页的「← 返回设置」随旧页卸载 → 焦点放到 <main>（读屏器有落点）；rail 项点击焦点还在就不动。
// api 全 mock（fetchRecaps 回一行，其余 offline）、realtime mock 计数、壳桥用假 messageHandler 顶着。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchRecaps } from "./api";
import { App } from "./app";
import { createRecapsRealtime } from "./realtime";
import { resetRouterForTests } from "./route";
import { resetShellBridgeForTests, SHELL_COMMAND_EVENT } from "./shellBridge";
import { getState, resetStoreForTests } from "./store";

// vi.mock 工厂会被提升到文件顶部：工厂里用到的东西走 vi.hoisted
const { SNAPSHOT, realtimeStart, realtimeStop } = vi.hoisted(() => ({
  SNAPSHOT: {
    recaps: [{
      key: "meeting:2026-10-20T1400-zoom", app: "zoom", start: "2026-10-20T14:00:00Z", end: "2026-10-20T14:42:00Z",
      duration_min: 42, status: "closed", version: 1, en: ["Decisions: none"], zh: ["决定：无"], quality: "ok",
    }],
    recap_counts: { active: 1, archived: 0, dismissed: 0 },
  },
  realtimeStart: vi.fn(),
  realtimeStop: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  const offline: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(actual)) {
    if (typeof value === "function" && /^(fetch|post|put|verify)/.test(name)) {
      offline[name] = vi.fn().mockRejectedValue(new Error("offline"));
    }
  }
  return { ...actual, ...offline, fetchRecaps: vi.fn().mockResolvedValue(SNAPSHOT) };
});

vi.mock("./realtime", () => ({
  createRecapsRealtime: vi.fn(() => ({ start: realtimeStart, stop: realtimeStop })),
}));

function shellCommand(detail: unknown) {
  act(() => {
    window.dispatchEvent(new CustomEvent(SHELL_COMMAND_EVENT, { detail }));
  });
}

function popTo(path: string) {
  act(() => {
    window.history.replaceState(null, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
}

const recapsRendered = () => expect(document.querySelector(".recaps-layout")).toBeTruthy();

async function renderRecaps() {
  const view = render(<App />);
  await waitFor(recapsRendered);
  return view;
}

const railLink = (slug: string) => document.querySelector<HTMLAnchorElement>(`[data-rail-item="${slug}"]`)!;

/** jsdom 不滚：把 window.scrollY 假装成 y（rememberScroll 读它） */
function setScrollY(y: number) {
  Object.defineProperty(window, "scrollY", { value: y, configurable: true });
}

/** 页面里任何指向本 SPA 的 <a href>（录制页 / 关于页的设置页深链都是这种）：挂一条、点它、摘掉 */
function clickAppLink(href: string) {
  const a = document.createElement("a");
  a.href = href;
  document.body.appendChild(a);
  act(() => {
    fireEvent.click(a);
  });
  a.remove();
}

const scrolledInto = () => vi.mocked(Element.prototype.scrollIntoView).mock.contexts as Element[];

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.sessionStorage.setItem("zai.launched", "1"); // 不是冷启动：mainSection 不回跳
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  resetRouterForTests();
  resetShellBridgeForTests();
  vi.mocked(fetchRecaps).mockClear();
  vi.mocked(createRecapsRealtime).mockClear();
  realtimeStart.mockClear();
  realtimeStop.mockClear();
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  Element.prototype.scrollIntoView = vi.fn(); // jsdom 没有；设置页 anchor 深链会调
  window.webkit = { messageHandlers: { zaiShell: { postMessage: vi.fn().mockResolvedValue({}) } } };
});

afterEach(() => {
  cleanup();
  delete window.webkit;
  vi.restoreAllMocks();
  setScrollY(0);
  window.history.replaceState(null, "", "/");
});

describe("App — 换页不重载（D40）", () => {
  it("rail 点击 → 设置页；store / SSE 活过换页；回会议纪要不再拉列表、不闪加载态", async () => {
    await renderRecaps();
    expect(fetchRecaps).toHaveBeenCalledTimes(1);
    expect(realtimeStart).toHaveBeenCalledTimes(1);
    expect(document.title).toMatch(/— (会议纪要|Recaps)$/);
    const rows = getState().recaps;

    act(() => {
      fireEvent.click(railLink("settings"));
    });
    expect(window.location.search).toBe("?page=settings");
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    expect(document.title).toMatch(/— (设置|Settings)$/);
    expect(railLink("settings").getAttribute("aria-current")).toBe("page");
    expect(railLink("recaps").getAttribute("aria-current")).toBeNull();
    // 没有整页重载：同一份 store、SSE 没重起、/api/recaps 没再拉
    expect(getState().recaps).toBe(rows);
    expect(fetchRecaps).toHaveBeenCalledTimes(1);
    expect(createRecapsRealtime).toHaveBeenCalledTimes(1);
    expect(realtimeStop).not.toHaveBeenCalled();

    act(() => {
      fireEvent.click(railLink("recaps"));
    });
    expect(window.location.search).toBe("");
    await waitFor(recapsRendered);
    expect(screen.queryByText(/^(读取中…|Loading…)$/)).toBeNull();
    expect(fetchRecaps).toHaveBeenCalledTimes(1);
    expect(getState().recaps).toBe(rows);
    expect(document.title).toMatch(/— (会议纪要|Recaps)$/);
  });

  it("⌘3 → 设置页、⌘1 → 会议纪要、⌘2 → 录制与导入、⌘4 → 关于，同样不重载", async () => {
    await renderRecaps();
    act(() => {
      fireEvent.keyDown(window, { key: "3", metaKey: true });
    });
    expect(window.location.search).toBe("?page=settings");
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    act(() => {
      fireEvent.keyDown(window, { key: "1", metaKey: true });
    });
    expect(window.location.search).toBe("");
    await waitFor(recapsRendered);
    act(() => {
      fireEvent.keyDown(window, { key: "2", metaKey: true });
    });
    expect(window.location.search).toBe("?page=ingest");
    act(() => {
      fireEvent.keyDown(window, { key: "4", metaKey: true });
    });
    expect(window.location.search).toBe("?page=about");
    act(() => {
      fireEvent.keyDown(window, { key: "5", metaKey: true }); // 栏上只有四项：⌘5 不归我们
    });
    expect(window.location.search).toBe("?page=about");
    expect(fetchRecaps).toHaveBeenCalledTimes(1);
  });

  it("后退 / 前进（popstate）：页跟 URL；退役页与 ?card= 的旧深链回落会议纪要", async () => {
    await renderRecaps();
    popTo("/?page=about");
    await waitFor(() => expect(document.title).toMatch(/— (关于|About)$/));
    popTo("/?page=trash");
    await waitFor(() => expect(document.title).toMatch(/— (会议纪要|Recaps)$/));
    popTo("/?page=ingest");
    await waitFor(() => expect(document.title).toMatch(/— (录制与导入|Recording & import)$/));
    popTo("/?card=R-1");
    await waitFor(recapsRendered);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchRecaps).toHaveBeenCalledTimes(1);
  });

  it("壳的 open_page 命令：page 过白名单、anchor 过校验（到了设置页即被消费：滚到那一区、URL 上摘掉）；不认识的页回落会议纪要", async () => {
    await renderRecaps();
    shellCommand({ command: "open_page", page: "settings", anchor: "recording" });
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    expect(scrolledInto()).toContain(document.getElementById("settings-recording"));
    expect(window.location.search).toBe("?page=settings"); // 一次性指令：消费完从 URL 上摘掉（后退 / 前进不重放）
    shellCommand({ command: "open_page", page: "about" });
    expect(window.location.search).toBe("?page=about");
    shellCommand({ command: "open_page", page: "permissions", anchor: "<script>" });
    expect(window.location.search).toBe("?page=permissions"); // 坏 anchor 丢掉
    shellCommand({ command: "open_page", page: "nope" });
    expect(window.location.search).toBe("");
    shellCommand({ command: "open_page" });
    expect(window.location.search).toBe("");
    shellCommand({ command: "quick_capture" }); // 退役命令（§61.1 tombstone）：接了也什么都不做
    expect(window.location.search).toBe("");
    expect(fetchRecaps).toHaveBeenCalledTimes(1);
  });

  it("设置页 anchor 深链：App 不抢滚动、anchor 不漏进下一页的 URL、回会议纪要还原离开时的位置", async () => {
    await renderRecaps();
    setScrollY(500);
    clickAppLink("/?page=settings&anchor=deps"); // 关于页「依赖检查」那种深链
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    expect(scrolledInto()).toContain(document.getElementById("settings-deps")); // SettingsPage 滚到那一区
    expect(window.scrollTo).not.toHaveBeenCalled(); // App 不把它拉回顶 / 拉回记忆
    expect(window.location.search).toBe("?page=settings"); // anchor 被消费
    setScrollY(1800);
    act(() => {
      fireEvent.click(railLink("recaps"));
    });
    await waitFor(recapsRendered);
    expect(window.location.search).toBe(""); // 没有 ?anchor=deps 残留
    expect(window.scrollTo).toHaveBeenLastCalledWith(0, 500); // 回到离开时的位置
    // 再去设置页：不再滚回 deps 区（anchor 没有跟着 URL 转世），而是还原离开设置页时的位置
    vi.mocked(Element.prototype.scrollIntoView).mockClear();
    act(() => {
      fireEvent.click(railLink("settings"));
    });
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
    expect(window.scrollTo).toHaveBeenLastCalledWith(0, 1800);
  });

  it("已在设置页时再点一条带 anchor 的链接：不重挂、滚到那一区、anchor 再被消费；同一个 anchor 再来一次也再滚", async () => {
    await renderRecaps();
    act(() => {
      fireEvent.click(railLink("settings"));
    });
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    const page = document.querySelector(".settings-page");
    vi.mocked(window.scrollTo).mockClear();
    clickAppLink("/?page=settings&anchor=deps");
    await waitFor(() => expect(scrolledInto()).toContain(document.getElementById("settings-deps")));
    expect(document.querySelector(".settings-page")).toBe(page); // 同一个组件实例
    expect(window.location.search).toBe("?page=settings");
    expect(window.scrollTo).not.toHaveBeenCalled(); // 同页 URL 变化不是换页：不还原、不到顶
    vi.mocked(Element.prototype.scrollIntoView).mockClear();
    shellCommand({ command: "open_page", page: "settings", anchor: "deps" }); // 壳菜单再点一次同一区
    await waitFor(() => expect(scrolledInto()).toContain(document.getElementById("settings-deps")));
    expect(window.location.search).toBe("?page=settings");
  });

  it("焦点：「← 返回设置」随旧页卸载 → 焦点放到 <main>；rail 项点击焦点还在 rail 上就不动；首帧不抢焦点", async () => {
    await renderRecaps();
    expect(document.activeElement).toBe(document.body);
    const rail = railLink("settings");
    rail.focus();
    act(() => {
      fireEvent.click(rail);
    });
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    expect(document.activeElement).toBe(rail); // 焦点没丢，不动
    popTo("/?page=permissions");
    await waitFor(() => expect(document.querySelector(".perm-page .page-back-link")).toBeTruthy());
    const back = document.querySelector<HTMLAnchorElement>(".perm-page .page-back-link")!;
    back.focus();
    act(() => {
      fireEvent.click(back);
    });
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    expect(back.isConnected).toBe(false);
    expect(document.activeElement).toBe(document.querySelector("main.shell-main"));
  });

  it("App 卸载：路由器停掉——之后点链接不再拦、popstate 不再动 store", async () => {
    const view = await renderRecaps();
    view.unmount();
    const a = document.createElement("a");
    a.href = "/?page=about";
    document.body.appendChild(a);
    const event = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
    window.addEventListener("click", (e) => e.preventDefault(), { once: true }); // 免得 jsdom 去导航
    a.dispatchEvent(event);
    expect(window.location.search).toBe("");
    a.remove();
    expect(realtimeStop).toHaveBeenCalledTimes(1);
  });
});
