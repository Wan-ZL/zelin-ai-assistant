// App 壳（刻意薄）：只做五件事——语言接线、realtime 生命周期（SSE recaps.updated + /api/health 轮询）、页面分发、
// 壳桥接线（壳菜单换页 / 首次运行向导跳转）、客户端路由器的启停（D40：换页 = pushState + 从 store 重渲染，不整页重载——
// store / SSE 都活过换页；去过的页回来滚动位置还原、焦点不掉到 body）。
// 布局骨架 / 顶栏 / 导航栏在 components/shell/AppShell；一切业务 state 进 store.ts，一切板块 UI 进 pages/ 与 components/。
// 禁止在这里堆 useState。v2.0.0：默认页 = 会议纪要；看板、过滤栏、详情抽屉、Dock 徽章与全局快速捕获随 Act 管线退役（全文见 tag full）。
import { useEffect, useLayoutEffect, useRef } from "react";
import { setApiText } from "./api";
import { getI18n, LanguageContext } from "./i18n";
import {
  buildAppUrl, DEFAULT_PAGE, focusPageRoot, isDepsPage, navigate, readAnchor, readPage, readSettingsAnchor, restoreScroll, startRouter,
  useRoute, type AppPage,
} from "./route";
import { createRecapsRealtime } from "./realtime";
import { onShellCommand } from "./shellBridge";
import {
  hydrateLanguage, refreshDisplaySettings, refreshFailures, refreshHealth, refreshRecaps, refreshSetup, setConnection, useAppState,
} from "./store";
import { AppShell } from "./components/shell/AppShell";
import { rememberMainSection, restoreMainSection } from "./components/shell/NavRail";
import { AboutPage } from "./pages/AboutPage";
import { IngestPage } from "./pages/IngestPage";
import { PermissionsPage } from "./pages/PermissionsPage";
import { RecapsPage } from "./pages/RecapsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { isSetupSkipped, SetupPage } from "./pages/SetupPage";

const HEALTH_POLL_MS = 30_000;

/** 首次运行向导跳转（§68.5）：setup.needed 且当前在缺省页（会议纪要）→ 换到 ?page=setup（一次性、replaceState 不进历史栈）。
 *  本窗口会话里点过「先跳过（下次再来）」（sessionStorage 标记，原生关窗 = 这次不问）→ 不跳；新开窗口再问。 */
export function shouldRedirectToSetup(page: AppPage, needed: boolean | undefined): boolean {
  return page === DEFAULT_PAGE && needed === true && !isSetupSkipped();
}

/** 壳的 `open_page {page, anchor?}` 命令（D40，§61.6）→ 目标 URL：page 过 readPage 白名单（不认识的值 = 缺省页）、anchor 过
 *  readAnchor 同款校验；与壳整页加载时的 `?page=<p>&anchor=<a>` 深链同形（shell ShellConfig.pageURL），只是不重载。 */
export function shellPageUrl(args: Record<string, unknown>, href = window.location.href): URL {
  const page = typeof args.page === "string" ? readPage(`?page=${encodeURIComponent(args.page)}`) : DEFAULT_PAGE;
  const anchor = typeof args.anchor === "string" ? readAnchor(`?anchor=${encodeURIComponent(args.anchor)}`) : null;
  const url = buildAppUrl(href, page); // 页内 query（anchor / log / step）不继承——壳说「设置页顶部」就是顶部
  if (anchor) url.searchParams.set("anchor", anchor);
  return url;
}

function renderPage(page: AppPage) {
  switch (page) {
    case "settings": return <SettingsPage />;
    case "permissions": return <PermissionsPage />;
    // 依赖检查的两个旧深链（原生 rail 名 deps / 更早的 diagnostics）：D30 起是设置页的一区，SettingsPage 按 readSettingsAnchor 滚到它
    case "diagnostics": case "deps": return <SettingsPage />;
    case "setup": return <SetupPage />;
    case "ingest": return <IngestPage />;
    case "about": return <AboutPage />;
    default: return <RecapsPage />;
  }
}

export function App() {
  // 语言读 store（D37 §15：真源是 server 的 general.language——首帧先按 ?lang= > localStorage 缓存 > 浏览器猜一下，挂载后 hydrateLanguage
  // 对齐；切换经 chooseLanguage 立刻切 + 写回 server）
  const { language, setup } = useAppState();
  // 路由真源是 URL（route.useRoute 订阅 location.search；navigate / popstate 后重渲染——D40 客户端路由）
  const search = useRoute();
  const page = readPage(search);
  // 设置页的 anchor 深链（含 ?page=deps / diagnostics 旧深链）由 SettingsPage 自己滚到那一区——只在设置页上才算
  const settingsAnchor = page === "settings" || isDepsPage(page) ? readSettingsAnchor(search) : null;
  const lastPage = useRef<AppPage | null>(null);

  // api.ts 无 React：错误文案的语言经注入接线（语言切换后重注入，幂等）
  setApiText(getI18n(language).text);

  useEffect(() => {
    void refreshRecaps();
    void refreshHealth();
    void refreshFailures(); // §25 失败目录双语句（server-owned，GET /api/failures）：依赖行 / 引擎诊断行按 failure id 说人话；静态，拉一次
    void refreshDisplaySettings(); // 字号 / 字重 / 描边（§68.1 v2.0.0 修订（原 §54.1 第 12 项，该节退役））：到达即落 <html> data-*，首帧由 index.html 的缓存顶住
    void refreshSetup(); // §68.5 首次运行判定（config.yaml / 凭证 / 完成标记）
    // D37（§15 追记）：语言的真源是 server 的 general.language——显式值压过首帧缓存；谁都没选过（source default）就把此刻正显示的
    // 语言写进设置一次（launchd 下的 python 没有 LANG）；?lang= 在场 = 一次性覆写，不水合不写
    void hydrateLanguage();
    // D40 客户端路由：popstate + 文档级链接委托——与 realtime 同寿命，SSE 只连一次
    const stopRouter = startRouter();
    const realtime = createRecapsRealtime({
      onRefetch: () => void refreshRecaps(),
      onConnectionChange: setConnection,
    });
    realtime.start();
    // §49 活性轮询：cron 探针的新鲜度按小时算，30 s 一拉足够及时且几乎零成本（server 只 stat 两个文件）
    const healthTimer = setInterval(() => void refreshHealth(), HEALTH_POLL_MS);
    // 壳菜单 关于 / 设置… / 权限体检… → open_page {page, anchor?}（D40）：SPA 自己换页，不重载
    const stopCommands = onShellCommand((command, args) => {
      if (command === "open_page") navigate(shellPageUrl(args));
    });
    return () => {
      realtime.stop();
      clearInterval(healthTimer);
      stopCommands();
      stopRouter();
    };
  }, []);

  // 原生 MainNav 的 `mainSection`：冷启动回到上次的 rail 页（URL 没指定去处时），否则记住当前页
  useEffect(() => {
    const target = restoreMainSection(window.location.search);
    if (target) navigate(buildAppUrl(window.location.href, target), true);
    else rememberMainSection(page);
  }, [page]);

  // 换页后的滚动与焦点（D40）——只在**页变了**那一拍做：同一页上 URL 的其它变化（anchor 被 SettingsPage 消费掉）不是换页，
  // 不许把刚滚到的 anchor 区又拉回去。滚动：记过的页还原到离开时的位置，第一次到的页到顶；带 anchor 的设置页深链不动
  // （SettingsPage 滚到那一区）。焦点：pushState 换页时刚点的链接随旧页卸载、焦点掉到 <body>——放到 <main>（AppShell
  // tabIndex=-1）；焦点还在就不动。layout effect：DOM 已换好、还没绘出——不闪一帧顶部
  useLayoutEffect(() => {
    const previous = lastPage.current;
    if (previous === page) return;
    lastPage.current = page;
    if (!settingsAnchor) restoreScroll(page);
    if (previous !== null) focusPageRoot();
  }, [page, settingsAnchor]);

  // 首次运行向导：空环境（无 config / 无凭证、且没走完向导）时开在向导页
  useEffect(() => {
    if (shouldRedirectToSetup(page, setup?.needed)) {
      navigate(buildAppUrl(window.location.href, "setup"), true);
    }
  }, [page, setup]);

  return (
    <LanguageContext.Provider value={language}>
      {/* 页面分发见 renderPage（?page= 路由，route.ts） */}
      <AppShell>{renderPage(page)}</AppShell>
    </LanguageContext.Provider>
  );
}
