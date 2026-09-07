// App 级判例（D40 客户端路由，CONTRACT §49 / §54.4 2026-09-06 追记）：换页 = pushState + 从路由订阅重渲染，**不重载**——
//   · rail 点击（文档级链接委托）→ ?page=settings、document.title 跟页、设置页渲染；看板 store 一直是同一份：/api/board 不再拉、
//     SSE（realtime.start）只起一次、「合并中…」章（forceMergingIds + 180 s 定时器）/ 多选态 / 书立条展开态都活过换页；
//   · 「← 返回看板」→ 回看板不闪「正在加载看板…」（快照还在）；
//   · popstate（后退 / 前进）→ 页跟 URL；回到带 ?card= 的那一版 → 抽屉重开；
//   · 壳的 open_page {page, anchor?} 命令 → 同一条路（不认识的 page 回落看板；anchor 过校验、到了设置页即被消费）；
//   · 设置页 anchor 深链：App 不抢滚动（SettingsPage 滚到那一区）、anchor 不漏进下一页的 URL、回看板照样还原滚动位置；
//     已在设置页时再点一条带 anchor 的链接 → 不重挂也滚过去；
//   · 看板列容器（.board-main，data-scroll-memory）的横向滚动随换页记住；
//   · 焦点：「← 返回看板」随旧页卸载 → 焦点放到 <main>（读屏器有落点）；rail 项点击焦点还在就不动；
//   · 非看板页不等 /api/board（appshell-dashboard-missing 已做，这里钉不回退：换到设置页时 boardLoading 与否都渲染）。
// api 全 mock（fetchBoard 回最小快照，其余 offline）、realtime mock 计数、壳桥用假 messageHandler 顶着。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "./api";
import { App } from "./app";
import { createBoardRealtime } from "./realtime";
import { resetRouterForTests } from "./route";
import { resetShellBridgeForTests, SHELL_COMMAND_EVENT } from "./shellBridge";
import {
  getState, markForceMerging, resetStoreForTests, setBacklogStripExpanded, setSelectionMode, toggleSelected,
} from "./store";

// vi.mock 工厂会被提升到文件顶部：工厂里用到的东西走 vi.hoisted
const { BOARD, realtimeStart, realtimeStop } = vi.hoisted(() => ({
  BOARD: {
    generated_at: "2026-09-06T12:00:00Z",
    needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [], counts: {},
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
  return {
    ...actual,
    ...offline,
    fetchBoard: vi.fn().mockResolvedValue(BOARD),
    fetchCard: vi.fn().mockResolvedValue({ id: "R-1", lane: "running", name: "Card one" }),
  };
});

vi.mock("./realtime", () => ({
  createBoardRealtime: vi.fn(() => ({ start: realtimeStart, stop: realtimeStop })),
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

async function renderBoard() {
  const view = render(<App />);
  await waitFor(() => expect(document.querySelector(".board-column")).toBeTruthy());
  return view;
}

const railLink = (slug: string) => document.querySelector<HTMLAnchorElement>(`[data-rail-item="${slug}"]`)!;

/** jsdom 不滚：把 window.scrollY 假装成 y（rememberScroll 读它） */
function setScrollY(y: number) {
  Object.defineProperty(window, "scrollY", { value: y, configurable: true });
}

/** 页面里任何指向本 SPA 的 <a href>（横幅 / 诊断条 / 录制页的设置页深链都是这种）：挂一条、点它、摘掉 */
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
  vi.mocked(fetchBoard).mockClear();
  vi.mocked(createBoardRealtime).mockClear();
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
  it("rail 点击 → 设置页；store / SSE / 「合并中…」章 / 多选 / 书立条都活过换页；回看板不再拉快照、不闪加载态", async () => {
    await renderBoard();
    expect(fetchBoard).toHaveBeenCalledTimes(1);
    expect(realtimeStart).toHaveBeenCalledTimes(1);
    expect(document.title).toMatch(/— (任务台|Workbench)$/);

    // 会话内瞬态：强制合并在途批次（章 + 180 s 定时器）、多选态、书立条展开态
    act(() => {
      markForceMerging(["R-1", "R-2"], "R-1");
      setSelectionMode(true);
      toggleSelected("R-3");
      setBacklogStripExpanded(true);
    });
    const board = getState().board;

    act(() => {
      fireEvent.click(railLink("settings"));
    });
    expect(window.location.search).toBe("?page=settings");
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    expect(document.title).toMatch(/— (设置|Settings)$/);
    expect(railLink("settings").getAttribute("aria-current")).toBe("page");
    expect(railLink("dashboard").getAttribute("aria-current")).toBeNull();
    // 没有整页重载：看板 store 同一份、SSE 没重起、/api/board 没再拉
    expect(getState().board).toBe(board);
    expect(fetchBoard).toHaveBeenCalledTimes(1);
    expect(createBoardRealtime).toHaveBeenCalledTimes(1);
    expect(realtimeStop).not.toHaveBeenCalled();
    expect([...getState().forceMergingIds].sort()).toEqual(["R-1", "R-2"]);
    expect(getState().selectionMode).toBe(true);
    expect(getState().selectedIds.has("R-3")).toBe(true);
    expect(getState().backlogStripExpanded).toBe(true);
    expect(screen.queryByText(/正在加载看板|Loading the board/)).toBeNull();

    // 「← 返回看板」（设置页顶的 <a href>）：同一条委托路
    act(() => {
      fireEvent.click(document.querySelector(".settings-page .trash-back-link")!);
    });
    expect(window.location.search).toBe("");
    await waitFor(() => expect(document.querySelector(".board-column")).toBeTruthy());
    expect(screen.queryByText(/正在加载看板|Loading the board/)).toBeNull();
    expect(fetchBoard).toHaveBeenCalledTimes(1);
    expect(getState().board).toBe(board);
    expect([...getState().forceMergingIds].sort()).toEqual(["R-1", "R-2"]);
    expect(document.title).toMatch(/— (任务台|Workbench)$/);
  });

  it("⌘6 → 设置页、⌘1 → 看板，同样不重载", async () => {
    await renderBoard();
    act(() => {
      fireEvent.keyDown(window, { key: "6", metaKey: true });
    });
    expect(window.location.search).toBe("?page=settings");
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    act(() => {
      fireEvent.keyDown(window, { key: "1", metaKey: true });
    });
    expect(window.location.search).toBe("");
    await waitFor(() => expect(document.querySelector(".board-column")).toBeTruthy());
    expect(fetchBoard).toHaveBeenCalledTimes(1);
  });

  it("后退 / 前进（popstate）：页跟 URL；回到带 ?card= 的那一版抽屉重开、离开它关掉", async () => {
    await renderBoard();
    popTo("/?page=about");
    await waitFor(() => expect(document.title).toMatch(/— (关于|About)$/));
    popTo("/?card=R-1");
    await waitFor(() => expect(getState().selectedCardId).toBe("R-1"));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    popTo("/?page=trash");
    await waitFor(() => expect(document.title).toMatch(/— (回收站|Trash)$/));
    expect(getState().selectedCardId).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchBoard).toHaveBeenCalledTimes(1);
  });

  it("壳的 open_page 命令：page 过白名单、anchor 过校验（到了设置页即被消费：滚到那一区、URL 上摘掉）；不认识的页回落看板", async () => {
    await renderBoard();
    shellCommand({ command: "open_page", page: "settings", anchor: "live_captions" });
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    expect(scrolledInto()).toContain(document.getElementById("settings-live_captions"));
    expect(window.location.search).toBe("?page=settings"); // 一次性指令：消费完从 URL 上摘掉（后退 / 前进不重放）
    shellCommand({ command: "open_page", page: "about" });
    expect(window.location.search).toBe("?page=about");
    shellCommand({ command: "open_page", page: "permissions", anchor: "<script>" });
    expect(window.location.search).toBe("?page=permissions"); // 坏 anchor 丢掉
    shellCommand({ command: "open_page", page: "nope" });
    expect(window.location.search).toBe("");
    shellCommand({ command: "open_page" });
    expect(window.location.search).toBe("");
    expect(fetchBoard).toHaveBeenCalledTimes(1);
  });

  it("过滤词跟着换页留在 URL 里（rail 链接从当前 URL 长出来）；后退带回那一版的 ?q=", async () => {
    await renderBoard();
    act(() => {
      window.history.replaceState(null, "", "/?q=foo");
    });
    // rail 的 href 在渲染时算好——让它重渲染一次（任何 store 变化都会）
    act(() => {
      setSelectionMode(true);
    });
    act(() => {
      fireEvent.click(railLink("trash"));
    });
    expect(window.location.search).toBe("?q=foo&page=trash");
    expect(getState().filters.search).toBe("foo");
    popTo("/?page=trash");
    expect(getState().filters.search).toBe("");
    popTo("/?q=foo&page=trash");
    expect(getState().filters.search).toBe("foo");
  });

  it("设置页 anchor 深链：App 不抢滚动、anchor 不漏进下一页的 URL、回看板还原离开时的位置", async () => {
    await renderBoard();
    setScrollY(500);
    clickAppLink("/?page=settings&anchor=deps"); // 横幅「依赖检查」那种深链
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    expect(scrolledInto()).toContain(document.getElementById("settings-deps")); // SettingsPage 滚到那一区
    expect(window.scrollTo).not.toHaveBeenCalled(); // App 不把它拉回顶 / 拉回记忆
    expect(window.location.search).toBe("?page=settings"); // anchor 被消费
    setScrollY(1800);
    act(() => {
      fireEvent.click(railLink("dashboard"));
    });
    await waitFor(() => expect(document.querySelector(".board-column")).toBeTruthy());
    expect(window.location.search).toBe(""); // 没有 ?anchor=deps 残留
    expect(window.scrollTo).toHaveBeenLastCalledWith(0, 500); // 看板回到离开时的位置
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
    await renderBoard();
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
    shellCommand({ command: "open_page", page: "settings", anchor: "deps" }); // 悬浮窗齿轮再点一次同一区
    await waitFor(() => expect(scrolledInto()).toContain(document.getElementById("settings-deps")));
    expect(window.location.search).toBe("?page=settings");
  });

  it("看板列容器（.board-main）的横向滚动随换页记住、回来还原", async () => {
    await renderBoard();
    document.querySelector<HTMLElement>(".board-main")!.scrollLeft = 300; // 窄窗：横向滚过两列
    act(() => {
      fireEvent.click(railLink("about"));
    });
    await waitFor(() => expect(document.title).toMatch(/— (关于|About)$/));
    act(() => {
      fireEvent.click(railLink("dashboard"));
    });
    await waitFor(() => expect(document.querySelector(".board-column")).toBeTruthy());
    expect(document.querySelector<HTMLElement>(".board-main")!.scrollLeft).toBe(300);
  });

  it("焦点：「← 返回看板」随旧页卸载 → 焦点放到 <main>；rail 项点击焦点还在 rail 上就不动；首帧不抢焦点", async () => {
    await renderBoard();
    expect(document.activeElement).toBe(document.body);
    const rail = railLink("settings");
    rail.focus();
    act(() => {
      fireEvent.click(rail);
    });
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    expect(document.activeElement).toBe(rail); // 焦点没丢，不动
    const back = document.querySelector<HTMLAnchorElement>(".settings-page .trash-back-link")!;
    back.focus();
    act(() => {
      fireEvent.click(back);
    });
    await waitFor(() => expect(document.querySelector(".board-column")).toBeTruthy());
    expect(back.isConnected).toBe(false);
    expect(document.activeElement).toBe(document.querySelector("main.shell-main"));
  });

  it("App 卸载：路由器停掉——之后点链接不再拦、popstate 不再动 store", async () => {
    const view = await renderBoard();
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
