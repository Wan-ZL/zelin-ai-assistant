// App 壳（刻意薄）：只做五件事——语言接线、realtime 生命周期（SSE + /api/health 轮询）、页面分发、
// 壳桥接线（Dock 徽章 / 全局快速捕获命令 / 壳菜单换页 / 首次运行向导跳转）、客户端路由器的启停（D40：换页 = pushState +
// 从 store 重渲染，不整页重载——store / SSE / 未决的「合并中…」章与多选都活过换页；去过的页回来滚动位置还原、焦点不掉到 body）。
// 布局骨架/顶栏/离线横幅/整页空态在 components/shell/AppShell（G7）；
// 一切业务 state 进 store.ts，一切板块 UI 进 pages/ 与 components/。禁止在这里堆 useState。
import { useEffect, useLayoutEffect, useRef } from "react";
import { setApiText } from "./api";
import { getI18n, LanguageContext } from "./i18n";
import {
  buildAppUrl, focusPageRoot, isDepsPage, navigate, readAnchor, readPage, readSettingsAnchor, restoreScroll, startRouter, subscribeRoute,
  useRoute, type AppPage,
} from "./route";
import { createBoardRealtime } from "./realtime";
import { onShellCommand, pushBadge } from "./shellBridge";
import {
  hydrateLanguage, refreshBoard, refreshDisplaySettings, refreshFailures, refreshHealth, refreshLanes, refreshSetup, setConnection,
  syncRouteFromUrl, useAppState,
} from "./store";
import { focusComposer } from "./components/board/focusComposer";
import { AppShell } from "./components/shell/AppShell";
import { rememberMainSection, restoreMainSection } from "./components/shell/NavRail";
import { FilterBar } from "./components/chrome/FilterBar";
import { DetailDrawer } from "./components/detail/DetailDrawer";
import { AboutPage } from "./pages/AboutPage";
import { ArchivePage } from "./pages/ArchivePage";
import { BoardPage } from "./pages/BoardPage";
import { IngestPage } from "./pages/IngestPage";
import { RecapsPage } from "./pages/RecapsPage";
import { PermissionsPage } from "./pages/PermissionsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { isSetupSkipped, SetupPage } from "./pages/SetupPage";
import { StyleguidePage } from "./pages/StyleguidePage";
import { TrashPage } from "./pages/TrashPage";

const HEALTH_POLL_MS = 30_000;

/** 等你动作的卡数 = Dock 徽章（原生 §15 v0.46 ②：待拍板 + 需输入 + 待验收；counts 真实总数优先） */
export function badgeCount(board: { counts?: Record<string, number>; needs_approval?: unknown[]; needs_input?: unknown[]; review?: unknown[] } | null): number {
  if (!board) return 0;
  const n = (key: "needs_approval" | "needs_input" | "review") => board.counts?.[key] ?? (Array.isArray(board[key]) ? board[key]!.length : 0);
  return n("needs_approval") + n("needs_input") + n("review");
}

/** 首次运行向导跳转（§68.5）：setup.needed 且当前在看板页 → 换到 ?page=setup（一次性、replaceState 不进历史栈）。
 *  本窗口会话里点过「先去看板（下次再来）」（sessionStorage 标记，原生关窗 = 这次不问）→ 不跳；新开窗口再问。 */
export function shouldRedirectToSetup(page: AppPage, needed: boolean | undefined): boolean {
  return page === "board" && needed === true && !isSetupSkipped();
}

/** 壳的 `open_page {page, anchor?}` 命令（D40，§61.6）→ 目标 URL：page 过 readPage 白名单（不认识的值 = 看板）、anchor 过
 *  readAnchor 同款校验；与壳整页加载时的 `?page=<p>&anchor=<a>` 深链同形（shell ShellConfig.pageURL），只是不重载。 */
export function shellPageUrl(args: Record<string, unknown>, href = window.location.href): URL {
  const page = typeof args.page === "string" ? readPage(`?page=${encodeURIComponent(args.page)}`) : "board";
  const anchor = typeof args.anchor === "string" ? readAnchor(`?anchor=${encodeURIComponent(args.anchor)}`) : null;
  const url = buildAppUrl(href, page, null); // 页内 query（anchor / log / step）不继承——壳说「设置页顶部」就是顶部
  if (anchor) url.searchParams.set("anchor", anchor);
  return url;
}

function renderPage(page: AppPage) {
  switch (page) {
    case "trash": return <TrashPage />;
    case "settings": return <SettingsPage />;
    case "styleguide": return <StyleguidePage />;
    case "recaps": return <RecapsPage />;
    case "archive": return <ArchivePage />;
    case "permissions": return <PermissionsPage />;
    // 依赖检查的两个旧深链（原生 rail 名 deps / 更早的 diagnostics）：D30 起是设置页的一区，SettingsPage 按 readSettingsAnchor 滚到它
    case "diagnostics": case "deps": return <SettingsPage />;
    case "setup": return <SetupPage />;
    case "ingest": return <IngestPage />;
    case "about": return <AboutPage />;
    default: return <BoardPage />;
  }
}

export function App() {
  // 语言读 store（D37 §15：真源是 server 的 general.language——首帧先按 ?lang= > localStorage 缓存 > 浏览器猜一下，挂载后 hydrateLanguage
  // 对齐；切换经 chooseLanguage 立刻切 + 写回 server）
  const { language, board, setup } = useAppState();
  // 路由真源是 URL（route.useRoute 订阅 location.search；navigate / popstate 后重渲染——D40 客户端路由）
  const search = useRoute();
  const page = readPage(search);
  // 设置页的 anchor 深链（含 ?page=deps / diagnostics 旧深链）由 SettingsPage 自己滚到那一区——只在设置页上才算：别的页 URL 上
  // 万一残留的 ?anchor= 不许压掉滚动还原（buildAppUrl 已不带页内 query，这里是第二道）
  const settingsAnchor = page === "settings" || isDepsPage(page) ? readSettingsAnchor(search) : null;
  const lastPage = useRef<AppPage | null>(null);

  // api.ts 无 React：错误文案的语言经注入接线（语言切换后重注入，幂等）
  setApiText(getI18n(language).text);

  useEffect(() => {
    void refreshBoard();
    void refreshHealth();
    void refreshLanes(); // 列头「?」说明文案（server-owned 目录，§54；静态，拉一次）
    void refreshFailures(); // §25 失败目录双语句（server-owned，GET /api/failures）：卡片错误行按 failure id 说人话；静态，拉一次
    void refreshDisplaySettings(); // 字号 / 字重 / 描边（§54.1 第 12 项）：到达即落 <html> data-*，首帧由 index.html 的缓存顶住
    void refreshSetup(); // §68.5 首次运行判定（config.yaml / 凭证 / 完成标记）
    // D37（§15 追记）：语言的真源是 server 的 general.language——显式值压过首帧缓存；谁都没选过（source default）就把此刻正显示的
    // 语言写进设置一次（原生 L10n.swift 首启持久化：launchd 下的 python 没有 LANG）；?lang= 在场 = 一次性覆写，不水合不写
    void hydrateLanguage();
    // D40 客户端路由：popstate + 文档级链接委托；URL 变了把不进历史栈的过滤器 / ?card= 同步回 store。
    // 这两条与 realtime 同寿命——换页不再重建文档，SSE 只连一次、看板 store 一直是同一份
    const stopRouter = startRouter();
    const stopRouteSync = subscribeRoute(syncRouteFromUrl);
    const realtime = createBoardRealtime({
      onRefetch: () => void refreshBoard(),
      onConnectionChange: setConnection,
    });
    realtime.start();
    // §47.4 管线活性轮询：心跳的 stale 阈值下限 90s，30s 一拉足够及时且几乎零成本
    // （server 只 stat 三个文件）。SSE 的 board.updated 不携带心跳，所以要独立拉。
    const healthTimer = setInterval(() => void refreshHealth(), HEALTH_POLL_MS);
    // §68.13 壳的全局快捷键 ⌃⌥Space / 壳菜单 View ▸ 聚焦捕获框（⌘L）→ quick_capture：与 rail 的 ⌘L 同一落点
    // focusComposer（§54.4 2026-09-05 追记）——聚焦提案列 composer，不在看板页就留接力棒先回看板；
    // 壳菜单 关于 / 设置… / 权限体检… 与字幕悬浮窗齿轮 → open_page {page, anchor?}（D40）：SPA 自己换页，不重载
    const stopCommands = onShellCommand((command, args) => {
      if (command === "quick_capture") focusComposer();
      else if (command === "open_page") navigate(shellPageUrl(args));
    });
    return () => {
      realtime.stop();
      clearInterval(healthTimer);
      stopCommands();
      stopRouteSync();
      stopRouter();
    };
  }, []);

  // Dock 徽章跟随看板（壳不在场 = no-op）
  useEffect(() => {
    pushBadge(badgeCount(board));
  }, [board]);

  // 原生 MainNav 的 `mainSection`：冷启动回到上次的 rail 页（URL 没指定去处时），否则记住当前页
  useEffect(() => {
    const target = restoreMainSection(window.location.search);
    if (target) navigate(buildAppUrl(window.location.href, target, null), true);
    else rememberMainSection(page);
  }, [page]);

  // 换页后的滚动与焦点（D40）——只在**页变了**那一拍做：同一页上 URL 的其它变化（anchor 被 SettingsPage 消费掉、?card= 抽屉、
  // 过滤器）不是换页，不许把刚滚到的 anchor 区又拉回去。滚动：记过的页还原到离开时的位置（看板在内），第一次到的页到顶（整页导航
  // 的默认行为）；带 anchor 的设置页深链不动（SettingsPage 滚到那一区）。焦点：整页导航会把焦点归零、读屏器报新文档标题；pushState
  // 换页时刚点的「← 返回看板」随旧页卸载、焦点掉到 <body>——放到 <main>（AppShell tabIndex=-1），读屏器报到主区、Tab 从新页起步；
  // 焦点还在（rail 项、⌘1…⌘7 时的输入框）就不动。layout effect：DOM 已换好、还没绘出——不闪一帧顶部
  useLayoutEffect(() => {
    const previous = lastPage.current;
    if (previous === page) return;
    lastPage.current = page;
    if (!settingsAnchor) restoreScroll(page);
    if (previous !== null) focusPageRoot();
  }, [page, settingsAnchor]);

  // 首次运行向导：空环境（无 config / 无凭证、且没走完向导）时看板开在向导页
  useEffect(() => {
    if (shouldRedirectToSetup(page, setup?.needed)) {
      navigate(buildAppUrl(window.location.href, "setup", null), true);
    }
  }, [page, setup]);

  return (
    <LanguageContext.Provider value={language}>
      {/* searchSlot = A8 过滤 chips + ⌘F 搜索（G4）；页面分发见 renderPage（?page= 路由，route.ts） */}
      <AppShell searchSlot={<FilterBar />}>
        {renderPage(page)}
        {/* 详情抽屉（G3）：无选中卡时渲染 null，任何页面下挂载都安全 */}
        <DetailDrawer />
      </AppShell>
    </LanguageContext.Provider>
  );
}
