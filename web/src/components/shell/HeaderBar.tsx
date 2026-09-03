// 顶栏（G7 shell，自写非 fork；§54.4 布局跟原生 Kanban.header）：左=标识+标题+设备标签+新鲜度+部署状态（§56）；
// 中=搜索/过滤/排序槽位；右=壳内原生开关（录制 / 实时字幕，仅壳里出现，§61）+连接状态点+语言切换+主题切换。
// 页面入口（任务台 / 回收站 / 永久性完成 / 设置 / 关于 / 会议纪要…）不在这里——它们在左侧导航栏（NavRail），
// 与原生 MainWindow 的 sidebar 同位。搜索本体归 A8（filters/⌘F）——这里只留 searchSlot 槽位。
import type { ReactNode } from "react";
import { useI18n } from "../../i18n";
import { useAppState, type ConnectionState } from "../../store";
import { DeployLabel } from "./DeployLabel";
import { FreshnessLabel } from "./FreshnessLabel";
import { LanguageToggle } from "./LanguageToggle";
import { ShellControls } from "./ShellControls";
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
        {/* §61：录制 / 实时字幕 开关——只在 shell/ 壳（"Zelin's AI Assistant"）里渲染（普通浏览器无桥 → null） */}
        <ShellControls />
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
