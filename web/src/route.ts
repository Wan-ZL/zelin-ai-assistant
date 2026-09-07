// 深链路由：URL query 序列化模式，fork 自 dashi web/src/issueRoute.ts（Apache-2.0，NOTICE 登记）。
// 单页两个维度：?page=trash（回收站单独页，缺省 board）+ ?card=R-101（详情抽屉深链）。
// ?page=styleguide = 活体样式指南（开发者页，仅 URL 直达——看板头部不放入口）。
// ?page=settings = 设置页（§59 首个 section「模型」；顶栏齿轮入口）。
// ?page=recaps = 会议纪要页（§63；顶栏入口）——recap 不是卡，不走 ?card= 抽屉。
// ?page=archive（永久性完成页）/ permissions（权限体检）/ setup（首次运行向导）= §68 P4 parity 页。
// ?page=ingest（录制与数据接入）/ about（关于）= 左侧导航栏（§54.4，原生 MainSection）补齐的页；
// rail slug ↔ page 的映射在 components/shell/NavRail.tsx。
// ?page=deps / diagnostics（依赖检查——原生 DepsView 的名字与更早的诊断页深链）：D30（2026-09-04）起依赖检查并入
// 设置页的一区，两个值仍接受（URL 是 API，add-only），都渲染设置页并滚到 deps 区（readSettingsAnchor）。
// ?page=ask（问问助手 §27）已随 D29 退役：不再是合法页，旧深链按「未知页」回落看板。
// ?anchor=<section id>：设置页滚到某个 section（字幕悬浮窗齿轮深链 live_captions，§61.3；依赖检查 = deps）。
// 约定：路由只存"哪一页 + 哪张卡"，过滤器序列化由 A8 仿 dashi taskFilters.ts 在独立模块追加。
//
// 客户端路由（D40，CONTRACT §49 / §54.4 2026-09-06 追记）：**URL 仍是真源**——但换页不再整页重载。
// `navigate()` = `history.pushState` / `replaceState` + 通知订阅者；`useRoute()` 让组件订阅 `location.search`
// （与 realtime.ts / shellBridge.ts 同款的 useSyncExternalStore 小店，快照就是浏览器的 URL，没有第二份 state）；
// `startRouter()` 挂 popstate（后退 / 前进）与文档级的链接委托——页面里任何指向本 SPA 的 `<a href>`（rail 项、
// 「← 返回看板」、横幅 / 诊断条 / 向导的深链……）左键点下去都走 `navigate()`，href 照旧留着（⌘点 / 中键开新标签、
// 复制链接、无 JS 退化都还是原来的 URL）。原生 MainWindow.swift 在进程内换 section、store 是 app 寿命的
// （AppDelegate.swift:21）；web 自此同样：store / SSE / 「合并中…」章与它的 180 s 定时器 / 多选 / 书立条展开态
// 都活过换页；看板的滚动位置离开时记住、回来还原（其余页到顶——整页导航的默认行为）。
import { useSyncExternalStore } from "react";

const CARD_QUERY_PARAM = "card";
const PAGE_QUERY_PARAM = "page";
const ANCHOR_QUERY_PARAM = "anchor";

export type AppPage = "board" | "trash" | "styleguide" | "settings" | "recaps" | "archive" | "permissions" | "diagnostics" | "setup"
  | "deps" | "ingest" | "about";
const PAGES: readonly AppPage[] = ["board", "trash", "styleguide", "settings", "recaps", "archive", "permissions", "diagnostics", "setup",
  "deps", "ingest", "about"];

/** 设置页「依赖检查」区的 section id（D30；`?page=deps` / `diagnostics` 旧深链都落到它） */
export const DEPS_ANCHOR = "deps";

export function readCardId(search: string): string | null {
  // 保留大小写：id 由 SAFE_ID_RE 界定（允许小写），匹配按原样精确比对——不做 case 折叠
  const id = new URLSearchParams(search).get(CARD_QUERY_PARAM)?.trim();
  return id || null;
}

export function readPage(search: string): AppPage {
  const page = new URLSearchParams(search).get(PAGE_QUERY_PARAM);
  return page && (PAGES as readonly string[]).includes(page) ? (page as AppPage) : "board";
}

/** 依赖检查的两个旧深链（?page=deps / ?page=diagnostics）——它们如今都是设置页（D30） */
export function isDepsPage(page: AppPage): boolean {
  return page === "deps" || page === "diagnostics";
}

/** URL 有没有明确指定去处（?page= 或 ?card=）——没有 = 「就打开 app」，冷启动可回上次的页（NavRail restoreMainSection） */
export function hasExplicitRoute(search: string): boolean {
  const params = new URLSearchParams(search);
  return params.has(PAGE_QUERY_PARAM) || params.has(CARD_QUERY_PARAM);
}

/** ?anchor=<id>（设置页 section 深链）；只认 [a-z0-9_-]，其余当没有 */
export function readAnchor(search: string): string | null {
  const anchor = new URLSearchParams(search).get(ANCHOR_QUERY_PARAM)?.trim() ?? "";
  return /^[a-z0-9_-]{1,40}$/i.test(anchor) ? anchor : null;
}

/** 设置页要滚到的区：?anchor= 优先；旧深链 ?page=deps / diagnostics 没带 anchor 时 = 依赖检查区 */
export function readSettingsAnchor(search: string): string | null {
  return readAnchor(search) ?? (isDepsPage(readPage(search)) ? DEPS_ANCHOR : null);
}

export function buildAppUrl(href: string, page: AppPage, cardId: string | null): URL {
  const url = new URL(href);

  if (page !== "board") url.searchParams.set(PAGE_QUERY_PARAM, page);
  else url.searchParams.delete(PAGE_QUERY_PARAM);

  if (cardId) url.searchParams.set(CARD_QUERY_PARAM, cardId.trim());
  else url.searchParams.delete(CARD_QUERY_PARAM);

  // 换页链接不带上一页的片段（设置页目录 `#settings-<id>` 点过之后 location.href 会带着它）——
  // 片段是页内锚点，不是路由的一部分
  url.hash = "";
  return url;
}

/** 设置页某一区的深链：?page=settings&anchor=<id>（横幅 / 诊断条 / 向导 / 权限体检指向依赖检查区都走这里） */
export function buildSettingsUrl(href: string, anchor: string): URL {
  const url = buildAppUrl(href, "settings", null);
  url.searchParams.set(ANCHOR_QUERY_PARAM, anchor);
  return url;
}

/** 摘掉设置页深链的锚点（`?anchor=` 与 `#settings-<id>` 片段；`?page=` 不动）：设置页挂载读过一次锚点就 replaceState 成这个形——
 *  buildAppUrl 走 `new URL(href)`、原样带着 query 与 hash 去别页再回来，不摘就每次回到设置页都重新展开 + 记住 + 滚动（D44，§68.1 追记） */
export function withoutSettingsAnchor(href: string): URL {
  const url = new URL(href);
  url.searchParams.delete(ANCHOR_QUERY_PARAM);
  if (url.hash.startsWith("#settings-")) url.hash = "";
  return url;
}

// ----- 客户端路由（D40）：订阅 / 导航 / 后退前进 / 链接委托 / 滚动记忆 ----------------------------- #

const routeListeners = new Set<() => void>();

/** 路由变了（navigate / popstate）就叫一声；返回退订。store.syncRouteFromUrl 与 useRoute 都挂在这里 */
export function subscribeRoute(listener: () => void): () => void {
  routeListeners.add(listener);
  return () => {
    routeListeners.delete(listener);
  };
}

function emitRoute() {
  for (const listener of routeListeners) listener();
}

function currentSearch(): string {
  return window.location.search;
}

/** 组件读当前路由的唯一入口：返回 `location.search`（字符串，按值比较——同一 URL 不重渲染），navigate / popstate 后重渲染。
 *  页 = `readPage(useRoute())`，设置页 anchor = `readSettingsAnchor(useRoute())`。 */
export function useRoute(): string {
  return useSyncExternalStore(subscribeRoute, currentSearch, currentSearch);
}

/** 这个 URL 是不是本 SPA 的一页（同 origin + 同路径；`?page=` / `?card=` 都是 query，片段不算）——
 *  `/api/…` `/files/…` 与别的 origin 不是，交给浏览器整页走 */
export function isAppUrl(url: URL, current: { origin: string; pathname: string } = window.location): boolean {
  return url.origin === current.origin && url.pathname === current.pathname;
}

/** 只有片段不同（设置页目录 `#settings-<id>`）：那是页内锚点，交给浏览器原生滚动，不进路由 */
export function isHashOnlyChange(url: URL, current: { search: string } = window.location): boolean {
  return url.search === current.search && url.hash !== "";
}

/**
 * 换页（rail / ⌘1…⌘7 / `/open` / 「← 返回看板」/ 向导完成 / 壳的 open_page 命令都走这一条）：
 * 本 SPA 的 URL → `pushState`（`replace=true` 或目标就是当前 URL → `replaceState`，不叠历史）+ 通知订阅者，**不重载**；
 * 不属于本 SPA 的 URL（别的路径 / origin）→ 仍是整页 `location.assign` / `replace`。
 * 离开当前页前先记下它的滚动位置（restoreScroll 回来还原）。
 */
export function navigate(url: URL | string, replace = false): void {
  const target = new URL(String(url), window.location.href);
  if (!isAppUrl(target)) {
    if (replace) window.location.replace(target.href);
    else window.location.assign(target.href);
    return;
  }
  rememberScroll(readPage(window.location.search));
  if (replace || target.href === window.location.href) window.history.replaceState(null, "", target);
  else window.history.pushState(null, "", target);
  emitRoute();
}

/**
 * 文档级链接委托：左键、无修饰键（⌘ / ⌃ / ⇧ / ⌥ 点是浏览器的「新标签 / 新窗口 / 下载」手势，归浏览器）、没被更内层的
 * 处理器 `preventDefault`、`<a href>` 没有 target（`_self` 除外）/ download、目标是本 SPA 的 URL 且不是纯片段跳转
 * → `preventDefault` + `navigate(href)`。返回停止函数。
 */
export function interceptAppLinks(root: Pick<Document, "addEventListener" | "removeEventListener"> = document): () => void {
  const onClick = (event: Event) => {
    const mouse = event as MouseEvent;
    if (mouse.defaultPrevented || mouse.button !== 0) return;
    if (mouse.metaKey || mouse.ctrlKey || mouse.shiftKey || mouse.altKey) return;
    const target = mouse.target;
    if (!(target instanceof Element)) return;
    const anchor = target.closest("a[href]");
    if (!(anchor instanceof HTMLAnchorElement)) return;
    if (anchor.target && anchor.target !== "_self") return;
    if (anchor.hasAttribute("download")) return;
    let url: URL;
    try {
      url = new URL(anchor.href, window.location.href);
    } catch {
      return;
    }
    if (!isAppUrl(url) || isHashOnlyChange(url)) return;
    mouse.preventDefault();
    navigate(url);
  };
  root.addEventListener("click", onClick);
  return () => root.removeEventListener("click", onClick);
}

/**
 * 启动路由器（App 挂载时一次）：popstate（后退 / 前进）→ 记下刚离开那页的滚动位置 + 通知订阅者；文档级链接委托；
 * `history.scrollRestoration = "manual"`——同一文档内换页由 restoreScroll 自己还原，不让浏览器在 React 还没换内容时
 * 先把旧页滚一下。返回停止函数。
 */
export function startRouter(): () => void {
  let routedPage = readPage(window.location.search);
  const stopRoute = subscribeRoute(() => {
    routedPage = readPage(window.location.search);
  });
  const onPop = () => {
    // popstate 触发时 URL 已经是新的，DOM 还是旧页的——给旧页记滚动
    rememberScroll(routedPage);
    emitRoute();
  };
  try {
    if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";
  } catch {
    /* 老浏览器 / 只读：忽略 */
  }
  window.addEventListener("popstate", onPop);
  const stopLinks = interceptAppLinks();
  return () => {
    window.removeEventListener("popstate", onPop);
    stopLinks();
    stopRoute();
  };
}

// ----- 滚动记忆：按页记 window 滚动 + 任何 `[data-scroll-memory="<key>"]` 滚动容器（列独立滚动的看板日后挂这个属性即可） ----- #

interface ScrollSnapshot {
  x: number;
  y: number;
  parts: Record<string, { left: number; top: number }>;
}

const scrollMemory = new Map<AppPage, ScrollSnapshot>();

/** 离开 `page` 前记下它的滚动位置（navigate / popstate 调；同一页记最后一次） */
export function rememberScroll(page: AppPage, doc: Document = document): void {
  const parts: ScrollSnapshot["parts"] = {};
  for (const el of Array.from(doc.querySelectorAll<HTMLElement>("[data-scroll-memory]"))) {
    const key = el.dataset.scrollMemory;
    if (key) parts[key] = { left: el.scrollLeft, top: el.scrollTop };
  }
  scrollMemory.set(page, { x: window.scrollX, y: window.scrollY, parts });
}

/** 回到 `page`（DOM 已换好）：有记忆就还原到那里，没有就到顶——整页导航的默认行为 */
export function restoreScroll(page: AppPage, doc: Document = document): void {
  const snapshot = scrollMemory.get(page);
  if (!snapshot) {
    if (window.scrollX !== 0 || window.scrollY !== 0) window.scrollTo(0, 0);
    return;
  }
  window.scrollTo(snapshot.x, snapshot.y);
  for (const el of Array.from(doc.querySelectorAll<HTMLElement>("[data-scroll-memory]"))) {
    const saved = el.dataset.scrollMemory ? snapshot.parts[el.dataset.scrollMemory] : undefined;
    if (saved) {
      el.scrollLeft = saved.left;
      el.scrollTop = saved.top;
    }
  }
}

/** 仅测试用：清空滚动记忆与订阅者 */
export function resetRouterForTests(): void {
  scrollMemory.clear();
  routeListeners.clear();
}
