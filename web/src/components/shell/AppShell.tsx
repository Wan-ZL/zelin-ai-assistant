// App 外壳（G7，自写非 fork）：左侧导航栏 + 顶栏 + 主内容区的布局骨架（§54.4：布局跟原生 MainWindow——sidebar 通高在左，
// 顶栏与内容在右）。app.tsx 保持薄——页面组件作为 children 传进来，这里只管壳层关注点：
//   1. <html lang> 与 document.title 随语言与当前页同步（原生 installTitleSink：「Zelin's AI Assistant — <页>」，pageTitles.ts）；
//   2. 页面一律无条件渲染：v2.0.0 起每一页都自拉自己的快照、自报读失败（会议纪要页读 GET /api/recaps）。
// v2.0.0 退役（全文见 tag full）：读 dashboard.json 的整页状态分派（加载 / 离线 / 文件缺席 / 解不出来）与
// 离线 / 管线健康 / 自我改进 / 每日整理横幅、板级诊断条——它们说的都是退役的 Act 管线。
import { useEffect, type ReactNode } from "react";
import { useI18n } from "../../i18n";
import { readPage, useRoute } from "../../route";
import { HeaderBar } from "./HeaderBar";
import { NavRail } from "./NavRail";
import { pageTitle } from "./pageTitles";

export interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const { language, text } = useI18n();
  const page = readPage(useRoute()); // D40：换页不重载，页从路由订阅里来

  // 语言 / 页变化时同步文档级属性（无障碍朗读随 UI 语言；标签页标题 = 原生窗口标题「Zelin's AI Assistant — <页>」；
  // 壳经 WKWebView.title KVO 把它抬成窗口标题，pushState 换页后照样跟）
  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
    document.title = pageTitle(page, text);
  }, [language, text, page]);

  return (
    <div className="shell">
      {/* §54.4 左侧导航栏：会议纪要 / 录制与导入 / 设置 / 关于，通高、可折叠 */}
      <NavRail />
      <div className="shell-body">
        <HeaderBar />
        {/* tabIndex=-1：pushState 换页后焦点掉到 body 时 route.focusPageRoot 把焦点放到这里（读屏器报到主区、Tab 从新页起步，D40）。
            data-scroll-memory：D42 起文档不滚、页面在这里滚（shell.css overflow: auto）——换页时 route.rememberScroll / restoreScroll
            按页记还原它的位置，第一次到的页归零（这个 <main> 跨页常驻，不归零就把上一页滚到的位置带进新页） */}
        <main className="shell-main" tabIndex={-1} data-scroll-memory="shell-main">{children}</main>
      </div>
    </div>
  );
}
