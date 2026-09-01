// App 壳（刻意薄）：只做三件事——语言接线、realtime 生命周期（SSE + /api/health 轮询）、页面分发。
// 布局骨架/顶栏/离线横幅/整页空态在 components/shell/AppShell（G7）；
// 一切业务 state 进 store.ts，一切板块 UI 进 pages/ 与 components/。禁止在这里堆 useState。
import { useEffect } from "react";
import { setApiText } from "./api";
import { getI18n, LanguageContext } from "./i18n";
import { readPage } from "./route";
import { createBoardRealtime } from "./realtime";
import { refreshBoard, refreshHealth, setConnection, useAppState } from "./store";
import { AppShell } from "./components/shell/AppShell";
import { FilterBar } from "./components/chrome/FilterBar";
import { DetailDrawer } from "./components/detail/DetailDrawer";
import { BoardPage } from "./pages/BoardPage";
import { SettingsPage } from "./pages/SettingsPage";
import { StyleguidePage } from "./pages/StyleguidePage";
import { TrashPage } from "./pages/TrashPage";

const HEALTH_POLL_MS = 30_000;

export function App() {
  // 语言真源在 store（初值解析 ?lang= > localStorage zai.lang > 浏览器；切换经 setLanguage）
  const { language } = useAppState();
  const page = readPage(window.location.search);

  // api.ts 无 React：错误文案的语言经注入接线（语言切换后重注入，幂等）
  setApiText(getI18n(language).text);

  useEffect(() => {
    void refreshBoard();
    void refreshHealth();
    const realtime = createBoardRealtime({
      onRefetch: () => void refreshBoard(),
      onConnectionChange: setConnection,
    });
    realtime.start();
    // §47.4 管线活性轮询：心跳的 stale 阈值下限 90s，30s 一拉足够及时且几乎零成本
    // （server 只 stat 三个文件）。SSE 的 board.updated 不携带心跳，所以要独立拉。
    const healthTimer = setInterval(() => void refreshHealth(), HEALTH_POLL_MS);
    return () => {
      realtime.stop();
      clearInterval(healthTimer);
    };
  }, []);

  return (
    <LanguageContext.Provider value={language}>
      {/* searchSlot = A8 过滤 chips + ⌘F 搜索（G4）；页面分发：?page=trash → 回收站页，
          ?page=settings → 设置页（§59），?page=styleguide → 活体样式指南（开发者页，URL 直达） */}
      <AppShell searchSlot={<FilterBar />}>
        {page === "trash" ? <TrashPage />
          : page === "settings" ? <SettingsPage />
            : page === "styleguide" ? <StyleguidePage />
              : <BoardPage />}
        {/* 详情抽屉（G3）：无选中卡时渲染 null，任何页面下挂载都安全 */}
        <DetailDrawer />
      </AppShell>
    </LanguageContext.Provider>
  );
}
