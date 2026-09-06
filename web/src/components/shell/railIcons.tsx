// 左侧导航栏的图标（CONTRACT §54.4）：原生 MainSection.icon 的 SF Symbol 名 → 同名内联 SVG
// （tray.full / record.circle / trash / archivebox / gearshape / info.circle + 折叠钮 sidebar.leading；
// questionmark.bubble / checklist 随 D29 / D30 的两页一起从栏上撤下）。16×16 线稿、currentColor、
// aria-hidden——文字在 NavRail 里。
import type { ReactNode } from "react";

interface IconProps {
  children: ReactNode;
}

function Icon({ children }: IconProps) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {children}
    </svg>
  );
}

/** tray.full — 任务台 */
export function TrayFullIcon() {
  return (
    <Icon>
      <path d="M3 13.5V17a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-3.5" />
      <path d="M3 13.5h5l1.5 2.5h5l1.5-2.5h5" />
      <path d="M7 9h10M8.5 5.5h7" />
    </Icon>
  );
}

/** record.circle — 录制与数据接入 */
export function RecordCircleIcon() {
  return (
    <Icon>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="3.5" fill="currentColor" stroke="none" />
    </Icon>
  );
}

/** trash — 回收站 */
export function TrashIcon() {
  return (
    <Icon>
      <path d="M4 7h16M9.5 7V4.5h5V7M6 7l1 13h10l1-13" />
      <path d="M10 11v6M14 11v6" />
    </Icon>
  );
}

/** archivebox — 永久性完成 */
export function ArchiveBoxIcon() {
  return (
    <Icon>
      <rect x="3.5" y="4" width="17" height="4" rx="1" />
      <path d="M5 8v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8" />
      <path d="M10 12h4" />
    </Icon>
  );
}

/** gearshape — 设置 */
export function GearIcon() {
  return (
    <Icon>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </Icon>
  );
}

/** info.circle — 关于 */
export function InfoCircleIcon() {
  return (
    <Icon>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5.5" />
      <path d="M12 7.6h.01" strokeWidth="2.4" />
    </Icon>
  );
}

/** sidebar.leading — 折叠/展开侧栏 */
export function SidebarLeadingIcon() {
  return (
    <Icon>
      <rect x="3" y="4.5" width="18" height="15" rx="2.5" />
      <path d="M9 4.5v15" />
    </Icon>
  );
}

/** text.bubble — 会议纪要（web 自有页，不在原生八页里） */
export function RecapIcon() {
  return (
    <Icon>
      <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H11l-4.5 4v-4H6.5A2.5 2.5 0 0 1 4 13.5v-8Z" />
      <path d="M8 8h8M8 11.5h5" />
    </Icon>
  );
}
