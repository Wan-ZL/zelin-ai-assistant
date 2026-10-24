// 左侧导航栏（CONTRACT §54.4）：镜像原生 MainWindow.swift 的 sidebar 形制——顶部 app 名 + 折叠钮（sidebar.leading），
// 选中页 accent 18% 底，hover 6% 底，收起 = 48px 图标条（tooltip 双语标题），展开 = 200px 默认、160–320 可拖（原生 dragHandle）。
// v2.0.0 栏上四项（owner 2026-10-23，lite 2.0）：会议纪要 / 录制与导入 / 设置 / 关于；任务台、回收站、永久性完成、技能随
// Act 管线退役（全文见 tag full）。三把偏好键逐字镜像原生 UserDefaults：`sidebarCollapsed` / `sidebarWidth` / `mainSection`
// （localStorage 同名；页面本身仍由 URL ?page= 承担，`mainSection` 只记「上次在哪一页」——冷启动（本窗口会话第一次加载、
// URL 没指定页）回到那一页，原生 MainNav.init 的行为；残留的退役 slug 查不到就不导航）。⌘1…⌘4 = 栏上四项（浏览器保留
// ⌘1-4 时由浏览器胜出，壳里可用）。每项的 `data-rail-item="<slug>"` 是探针锚（字面量、按栏序写死，不许改成循环渲染）。
// 换页不重载（D40，§54.4 2026-09-06 追记）：rail 项仍是 `<a href>`（⌘点 / 中键开新标签、复制链接照旧），左键点下去由
// route.startRouter 的文档级链接委托拦成 pushState；⌘1…⌘4 直接 route.navigate；选中态从 useRoute() 订阅的 URL 里读。
// 折叠 / 展开 150ms ease-in-out（shell.css，原生 `.animation(.easeInOut(duration: 0.15))`），拖宽期间挂 `is-dragging`
// 关掉过渡，宽度才跟得上指针。
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { useI18n } from "../../i18n";
import { buildAppUrl, DEFAULT_PAGE, hasExplicitRoute, isDepsPage, navigate, readPage, useRoute, type AppPage } from "../../route";
import { GearIcon, InfoCircleIcon, RecapIcon, RecordCircleIcon, SidebarLeadingIcon } from "./railIcons";

const COLLAPSED_KEY = "sidebarCollapsed";
const WIDTH_KEY = "sidebarWidth";
const SECTION_KEY = "mainSection";
/** sessionStorage：本窗口会话已经冷启动过（同一窗口里的整页导航不再回上次的页） */
const LAUNCHED_KEY = "zai.launched";
const WIDTH_DEFAULT = 200;
const WIDTH_MIN = 160;
const WIDTH_MAX = 320;

/** rail slug → web ?page=（栏序）。退役的 slug（dashboard / trash / archive / skills，以及更早的 ask / deps）不在表里——
 *  localStorage 里残留的旧 `mainSection` 值查不到就不导航（restoreMainSection 的坏值路径）。 */
export const RAIL_PAGE: Record<string, AppPage> = {
  recaps: "recaps", ingest: "ingest", settings: "settings", about: "about",
};

/** 当前 ?page= 属于哪个 rail slug（deps / diagnostics 旧深链归设置；permissions / setup 不点亮任何项） */
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

/** 原生 MainNav.init：只在冷启动且 URL 没指定去处时给出要回去的页；缺省页（会议纪要）本来就是落点，返回 null 不导航。
 *  sessionStorage 不可用时视为已启动过（宁不跳）。 */
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
  return page && page !== DEFAULT_PAGE ? page : null;
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
      href={buildAppUrl(window.location.href, page).toString()}
      aria-current={isActive ? "page" : undefined}
      title={`${title} (${shortcut})`}
      data-rail-item={rest["data-rail-item"]}
    >
      <span className="rail-icon">{icon}</span>
      {!isCollapsed && <span className="rail-label">{title}</span>}
    </a>
  );
}

/** 栏序（⌘1…⌘4 的落点；与下面四个 RailLink 同序） */
export const RAIL_ORDER: readonly AppPage[] = ["recaps", "ingest", "settings", "about"];

/** ⌘1…⌘4 → 栏上四项；输入框里不劫持 */
export function shortcutPage(event: KeyboardEvent): AppPage | null {
  if (!event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return null;
  const target = event.target as HTMLElement | null;
  if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return null;
  const index = Number(event.key) - 1;
  return index >= 0 && index < RAIL_ORDER.length && String(index + 1) === event.key ? RAIL_ORDER[index] : null;
}

export function NavRail() {
  const { text } = useI18n();
  const [isCollapsed, setCollapsed] = useState<boolean>(readCollapsed);
  const [width, setWidth] = useState<number>(readSidebarWidth);
  const [isDragging, setDragging] = useState(false);
  const dragStart = useRef<{ x: number; width: number } | null>(null);
  const page = readPage(useRoute()); // D40：换页不重载，选中态跟着 pushState 后的 URL 走
  const active = activeRailSlug(page);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = shortcutPage(event);
      if (!target) return;
      event.preventDefault();
      navigate(buildAppUrl(window.location.href, target));
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
      <RailLink data-rail-item="recaps" page="recaps" zh="会议纪要" en="Recaps" shortcut="⌘1" icon={<RecapIcon />} {...link("recaps")} />
      <RailLink data-rail-item="ingest" page="ingest" zh="录制与导入" en="Recording & import" shortcut="⌘2" icon={<RecordCircleIcon />} {...link("ingest")} />
      <RailLink data-rail-item="settings" page="settings" zh="设置" en="Settings" shortcut="⌘3" icon={<GearIcon />} {...link("settings")} />
      <RailLink data-rail-item="about" page="about" zh="关于" en="About" shortcut="⌘4" icon={<InfoCircleIcon />} {...link("about")} />
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
