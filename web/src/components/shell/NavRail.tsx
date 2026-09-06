// 左侧导航栏（CONTRACT §54.4 / §66.2 rail:*）：镜像原生 MainWindow.swift 的 sidebar，同序同名同图标——
// 原生八页里 owner 2026-09-04 拿掉两页：问问助手整页退役（D29）、依赖检查并入设置页一区（D30），剩六项
// （任务台 / 录制与数据接入 / 回收站 / 永久性完成 / 设置 / 关于；清单 rail_owner 表标 retired，探针只数剩下的），
// 顶部 app 名 + 折叠钮（sidebar.leading），选中页 accent 18% 底，hover 6% 底，收起 = 48px 图标条
// （tooltip 双语标题），展开 = 200px 默认、160–320 可拖（原生 dragHandle）。三把偏好键逐字镜像原生
// UserDefaults：`sidebarCollapsed` / `sidebarWidth` / `mainSection`（localStorage 同名；页面本身仍由 URL ?page=
// 承担，`mainSection` 只记「上次在哪一页」——冷启动（本窗口会话第一次加载、URL 没指定页）回到那一页，
// 原生 MainNav.init 的行为）。⌘1…⌘7 = 原生 keyboardShortcut 按栏上的七项连续重编（浏览器保留 ⌘1-8 时由浏览器
// 胜出，壳里可用）。每个原生条目的 `data-rail-item="<slug>"` 是 parity 探针的锚（字面量、按原生顺序写死，不许改成循环渲染）。
// 原生页之外的 web 自有页（会议纪要 §63）owner 2026-09-04 要它紧跟任务台（D32）：列第二、拿 ⌘2，不带 data-rail-item——
// 探针 rail:order 只读带锚的六项，相对顺序不变即绿；原先分隔线下再无条目，分隔线随之退役。
// ⌘L = 原生 View ▸ 聚焦捕获框（AppDelegate.swift focusCaptureField）：光标进提案列 composer（board/focusComposer，
// 与壳的 quick_capture 命令同一落点；浏览器里 ⌘L 归地址栏拦不到，壳内可用）。原生 ⌥⌘S 折叠 / 展开侧栏随 s4 DELETE
// 退役、不移植（owner 决策）——折叠只走顶部的折叠钮。折叠 / 展开 150ms ease-in-out（shell.css，原生 MainWindow.swift
// `.animation(.easeInOut(duration: 0.15))`），拖宽期间挂 `is-dragging` 关掉过渡，宽度才跟得上指针。
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { useI18n } from "../../i18n";
import { buildAppUrl, hasExplicitRoute, isDepsPage, navigate, readPage, type AppPage } from "../../route";
import { focusComposer } from "../board/focusComposer";
import {
  ArchiveBoxIcon, GearIcon, InfoCircleIcon, RecapIcon,
  RecordCircleIcon, SidebarLeadingIcon, TrashIcon, TrayFullIcon,
} from "./railIcons";

const COLLAPSED_KEY = "sidebarCollapsed";
const WIDTH_KEY = "sidebarWidth";
const SECTION_KEY = "mainSection";
/** sessionStorage：本窗口会话已经冷启动过（同一窗口里「← 返回看板」这类整页导航不再回上次的页） */
const LAUNCHED_KEY = "zai.launched";
const WIDTH_DEFAULT = 200;
const WIDTH_MIN = 160;
const WIDTH_MAX = 320;

/** 原生 MainSection.rawValue → web ?page=（dashboard 是看板本体）。`ask` / `deps` 不在表里：D29 退役、D30 并入设置——
 *  localStorage 里残留的旧 `mainSection` 值查不到就不导航（restoreMainSection 的坏值路径）。 */
export const RAIL_PAGE: Record<string, AppPage> = {
  dashboard: "board", ingest: "ingest",
  trash: "trash", archive: "archive", settings: "settings", about: "about",
};

/** 当前 ?page= 属于哪个 rail slug（deps / diagnostics 旧深链归设置；permissions / setup / styleguide 不点亮任何项） */
export function activeRailSlug(page: AppPage): string | null {
  if (isDepsPage(page)) return "settings";
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

/** 原生 MainNav.section didSet：每到一个 rail 页就记住它的 slug（非 rail 页——permissions / setup——不记） */
export function rememberMainSection(page: AppPage): void {
  const slug = activeRailSlug(page);
  if (slug) persist(SECTION_KEY, slug);
}

/** 原生 MainNav.init（`mainSection` 兜底 dashboard）：只在冷启动且 URL 没指定去处时给出要回去的页；
 *  看板（dashboard）本来就是缺省，返回 null 不导航。sessionStorage 不可用时视为已启动过（宁不跳）。 */
export function restoreMainSection(search: string): AppPage | null {
  try {
    if (window.sessionStorage.getItem(LAUNCHED_KEY)) return null;
    window.sessionStorage.setItem(LAUNCHED_KEY, "1");
  } catch {
    return null;
  }
  if (hasExplicitRoute(search)) return null;
  let slug: string | null = null;
  try {
    slug = window.localStorage.getItem(SECTION_KEY);
  } catch {
    return null;
  }
  const page = slug ? RAIL_PAGE[slug] : undefined;
  return page && page !== "board" ? page : null;
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

/** ⌘1…⌘7 → 七页（原生 MainSection 顺序去掉 ask / deps，会议纪要插在任务台之后——D32）；输入框里不劫持 */
function shortcutPage(event: KeyboardEvent): AppPage | null {
  if (!event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return null;
  const target = event.target as HTMLElement | null;
  if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return null;
  const order: AppPage[] = ["board", "recaps", "ingest", "trash", "archive", "settings", "about"];
  const index = Number(event.key) - 1;
  return index >= 0 && index < order.length && String(index + 1) === event.key ? order[index] : null;
}

/** ⌘L → 聚焦提案列 composer（原生菜单快捷键：只认 ⌘ 单修饰 + L；输入框里也接——从运行中 composer / 搜索框按 ⌘L
 *  照样跳到提案框，已在提案框里 = 光标到末尾）。⌥⌘S 不在这里：原生的侧栏折叠快捷键已退役（见文件头）。 */
export function isFocusComposerShortcut(event: KeyboardEvent): boolean {
  return event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "l";
}

export function NavRail() {
  const { text } = useI18n();
  const [isCollapsed, setCollapsed] = useState<boolean>(readCollapsed);
  const [width, setWidth] = useState<number>(readSidebarWidth);
  const [isDragging, setDragging] = useState(false);
  const dragStart = useRef<{ x: number; width: number } | null>(null);
  const page = readPage(window.location.search);
  const active = activeRailSlug(page);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isFocusComposerShortcut(event)) {
        event.preventDefault();
        focusComposer();
        return;
      }
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

  // 原生 dragHandle：拖动中只改本地宽度，松手才持久化（UserDefaults sidebarWidth 同义）；
  // 拖动期间 `is-dragging` 关掉折叠 / 展开的宽度过渡（否则每一步都 150ms 滞后于指针）
  const onHandleDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (isCollapsed) return;
    dragStart.current = { x: event.clientX, width };
    setDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onHandleMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragStart.current) return;
    setWidth(clampSidebarWidth(dragStart.current.width + event.clientX - dragStart.current.x));
  };
  const onHandleUp = () => {
    if (!dragStart.current) return;
    dragStart.current = null;
    setDragging(false);
    persist(WIDTH_KEY, String(width));
  };

  const style = isCollapsed ? undefined : { width: `${width}px` };
  const link = (slug: string) => ({ isActive: active === slug, isCollapsed });
  const railClass = `rail${isCollapsed ? " is-collapsed" : ""}${isDragging ? " is-dragging" : ""}`;

  return (
    <nav className={railClass} data-rail="left" style={style} aria-label={text("导航", "Navigation")}>
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
      {/* 原生第 2 / 3 项（问问助手 / 依赖检查）自 2026-09-04 起不在栏上：D29 退役、D30 并入设置页「依赖检查」区 */}
      <RailLink data-rail-item="dashboard" page="board" zh="任务台" en="Workbench" shortcut="⌘1" icon={<TrayFullIcon />} {...link("dashboard")} />
      {/* web 自有页（§63 会议纪要）：原生没有此页，不带 data-rail-item（探针只数原生六项）；owner 要它紧跟任务台（D32），⌘2 */}
      <a
        className={`rail-item${page === "recaps" ? " is-active" : ""}`}
        href={buildAppUrl(window.location.href, "recaps", null).toString()}
        aria-current={page === "recaps" ? "page" : undefined}
        title={`${text("会议纪要", "Recaps")} (⌘2)`}
        data-rail-extra="recaps"
      >
        <span className="rail-icon"><RecapIcon /></span>
        {!isCollapsed && <span className="rail-label">{text("会议纪要", "Recaps")}</span>}
      </a>
      <RailLink data-rail-item="ingest" page="ingest" zh="录制与数据接入" en="Recording & Data Sources" shortcut="⌘3" icon={<RecordCircleIcon />} {...link("ingest")} />
      <RailLink data-rail-item="trash" page="trash" zh="回收站" en="Trash" shortcut="⌘4" icon={<TrashIcon />} {...link("trash")} />
      <RailLink data-rail-item="archive" page="archive" zh="永久性完成" en="Done for good" shortcut="⌘5" icon={<ArchiveBoxIcon />} {...link("archive")} />
      <RailLink data-rail-item="settings" page="settings" zh="设置" en="Settings" shortcut="⌘6" icon={<GearIcon />} {...link("settings")} />
      <RailLink data-rail-item="about" page="about" zh="关于" en="About" shortcut="⌘7" icon={<InfoCircleIcon />} {...link("about")} />
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
