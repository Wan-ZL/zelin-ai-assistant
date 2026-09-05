// App 外壳（G7，自写非 fork）：左侧导航栏 + 顶栏 + 离线横幅 + 主内容区的布局骨架（§54.4：
// 布局跟原生 MainWindow——sidebar 通高在左，顶栏与内容在右）。
// app.tsx 保持薄——页面组件作为 children 传进来，这里只管壳层关注点：
//   1. 整页状态分派（**只在读 /api/board 快照的页**——看板本体 + 回收站 / 永久性完成 / 会议纪要，它们读的是
//      board.trash / archived / recaps 同一份快照；原生 MainWindow.detail 的其余 section 从不依赖 store.dashboard，§54.1 追记）：
//      首载 loading / 从未加载成功且离线（诚实空态+恢复路径——「拉不到」绝不渲染成「为空」）/ dashboard.json 不存在
//      （server 在、文件不在：看板页 = 原生 PipelineEmptyStateView + 列顶 composer；三个列表页 = 空列表，原生
//      `dashboard?.trash ?? []`）/ 正常渲染页面；自拉快照的页（设置 / 关于 / 录制 / 权限体检 / 向导）无条件渲染 children；
//   2. <html lang> 与 document.title 随语言与当前页同步（原生 installTitleSink：「Zelin's AI Assistant — <页>」，pageTitles.ts）；
//   3. 有旧快照时的降级横幅（ErrorBanner 自读 store，条件互斥不双报）；
//   4. 管线健康横幅（PipelineBanner，§47.4：actd 卡住/连崩/没跑——server 可达时才说话；看板页的「没写出数据」空态
//      自己带「启动后台服务」，那时横幅闭嘴——原生 Freshness.swift「.missing is owned by PipelineEmptyStateView」）；
//   5. 自我改进通道横幅（SelfImproveBanner，§65.4：敏感路径护栏挂起通道时点名 PR 并给「恢复通道」）。
//   6. 每日整理横幅（MaintenanceBanner，§70：正在整理 / 今日整理：合并 N、清理 M）。
//   7. 板级诊断条（DiagnosticsStrip，§48：用户打开的源在静默失败——每 path 一张卡 + 一颗直达修复的按钮；只在看板页）。
import { useEffect, useState, type ReactNode } from "react";
import { postSeedDashboard } from "../../api";
import { useI18n } from "../../i18n";
import { readPage, type AppPage } from "../../route";
import { refreshBoard, refreshHealth, useAppState } from "../../store";
import { consumePendingFocus } from "../board/focusComposer";
import { LaneComposer } from "../board/LaneComposer";
import { errorMessage } from "../settings/useToast";
import { DiagnosticsStrip } from "./DiagnosticsStrip";
import { EmptyState } from "./EmptyState";
import { ErrorBanner } from "./ErrorBanner";
import { HeaderBar } from "./HeaderBar";
import { MaintenanceBanner } from "./MaintenanceBanner";
import { NavRail } from "./NavRail";
import { pageTitle } from "./pageTitles";
import { PipelineBanner, RepairButton } from "./PipelineBanner";
import { SelfImproveBanner } from "./SelfImproveBanner";

export interface AppShellProps {
  /** 透传给 HeaderBar 的搜索/过滤槽位（A8 组件经 app.tsx 注入） */
  searchSlot?: ReactNode;
  children: ReactNode;
}

/** 数据住在 `GET /api/board` 快照里的页：看板本体，加上读 `board.trash` / `board.archived` / `board.recaps` 的三个列表页
 *  （TrashPage / ArchivePage / RecapsPage）。首载 / 离线无快照时这些页由壳统一说真话——没有快照时页面自己只会渲染成
 *  「为空」（ErrorBanner 无快照不说话、PipelineBanner 离线拿不到 health）。其余页自拉快照、自报读失败，不在此列。 */
export const BOARD_FED_PAGES: ReadonlySet<AppPage> = new Set<AppPage>(["board", "trash", "archive", "recaps"]);

const WarningIcon = () => (
  <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true">
    <path
      d="M12 2.8 22.6 21H1.4L12 2.8Zm0 6.2a1 1 0 0 0-1 1v4a1 1 0 1 0 2 0v-4a1 1 0 0 0-1-1Zm0 8a1.2 1.2 0 1 0 0 2.4 1.2 1.2 0 0 0 0-2.4Z"
      fill="currentColor"
    />
  </svg>
);

/** 原生 `Image(systemName: "hourglass")`（PipelineEmptyStateView 顶部图标） */
const HourglassIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
    <path
      d="M6 2h12v2h-1v2.6c0 1.7-.9 3.3-2.3 4.2L13 12l1.7 1.2c1.4.9 2.3 2.5 2.3 4.2V20h1v2H6v-2h1v-2.6c0-1.7.9-3.3 2.3-4.2L11 12 9.3 10.8C7.9 9.9 7 8.3 7 6.6V4H6V2Z"
      fill="currentColor"
    />
  </svg>
);

/**
 * 看板页「dashboard.json 还没写出来」空态——原生 Kanban.swift emptyState 的 web 版：列顶 composer 在上
 * （capture 走 inbox 写路径、不依赖管线跑过——首次安装就能捕获），PipelineEmptyStateView 在下（Freshness.swift 文案逐字
 * + 「启动后台服务」/「打开依赖检查」= RepairButton 的 stale 形，§68.8）；web 另给「立即生成一次」= `POST /api/setup/seed-dashboard`
 * （向导末步同一条路，§68.5）。导出供判例直渲。
 */
export function BoardMissingState() {
  const { text } = useI18n();
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState<string | null>(null);

  // ⌘L / quick_capture 从别的页过来（focusComposer 留下的 sessionStorage 接力棒，§54.4 2026-09-05 追记）：这一态里
  // 看板页的 body 是本组件而不是 BoardPage，所以由这里补上那一下聚焦；同页的 ⌘L / 命令走 focusComposerField 的
  // `.shell-board-missing` 退路，不用再订阅一遍（原生 Kanban.emptyState 的 KanbanComposer 同样收 .focusCaptureField）。
  useEffect(() => {
    consumePendingFocus();
  }, []);

  const seed = async () => {
    setSeeding(true);
    setSeedError(null);
    try {
      const receipt = await postSeedDashboard();
      if (!receipt.ok) setSeedError(receipt.error ?? "");
      else await Promise.all([refreshHealth(), refreshBoard()]);
    } catch (error) {
      setSeedError(errorMessage(error));
    } finally {
      setSeeding(false);
    }
  };

  return (
    <div className="shell-board-missing" data-board-missing="true">
      <div className="shell-board-missing-composer">
        <LaneComposer
          placeholder={text("一句话，AI 来研究并提案…", "One sentence — AI researches and proposes…")}
          submitLabel={text("捕获", "Capture")}
          successNote={text("已提交，AI 分析中（通常 2-3 分钟）", "Submitted; AI is analyzing (usually 2-3 min)")}
          buildBody={(t) => ({ action: "capture", text: t })}
        />
      </div>
      <EmptyState
        align="start"
        icon={<HourglassIcon />}
        title={text("后台服务还没写出数据", "The background service hasn't produced data yet")}
        hint={text(
          "首次安装或服务未启动时会这样。点「启动后台服务」原地拉起它。",
          "This happens on a fresh install or when the service isn't running. \"Start service\" launches it in place.",
        )}
        action={
          <div className="shell-empty-actions">
            <RepairButton verdict="stale" />
            <button type="button" className="btn" disabled={seeding} onClick={() => void seed()}>
              {seeding ? text("生成中…", "Seeding…") : text("立即生成一次", "Generate now")}
            </button>
            {seedError != null && (
              <span className="shell-banner-note" role="alert">
                <span>{text("生成失败: ", "Seeding failed: ")}</span>
                <span>{seedError}</span>
              </span>
            )}
          </div>
        }
      />
    </div>
  );
}

export function AppShell({ searchSlot, children }: AppShellProps) {
  const { language, text } = useI18n();
  const { board, boardError, boardMissing, boardLoading } = useAppState();
  const page = readPage(window.location.search);
  const isBoard = page === "board";
  const readsBoard = BOARD_FED_PAGES.has(page);

  // 语言 / 页变化时同步文档级属性（无障碍朗读随 UI 语言；标签页标题 = 原生窗口标题「Zelin's AI Assistant — <页>」）
  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
    document.title = pageTitle(page, text);
  }, [language, text, page]);

  let content: ReactNode;
  if (!readsBoard || board) {
    // 自拉快照的页从不等看板（原生 MainWindow.detail：settings / about / ingest / permissions / setup 各读各的）；
    // 读看板快照的页有快照就渲染（离线降级由 ErrorBanner 声明）
    content = children;
  } else if (boardLoading) {
    content = (
      <div className="shell-center">
        <EmptyState
          icon={<span className="shell-spinner" />}
          title={text("正在加载看板…", "Loading the board…")}
        />
      </div>
    );
  } else if (boardMissing) {
    // server 在、dashboard.json 不在（404）：不是离线——看板页 = 原生 PipelineEmptyStateView + composer；
    // 回收站 / 永久性完成 / 会议纪要 = 空列表（原生 TrashPageView 读 `dashboard?.trash ?? []`，健康横幅照常说话）
    content = isBoard ? <BoardMissingState /> : children;
  } else {
    // 从未加载成功 + 读失败（网络 / 5xx）：诚实说明原因与恢复路径——「拉不到」不许渲染成「为空」
    content = (
      <div className="shell-center">
        <EmptyState
          icon={<WarningIcon />}
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

  // 看板页的「没写出数据」空态自带「启动后台服务」——健康横幅同一句话不说两遍（原生 .missing 归 PipelineEmptyStateView）
  const pipelineBannerMuted = isBoard && !board && boardMissing;

  return (
    <div className="shell">
      {/* §54.4 左侧导航栏：原生 sidebar 的八页，通高、可折叠 */}
      <NavRail />
      <div className="shell-body">
        <HeaderBar searchSlot={searchSlot} />
        <ErrorBanner />
        {/* §47.4 管线健康（后台服务卡住/崩/停）——与离线横幅互斥，见组件头注 */}
        {!pipelineBannerMuted && <PipelineBanner />}
        {/* §65.4 自动草稿 PR 通道被敏感路径护栏挂起——点名 PR + 「恢复通道」 */}
        <SelfImproveBanner />
        {/* §70 每日整理：正在整理 / 今日整理摘要（不弹系统通知，D10） */}
        <MaintenanceBanner />
        {/* §48 诊断条：录制 / Gmail / Slack 这条路断了 + 一颗修复按钮（原生 kanban header 的 DiagnosticsStrip） */}
        <DiagnosticsStrip />
        <main className="shell-main">{content}</main>
      </div>
    </div>
  );
}
