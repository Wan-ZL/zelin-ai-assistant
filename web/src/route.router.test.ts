// 客户端路由器（D40，CONTRACT §49 / §54.4 2026-09-06 追记）：URL 是真源、换页不重载。
//   · navigate：本 SPA 的 URL → pushState + 通知订阅者；replace=true 或目标 = 当前 URL → replaceState（不叠历史）；
//     别的路径 / origin → 仍是整页 location.assign / replace（server 文件面、外链不进路由）；
//   · popstate（后退 / 前进）→ 通知订阅者；订阅者退订后不再收；
//   · 文档级链接委托：左键点本 SPA 的 <a href> → preventDefault + navigate；⌘ / ⌃ / ⇧ / ⌥ 点、中键、target=_blank、
//     download、别的路径、纯片段（设置页目录 #settings-<id>）、内层已 preventDefault 的都不拦；
//   · 滚动记忆：离开一页记 window 滚动 + [data-scroll-memory] 容器，回来还原；没记过 → 到顶；
//   · 焦点：换页后焦点掉到 body → 放到 main.shell-main；焦点还在别处不动；
//   · buildAppUrl 不带片段、不带上一页的页内 query（anchor / log / step）；isAppUrl / isHashOnlyChange 真值表。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildAppUrl, buildSettingsUrl, focusPageRoot, interceptAppLinks, isAppUrl, isHashOnlyChange, navigate, readPage, rememberScroll,
  resetRouterForTests, restoreScroll, startRouter, subscribeRoute,
} from "./route";

beforeEach(() => {
  resetRouterForTests();
  window.history.replaceState(null, "", "/");
  document.body.innerHTML = "";
});

afterEach(() => {
  resetRouterForTests();
  window.history.replaceState(null, "", "/");
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("route — navigate 是 pushState，不是整页重载", () => {
  it("本 SPA 的 URL：pushState + 通知订阅者，location 不动文档", () => {
    const seen: string[] = [];
    const stop = subscribeRoute(() => seen.push(window.location.search));
    const push = vi.spyOn(window.history, "pushState");
    navigate(buildAppUrl(window.location.href, "settings", null));
    expect(push).toHaveBeenCalledTimes(1);
    expect(window.location.search).toBe("?page=settings");
    expect(readPage(window.location.search)).toBe("settings");
    expect(seen).toEqual(["?page=settings"]);
    stop();
    navigate(buildAppUrl(window.location.href, "trash", null));
    expect(seen).toEqual(["?page=settings"]); // 退订后不再收
    expect(window.location.search).toBe("?page=trash");
  });

  it("replace=true → replaceState（向导跳转 / mainSection 冷启动回跳不进历史栈）", () => {
    const push = vi.spyOn(window.history, "pushState");
    const replace = vi.spyOn(window.history, "replaceState");
    navigate(buildAppUrl(window.location.href, "setup", null), true);
    expect(push).not.toHaveBeenCalled();
    expect(replace).toHaveBeenCalledTimes(1);
    expect(window.location.search).toBe("?page=setup");
  });

  it("目标就是当前 URL（再点一次当前 rail 项）→ replaceState，不叠一条一样的历史", () => {
    window.history.replaceState(null, "", "/?page=about");
    const push = vi.spyOn(window.history, "pushState");
    const seen = vi.fn();
    subscribeRoute(seen);
    navigate(buildAppUrl(window.location.href, "about", null));
    expect(push).not.toHaveBeenCalled();
    expect(seen).toHaveBeenCalledTimes(1); // 仍通知（订阅者幂等）
  });

  it("字符串也认（相对 / 绝对）", () => {
    navigate("?page=ingest");
    expect(window.location.search).toBe("?page=ingest");
    navigate(`${window.location.origin}/?page=archive`);
    expect(window.location.search).toBe("?page=archive");
  });

  it("不属于本 SPA 的 URL（别的路径 / origin）→ 整页 location.assign / replace，不进路由", () => {
    const assign = vi.fn();
    const replace = vi.fn();
    const real = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { origin: real.origin, pathname: real.pathname, search: real.search, href: real.href, assign, replace },
    });
    try {
      const seen = vi.fn();
      subscribeRoute(seen);
      navigate(`${real.origin}/api/logs/actd.log`);
      expect(assign).toHaveBeenCalledWith(`${real.origin}/api/logs/actd.log`);
      navigate("https://example.com/x", true);
      expect(replace).toHaveBeenCalledWith("https://example.com/x");
      expect(seen).not.toHaveBeenCalled();
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: real });
    }
  });

  it("isAppUrl / isHashOnlyChange 真值表", () => {
    const here = { origin: "http://127.0.0.1:47820", pathname: "/", search: "?page=settings" };
    expect(isAppUrl(new URL("http://127.0.0.1:47820/?page=trash"), here)).toBe(true);
    expect(isAppUrl(new URL("http://127.0.0.1:47820/#settings-deps"), here)).toBe(true); // 同路径，片段不算
    expect(isAppUrl(new URL("http://127.0.0.1:47820/api/board"), here)).toBe(false);
    expect(isAppUrl(new URL("http://127.0.0.1:47820/files/x.html"), here)).toBe(false);
    expect(isAppUrl(new URL("https://127.0.0.1:47820/"), here)).toBe(false); // scheme 也算 origin
    expect(isAppUrl(new URL("http://localhost:47820/"), here)).toBe(false);
    expect(isHashOnlyChange(new URL("http://127.0.0.1:47820/?page=settings#settings-deps"), here)).toBe(true);
    expect(isHashOnlyChange(new URL("http://127.0.0.1:47820/?page=trash#x"), here)).toBe(false); // query 变了 = 换页
    expect(isHashOnlyChange(new URL("http://127.0.0.1:47820/?page=settings"), here)).toBe(false); // 没片段
  });

  it("buildAppUrl / buildSettingsUrl 不带上一页的片段（设置页目录点过 #settings-<id> 之后 location.href 带着它）", () => {
    const url = buildAppUrl("http://127.0.0.1:47820/?page=settings#settings-deps", "board", null);
    expect(url.hash).toBe("");
    expect(url.search).toBe("");
    expect(buildAppUrl("http://127.0.0.1:47820/#x", "trash", "R-1").href).toBe("http://127.0.0.1:47820/?page=trash&card=R-1");
  });

  it("buildAppUrl 不带上一页的页内 query（?anchor= / ?log= / ?step=）——过滤器照旧带着；要带的调用方之后自己 set", () => {
    // ?page=settings&anchor=deps&log=actd.log 之后点 rail 任务台：不许成 /?anchor=deps&log=actd.log（再点设置又滚回 deps、又翻开日志）
    const board = buildAppUrl("http://127.0.0.1:47820/?page=settings&anchor=deps&log=actd.log&q=foo&tier=T1", "board", null);
    expect(board.search).toBe("?q=foo&tier=T1");
    // 已在设置页（anchor 还在 URL 上）点 rail 设置：同样不带
    expect(buildAppUrl("http://127.0.0.1:47820/?page=settings&anchor=deps", "settings", null).search).toBe("?page=settings");
    // 向导的 ?step= 是向导页内的
    expect(buildAppUrl("http://127.0.0.1:47820/?page=setup&step=vault", "board", null).search).toBe("");
    expect(buildAppUrl("http://127.0.0.1:47820/?step=vault", "setup", null).search).toBe("?page=setup");
    // buildSettingsUrl 在之后 set anchor：照旧只有它这一个
    expect(buildSettingsUrl("http://127.0.0.1:47820/?page=settings&anchor=general&log=x.log", "deps").search).toBe("?page=settings&anchor=deps");
  });
});

describe("route — startRouter：popstate + 链接委托", () => {
  it("后退 / 前进（popstate）通知订阅者；stop 后不再", () => {
    const stop = startRouter();
    const seen = vi.fn();
    subscribeRoute(seen);
    window.history.replaceState(null, "", "/?page=trash");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(seen).toHaveBeenCalledTimes(1);
    stop();
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("scrollRestoration 设成 manual（同文档换页由 restoreScroll 自己还原）", () => {
    window.history.scrollRestoration = "auto";
    const stop = startRouter();
    expect(window.history.scrollRestoration).toBe("manual");
    stop();
  });

  function link(href: string, attrs: Record<string, string> = {}): HTMLAnchorElement {
    const a = document.createElement("a");
    a.href = href;
    for (const [k, v] of Object.entries(attrs)) a.setAttribute(k, v);
    a.textContent = "x";
    document.body.appendChild(a);
    return a;
  }

  /** 派发一次点击；返回「路由器（挂在 document 上）处理完之后」的 defaultPrevented。window 上最后一站再 preventDefault 一次，
   *  免得没被拦的链接让 jsdom 去「navigation to another Document」（它不实现，只会刷一行 not implemented）。 */
  function click(el: Element, init: MouseEventInit = {}): { defaultPrevented: boolean } {
    const event = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ...init });
    let prevented = false;
    const last = (e: Event) => {
      prevented = e.defaultPrevented;
      e.preventDefault();
    };
    window.addEventListener("click", last, { once: true });
    el.dispatchEvent(event);
    window.removeEventListener("click", last);
    return { defaultPrevented: prevented };
  }

  it("左键点本 SPA 的 <a href> → 拦下、pushState、通知；href 原样留着（⌘点 / 复制链接照旧）", () => {
    const stop = startRouter();
    const seen = vi.fn();
    subscribeRoute(seen);
    const push = vi.spyOn(window.history, "pushState");
    const a = link("/?page=settings&anchor=deps");
    const event = click(a);
    expect(event.defaultPrevented).toBe(true);
    expect(push).toHaveBeenCalledTimes(1);
    expect(window.location.search).toBe("?page=settings&anchor=deps");
    expect(seen).toHaveBeenCalledTimes(1);
    expect(a.getAttribute("href")).toBe("/?page=settings&anchor=deps");
    stop();
  });

  it("点在 <a> 里的子元素（图标 / 文字 span）也算点了链接", () => {
    const stop = startRouter();
    const a = link("/?page=trash");
    const span = document.createElement("span");
    a.appendChild(span);
    const event = click(span);
    expect(event.defaultPrevented).toBe(true);
    expect(window.location.search).toBe("?page=trash");
    stop();
  });

  it("不拦：修饰键（⌘ / ⌃ / ⇧ / ⌥）、中键、target=_blank、download、别的路径、外部 origin、纯片段、内层已 preventDefault", () => {
    const stop = startRouter();
    const push = vi.spyOn(window.history, "pushState");
    const cases: Array<[HTMLAnchorElement, MouseEventInit]> = [
      [link("/?page=trash"), { metaKey: true }],
      [link("/?page=trash"), { ctrlKey: true }],
      [link("/?page=trash"), { shiftKey: true }],
      [link("/?page=trash"), { altKey: true }],
      [link("/?page=trash"), { button: 1 }],
      [link("/?page=trash", { target: "_blank" }), {}],
      [link("/?page=trash", { download: "" }), {}],
      [link("/api/logs/actd.log"), {}],
      [link("/files/R-1/report.html"), {}],
      [link("https://example.com/"), {}],
      [link("#settings-deps"), {}],
    ];
    for (const [a, init] of cases) {
      const event = click(a, init);
      expect(event.defaultPrevented).toBe(false);
    }
    const inner = link("/?page=trash");
    inner.addEventListener("click", (e) => e.preventDefault());
    click(inner);
    expect(push).not.toHaveBeenCalled();
    expect(window.location.search).toBe("");
    // 没有 <a> 的点击什么都不做
    const button = document.createElement("button");
    document.body.appendChild(button);
    click(button);
    expect(push).not.toHaveBeenCalled();
    stop();
  });

  it("target=_self 照拦；stop 之后不再拦", () => {
    const stop = startRouter();
    const a = link("/?page=about", { target: "_self" });
    expect(click(a).defaultPrevented).toBe(true);
    stop();
    const b = link("/?page=trash");
    expect(click(b).defaultPrevented).toBe(false);
  });

  it("interceptAppLinks 可挂在别的根上并卸掉", () => {
    const root = document.createElement("div");
    document.body.appendChild(root);
    const stop = interceptAppLinks(root as unknown as Document);
    const a = document.createElement("a");
    a.href = "/?page=archive";
    root.appendChild(a);
    expect(click(a).defaultPrevented).toBe(true);
    stop();
    expect(click(a).defaultPrevented).toBe(false);
  });
});

describe("route — 滚动记忆", () => {
  it("离开看板记住 window + [data-scroll-memory] 容器的位置，回来还原；没记过的页到顶", () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    const lane = document.createElement("div");
    lane.dataset.scrollMemory = "lane:needs_approval";
    document.body.appendChild(lane);
    Object.defineProperty(lane, "scrollTop", { value: 120, writable: true, configurable: true });
    Object.defineProperty(lane, "scrollLeft", { value: 4, writable: true, configurable: true });
    Object.defineProperty(window, "scrollY", { value: 300, configurable: true });
    Object.defineProperty(window, "scrollX", { value: 0, configurable: true });
    rememberScroll("board");
    // 换到设置页：DOM 里换成别的东西
    lane.scrollTop = 0;
    lane.scrollLeft = 0;
    Object.defineProperty(window, "scrollY", { value: 900, configurable: true });
    restoreScroll("settings"); // 没记过 → 到顶
    expect(scrollTo).toHaveBeenLastCalledWith(0, 0);
    restoreScroll("board");
    expect(scrollTo).toHaveBeenLastCalledWith(0, 300);
    expect(lane.scrollTop).toBe(120);
    expect(lane.scrollLeft).toBe(4);
    Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
  });

  it("没记过且已经在顶 → 不调 scrollTo（首帧不多余滚一下）", () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    restoreScroll("about");
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("navigate 在 pushState 之前给当前页记滚动；popstate 给刚离开的页记", () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    const stop = startRouter();
    Object.defineProperty(window, "scrollY", { value: 250, configurable: true });
    navigate("?page=settings"); // 离开 board @250
    Object.defineProperty(window, "scrollY", { value: 40, configurable: true });
    window.history.pushState(null, "", "/?page=about");
    window.dispatchEvent(new PopStateEvent("popstate")); // 离开 settings @40（模拟前进）
    Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
    restoreScroll("board");
    expect(scrollTo).toHaveBeenLastCalledWith(0, 250);
    restoreScroll("settings");
    expect(scrollTo).toHaveBeenLastCalledWith(0, 40);
    stop();
  });
});

describe("route — 换页后的焦点", () => {
  it("焦点掉到了 body → 放到 main.shell-main（不滚动）；焦点还在别处 → 不动；没有 main 不抛", () => {
    const main = document.createElement("main");
    main.className = "shell-main";
    main.tabIndex = -1;
    const focus = vi.spyOn(main, "focus");
    const button = document.createElement("button");
    document.body.append(main, button);
    focusPageRoot();
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
    expect(document.activeElement).toBe(main);
    button.focus();
    focusPageRoot();
    expect(document.activeElement).toBe(button); // rail 项 / 输入框还握着焦点：不抢
    expect(focus).toHaveBeenCalledTimes(1);
    document.body.innerHTML = "";
    expect(() => focusPageRoot()).not.toThrow();
  });
});
