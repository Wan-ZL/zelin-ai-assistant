// App 壳（刻意薄）：只做四件事——语言接线、realtime 生命周期（SSE + /api/health 轮询）、页面分发、
// 壳桥接线（Dock 徽章 / 全局快速捕获命令 / 首次运行向导跳转）。
// 布局骨架/顶栏/离线横幅/整页空态在 components/shell/AppShell（G7）；
// 一切业务 state 进 store.ts，一切板块 UI 进 pages/ 与 components/。禁止在这里堆 useState。
import { useEffect } from "react";
import { setApiText } from "./api";
import { getI18n, LanguageContext } from "./i18n";
import { buildAppUrl, navigate, readPage, type AppPage } from "./route";
import { createBoardRealtime } from "./realtime";
import { onShellCommand, pushBadge } from "./shellBridge";
import { refreshBoard, refreshDisplaySettings, refreshHealth, refreshLanes, refreshSetup, setConnection, useAppState } from "./store";
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
import { SetupPage } from "./pages/SetupPage";
import { StyleguidePage } from "./pages/StyleguidePage";
import { TrashPage } from "./pages/TrashPage";

const HEALTH_POLL_MS = 30_000;

/** 等你动作的卡数 = Dock 徽章（原生 §15 v0.46 ②：待拍板 + 需输入 + 待验收；counts 真实总数优先） */
export function badgeCount(board: { counts?: Record<string, number>; needs_approval?: unknown[]; needs_input?: unknown[]; review?: unknown[] } | null): number {
  if (!board) return 0;
  const n = (key: "needs_approval" | "needs_input" | "review") => board.counts?.[key] ?? (Array.isArray(board[key]) ? board[key]!.length : 0);
  return n("needs_approval") + n("needs_input") + n("review");
}

/** 首次运行向导跳转（§68.5）：setup.needed 且当前在看板页 → 换到 ?page=setup（一次性、整页导航） */
export function shouldRedirectToSetup(page: AppPage, needed: boolean | undefined): boolean {
  return page === "board" && needed === true;
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
  // 语言真源在 store（初值解析 ?lang= > localStorage zai.lang > 浏览器；切换经 setLanguage）
  const { language, board, setup } = useAppState();
  const page = readPage(window.location.search);

  // api.ts 无 React：错误文案的语言经注入接线（语言切换后重注入，幂等）
  setApiText(getI18n(language).text);

  useEffect(() => {
    void refreshBoard();
    void refreshHealth();
    void refreshLanes(); // 列头「?」说明文案（server-owned 目录，§54；静态，拉一次）
    void refreshDisplaySettings(); // 字号 / 字重 / 描边（§54.1 第 12 项）：到达即落 <html> data-*，首帧由 index.html 的缓存顶住
    void refreshSetup(); // §68.5 首次运行判定（config.yaml / 凭证 / 完成标记）
    const realtime = createBoardRealtime({
      onRefetch: () => void refreshBoard(),
      onConnectionChange: setConnection,
    });
    realtime.start();
    // §47.4 管线活性轮询：心跳的 stale 阈值下限 90s，30s 一拉足够及时且几乎零成本
    // （server 只 stat 三个文件）。SSE 的 board.updated 不携带心跳，所以要独立拉。
    const healthTimer = setInterval(() => void refreshHealth(), HEALTH_POLL_MS);
    // §61.6 壳的全局快捷键 → quick_capture：聚焦提案列 composer（不在看板页就先回看板）
    const stopCommands = onShellCommand((command) => {
      if (command !== "quick_capture") return;
      if (readPage(window.location.search) !== "board") {
        navigate(buildAppUrl(window.location.href, "board", null));
        return;
      }
      const input = document.querySelector<HTMLTextAreaElement>(".board-column .lane-composer textarea");
      input?.focus();
      input?.select();
    });
    return () => {
      realtime.stop();
      clearInterval(healthTimer);
      stopCommands();
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
