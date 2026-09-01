// 顶栏（G7 shell，自写非 fork）：左=标识+标题+设备标签+新鲜度+部署状态（§56）；中=搜索槽位；
// 右=连接状态点+语言切换+主题切换。搜索本体归 A8（filters/⌘F）——这里只留
// searchSlot 槽位，A8 把搜索组件经 app.tsx 传进来即可，不传则槽位为空但布局稳定。
import type { ReactNode } from "react";
import { useI18n } from "../../i18n";
import { buildAppUrl } from "../../route";
import { useAppState, type ConnectionState } from "../../store";
import { DeployLabel } from "./DeployLabel";
import { FreshnessLabel } from "./FreshnessLabel";
import { LanguageToggle } from "./LanguageToggle";
import { ThemeToggle } from "./ThemeToggle";

export interface HeaderBarProps {
  /** A8 的搜索/过滤组件挂载点（经 AppShell → app.tsx 注入） */
  searchSlot?: ReactNode;
}

function connectionLabel(state: ConnectionState, text: (zh: string, en: string) => string): string {
  switch (state) {
    case "live":
      return text("已连接", "Live");
    case "reconnecting":
      return text("重连中", "Reconnecting");
    default:
      return text("连接中", "Connecting");
  }
}

export function HeaderBar({ searchSlot }: HeaderBarProps) {
  const { text } = useI18n();
  const { board, connection } = useAppState();
  const deviceLabel = typeof board?.device_label === "string" ? board.device_label : "";

  return (
    <header className="shell-header">
      <div className="shell-header-left">
        <span className="shell-logo" aria-hidden="true">
          {/* 与 favicon.svg 同构：teal 圆角方 + 三根看板柱 */}
          <svg width="20" height="20" viewBox="0 0 64 64">
            <rect width="64" height="64" rx="14" fill="var(--accent)" />
            <rect x="12" y="14" width="11" height="24" rx="3" fill="var(--on-accent)" opacity="0.95" />
            <rect x="26.5" y="14" width="11" height="36" rx="3" fill="var(--on-accent)" opacity="0.78" />
            <rect x="41" y="14" width="11" height="16" rx="3" fill="var(--on-accent)" opacity="0.6" />
          </svg>
        </span>
        <h1 className="shell-title">{text("Zelin 的 AI 助理", "Zelin's AI Assistant")}</h1>
        {deviceLabel && <span className="shell-device">{deviceLabel}</span>}
        <FreshnessLabel />
        {/* §56：v0.48.x · deployed 12m ago（无 deploy_state 时自隐藏） */}
        <DeployLabel />
      </div>
      <div className="shell-search-slot">{searchSlot ?? null}</div>
      <div className="shell-header-right">
        {/* 回收站入口：TrashPage 只有 ?page=trash 深链——壳层导航走整页 <a>（同 trash-back-link 约定） */}
        <a
          className="shell-trash-link"
          href={buildAppUrl(window.location.href, "trash", null).toString()}
        >
          {text("回收站", "Trash")}
        </a>
        {/* 设置入口（§57）：齿轮 → ?page=settings 整页导航（同 trash 链接约定） */}
        <a
          className="shell-settings-link"
          href={buildAppUrl(window.location.href, "settings", null).toString()}
          aria-label={text("设置", "Settings")}
          title={text("设置", "Settings")}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true" fill="none"
            stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
          </svg>
        </a>
        <span
          className={`shell-connection is-${connection}`}
          title={connectionLabel(connection, text)}
          role="status"
        >
          <span className="shell-connection-dot" aria-hidden="true" />
          <span className="shell-connection-text">{connectionLabel(connection, text)}</span>
        </span>
        <LanguageToggle />
        <ThemeToggle />
      </div>
    </header>
  );
}
