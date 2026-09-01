// App 外壳（G7，自写非 fork）：顶栏 + 离线横幅 + 主内容区的布局骨架。
// app.tsx 保持薄——页面组件作为 children 传进来，这里只管壳层关注点：
//   1. 整页状态分派：首载 loading / 从未加载成功且离线（诚实空态+恢复路径）/ 正常渲染页面；
//   2. <html lang> 与 document.title 随语言同步；
//   3. 有旧快照时的降级横幅（ErrorBanner 自读 store，条件互斥不双报）；
//   4. 管线健康横幅（PipelineBanner，§47.4：actd 卡住/连崩/没跑——server 可达时才说话）。
import { useEffect, type ReactNode } from "react";
import { useI18n } from "../../i18n";
import { refreshBoard, useAppState } from "../../store";
import { EmptyState } from "./EmptyState";
import { ErrorBanner } from "./ErrorBanner";
import { HeaderBar } from "./HeaderBar";
import { PipelineBanner } from "./PipelineBanner";

export interface AppShellProps {
  /** 透传给 HeaderBar 的搜索/过滤槽位（A8 组件经 app.tsx 注入） */
  searchSlot?: ReactNode;
  children: ReactNode;
}

export function AppShell({ searchSlot, children }: AppShellProps) {
  const { language, text } = useI18n();
  const { board, boardError, boardLoading } = useAppState();

  // 语言变化时同步文档级属性（无障碍朗读与标签页标题跟随 UI 语言）
  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
    document.title = text("Zelin 的 AI 助理 · 看板", "Zelin's AI Assistant · Board");
  }, [language, text]);

  let content: ReactNode;
  if (board) {
    content = children; // 有快照就渲染页面（离线降级由 ErrorBanner 声明）
  } else if (boardLoading) {
    content = (
      <div className="shell-center">
        <EmptyState
          icon={<span className="shell-spinner" />}
          title={text("正在加载看板…", "Loading the board…")}
        />
      </div>
    );
  } else {
    // 从未加载成功 + 读失败：诚实说明原因与恢复路径（对齐 Mac 版 PipelineEmptyStateView 的精神）
    content = (
      <div className="shell-center">
        <EmptyState
          icon={
            <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M12 2.8 22.6 21H1.4L12 2.8Zm0 6.2a1 1 0 0 0-1 1v4a1 1 0 1 0 2 0v-4a1 1 0 0 0-1-1Zm0 8a1.2 1.2 0 1 0 0 2.4 1.2 1.2 0 0 0 0-2.4Z"
                fill="currentColor"
              />
            </svg>
          }
          title={text("连不上本地服务", "Can't reach the local server")}
          hint={
            (boardError ?? "")
            + text(
              " 请确认 server 正在运行（scripts/dev-preview.sh 可一键拉起）；连上后本页会自动恢复。",
              " Make sure the server is running (scripts/dev-preview.sh starts everything); this page recovers automatically once it's back.",
            )
          }
          action={
            <button type="button" className="shell-button" onClick={() => void refreshBoard()}>
              {text("重试", "Retry")}
            </button>
          }
        />
      </div>
    );
  }

  return (
    <div className="shell">
      <HeaderBar searchSlot={searchSlot} />
      <ErrorBanner />
      {/* §47.4 管线健康（后台服务卡住/崩/停）——与离线横幅互斥，见组件头注 */}
      <PipelineBanner />
      <main className="shell-main">{content}</main>
    </div>
  );
}
