// 左侧导航栏（CONTRACT §54.4 / §66.2 rail:*）：逐字镜像原生 MainWindow.swift 的 sidebar——
// 八页同序同名同图标（任务台 / 问问助手 / 依赖检查 / 录制与数据接入 / 回收站 / 永久性完成 / 设置 / 关于），
// 顶部 app 名 + 折叠钮（sidebar.leading），选中页 accent 18% 底，hover 6% 底，收起 = 48px 图标条
// （tooltip 双语标题），展开 = 200px 默认、160–320 可拖（原生 dragHandle）。三把偏好键逐字镜像原生
// UserDefaults：`sidebarCollapsed` / `sidebarWidth`（localStorage 同名；`mainSection` 由 URL ?page= 承担，
// 不另存）。⌘1…⌘8 = 原生 keyboardShortcut（浏览器保留 ⌘1-8 时由浏览器胜出，壳里可用）。
// 每个条目的 `data-rail-item="<slug>"` 是 parity 探针的锚（字面量、按原生顺序写死，不许改成循环渲染）。
// 原生八页之外的 web 自有页（会议纪要 §63）放分隔线下方，不带 data-rail-item。
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { useI18n } from "../../i18n";
import { buildAppUrl, navigate, readPage, type AppPage } from "../../route";
import {
  ArchiveBoxIcon, ChecklistIcon, GearIcon, InfoCircleIcon, QuestionBubbleIcon, RecapIcon,
  RecordCircleIcon, SidebarLeadingIcon, TrashIcon, TrayFullIcon,
} from "./railIcons";

const COLLAPSED_KEY = "sidebarCollapsed";
const WIDTH_KEY = "sidebarWidth";
const WIDTH_DEFAULT = 200;
const WIDTH_MIN = 160;
const WIDTH_MAX = 320;

/** 原生 MainSection.rawValue → web ?page=（dashboard 是看板本体；deps 与 diagnostics 同一页） */
export const RAIL_PAGE: Record<string, AppPage> = {
  dashboard: "board", ask: "ask", deps: "deps", ingest: "ingest",
  trash: "trash", archive: "archive", settings: "settings", about: "about",
};

/** 当前 ?page= 属于哪个 rail slug（diagnostics 归 deps；permissions / setup / styleguide 不点亮任何项） */
export function activeRailSlug(page: AppPage): string | null {
  if (page === "diagnostics") return "deps";
  for (const [slug, target] of Object.entries(RAIL_PAGE)) if (target === page) return slug;
  return null;
}

export function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === "true";
  } catch {
    return false;
  }
}

export function clampSidebarWidth(width: number): number {
  return Math.min(WIDTH_MAX, Math.max(WIDTH_MIN, width));
}

export function readSidebarWidth(): number {
  try {
    const raw = Number(window.localStorage.getItem(WIDTH_KEY));
    return raw > 0 ? clampSidebarWidth(raw) : WIDTH_DEFAULT;
  } catch {
    return WIDTH_DEFAULT;
  }
}

function persist(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* localStorage 不可写：本次会话仍生效，仅不持久化 */
  }
}

interface RailLinkProps {
  "data-rail-item": string;
  page: AppPage;
  zh: string;
  en: string;
  shortcut: string;
  icon: ReactNode;
  isActive: boolean;
  isCollapsed: boolean;
}

function RailLink({ page, zh, en, shortcut, icon, isActive, isCollapsed, ...rest }: RailLinkProps) {
  const { text } = useI18n();
  const title = text(zh, en);
  return (
    <a
      className={`rail-item${isActive ? " is-active" : ""}`}
      href={buildAppUrl(window.location.href, page, null).toString()}
      aria-current={isActive ? "page" : undefined}
      title={`${title} (${shortcut})`}
      data-rail-item={rest["data-rail-item"]}
    >
      <span className="rail-icon">{icon}</span>
      {!isCollapsed && <span className="rail-label">{title}</span>}
    </a>
  );
}

/** ⌘1…⌘8 → 八页（原生 MainSection 顺序）；输入框里不劫持 */
function shortcutPage(event: KeyboardEvent): AppPage | null {
  if (!event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return null;
  const target = event.target as HTMLElement | null;
  if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return null;
  const order: AppPage[] = ["board", "ask", "deps", "ingest", "trash", "archive", "settings", "about"];
  const index = Number(event.key) - 1;
  return index >= 0 && index < order.length && String(index + 1) === event.key ? order[index] : null;
}

export function NavRail() {
  const { text } = useI18n();
  const [isCollapsed, setCollapsed] = useState<boolean>(readCollapsed);
  const [width, setWidth] = useState<number>(readSidebarWidth);
  const dragStart = useRef<{ x: number; width: number } | null>(null);
  const page = readPage(window.location.search);
  const active = activeRailSlug(page);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = shortcutPage(event);
      if (!target) return;
      event.preventDefault();
      navigate(buildAppUrl(window.location.href, target, null));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const toggle = () => {
    const next = !isCollapsed;
    setCollapsed(next);
    persist(COLLAPSED_KEY, String(next));
  };

  // 原生 dragHandle：拖动中只改本地宽度，松手才持久化（UserDefaults sidebarWidth 同义）
  const onHandleDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (isCollapsed) return;
    dragStart.current = { x: event.clientX, width };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onHandleMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragStart.current) return;
    setWidth(clampSidebarWidth(dragStart.current.width + event.clientX - dragStart.current.x));
  };
  const onHandleUp = () => {
    if (!dragStart.current) return;
    dragStart.current = null;
    persist(WIDTH_KEY, String(width));
  };

  const style = isCollapsed ? undefined : { width: `${width}px` };
  const link = (slug: string) => ({ isActive: active === slug, isCollapsed });

  return (
    <nav className={`rail${isCollapsed ? " is-collapsed" : ""}`} data-rail="left" style={style} aria-label={text("导航", "Navigation")}>
      <div className="rail-head">
        {!isCollapsed && <span className="rail-title">Zelin's AI Assistant</span>}
        <button
          type="button"
          className="rail-toggle"
          onClick={toggle}
          aria-expanded={!isCollapsed}
          title={text("折叠/展开侧栏", "Collapse/expand sidebar")}
          aria-label={text("折叠/展开侧栏", "Collapse/expand sidebar")}
        >
          <SidebarLeadingIcon />
        </button>
      </div>
      <RailLink data-rail-item="dashboard" page="board" zh="任务台" en="Workbench" shortcut="⌘1" icon={<TrayFullIcon />} {...link("dashboard")} />
      <RailLink data-rail-item="ask" page="ask" zh="问问助手" en="Ask" shortcut="⌘2" icon={<QuestionBubbleIcon />} {...link("ask")} />
      <RailLink data-rail-item="deps" page="deps" zh="依赖检查" en="Dependencies" shortcut="⌘3" icon={<ChecklistIcon />} {...link("deps")} />
      <RailLink data-rail-item="ingest" page="ingest" zh="录制与数据接入" en="Recording & Data Sources" shortcut="⌘4" icon={<RecordCircleIcon />} {...link("ingest")} />
      <RailLink data-rail-item="trash" page="trash" zh="回收站" en="Trash" shortcut="⌘5" icon={<TrashIcon />} {...link("trash")} />
      <RailLink data-rail-item="archive" page="archive" zh="永久性完成" en="Done for good" shortcut="⌘6" icon={<ArchiveBoxIcon />} {...link("archive")} />
      <RailLink data-rail-item="settings" page="settings" zh="设置" en="Settings" shortcut="⌘7" icon={<GearIcon />} {...link("settings")} />
      <RailLink data-rail-item="about" page="about" zh="关于" en="About" shortcut="⌘8" icon={<InfoCircleIcon />} {...link("about")} />
      <div className="rail-divider" role="separator" />
      {/* web 自有页（§63 会议纪要）：原生没有此页，不带 data-rail-item，不参与 ⌘ 数字键 */}
      <a
        className={`rail-item${page === "recaps" ? " is-active" : ""}`}
        href={buildAppUrl(window.location.href, "recaps", null).toString()}
        aria-current={page === "recaps" ? "page" : undefined}
        title={text("会议纪要", "Recaps")}
        data-rail-extra="recaps"
      >
        <span className="rail-icon"><RecapIcon /></span>
        {!isCollapsed && <span className="rail-label">{text("会议纪要", "Recaps")}</span>}
      </a>
      {!isCollapsed && (
        <div
          className="rail-resize-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label={text("拖动调整侧栏宽度", "Drag to resize the sidebar")}
          onPointerDown={onHandleDown}
          onPointerMove={onHandleMove}
          onPointerUp={onHandleUp}
          onPointerCancel={onHandleUp}
        />
      )}
    </nav>
  );
}
