// 顶栏（G7 shell，自写非 fork；§54.4 布局跟原生 Kanban.header）：左=标识+标题+设备标签+新鲜度+部署状态（§56）；
// 中=搜索/过滤/排序槽位；右=壳内原生开关（录制 / 实时字幕，仅壳里出现，§61）+连接状态点+语言切换+主题切换。
// 页面入口（任务台 / 回收站 / 永久性完成 / 设置 / 关于 / 会议纪要…）不在这里——它们在左侧导航栏（NavRail），
// 与原生 MainWindow 的 sidebar 同位。搜索本体归 A8（filters/⌘F）——这里只留 searchSlot 槽位。
// 永远一行（§49 追记 2026-09-04）：按实测宽度分三档 data-density（headerDensity.ts）——
//   full    今天的布局；
//   compact 左侧收掉设备标签（槽位里的 FilterBar 自己把 chips 收进「筛选」popover）；
//   tight   左侧只剩标识 + 标题（极窄时标题省略号 + title 全名，标识不缩），新鲜度 / 部署小字折进连接点的 tooltip；
//           右侧开关只留图标（CSS）。
// 档位经 HeaderDensityContext 发给槽位与右侧开关；`density` prop 是测试 / 预览用的覆写。
import { useRef, type ReactNode } from "react";
import { useI18n } from "../../i18n";
import { hasShellBridge } from "../../shellBridge";
import { useAppState, type ConnectionState } from "../../store";
import { DeployLabel, useDeployLabel } from "./DeployLabel";
import { FreshnessLabel, freshnessText, useFreshness } from "./FreshnessLabel";
import { HeaderDensityContext, useMeasuredHeaderDensity, type HeaderDensity } from "./headerDensity";
import { LanguageToggle } from "./LanguageToggle";
import { ShellControls } from "./ShellControls";
import { ThemeToggle } from "./ThemeToggle";

export interface HeaderBarProps {
  /** A8 的搜索/过滤组件挂载点（经 AppShell → app.tsx 注入） */
  searchSlot?: ReactNode;
  /** 密度档覆写（测试 / 预览）；不给则按 .shell-header 实测宽度判 */
  density?: HeaderDensity;
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

export function HeaderBar({ searchSlot, density: densityOverride }: HeaderBarProps) {
  const { text, language } = useI18n();
  const { board, connection, displaySettings } = useAppState();
  const headerRef = useRef<HTMLElement>(null);
  const density = useMeasuredHeaderDensity(headerRef, {
    override: densityOverride,
    extras: { shell: hasShellBridge(), english: language === "en" },
    revision: displaySettings?.text_size ?? "",
  });
  const freshness = useFreshness();
  const deploy = useDeployLabel();
  const deviceLabel = typeof board?.device_label === "string" ? board.device_label : "";
  const tight = density === "tight";
  // tight 档左翼只剩标识 + 标题，顶栏极窄（导航栏拖到最宽 + 壳桥）时标题省略号收场（shell.css）——全名挂 title；
  // full / compact 标题不缩，不挂（视觉 golden 的 DOM 一字不变）
  const appName = text("Zelin 的 AI 助理", "Zelin's AI Assistant");

  // tight：新鲜度 / 部署小字不占行，整句挂在连接点的 tooltip 上（连接词 · 数据生成于 … · v… 部署）
  const connectionText = connectionLabel(connection, text);
  const connectionTitle = tight
    ? [connectionText, freshness && freshnessText(freshness), deploy?.label].filter(Boolean).join(" · ")
    : connectionText;

  return (
    <HeaderDensityContext.Provider value={density}>
      <header className="shell-header" data-density={density} ref={headerRef}>
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
          <h1 className="shell-title" title={tight ? appName : undefined}>{appName}</h1>
          {/* title 挂全文：左翼被槽位挤时三段小字会省略号（shell.css） */}
          {deviceLabel && density === "full" && <span className="shell-device" title={deviceLabel}>{deviceLabel}</span>}
          {!tight && <FreshnessLabel value={freshness} />}
          {/* §56：v0.48.x · deployed 12m ago（无 deploy_state 时自隐藏） */}
          {!tight && <DeployLabel value={deploy} />}
        </div>
        <div className="shell-search-slot">{searchSlot ?? null}</div>
        <div className="shell-header-right">
          {/* §61：录制 / 实时字幕 开关——只在 shell/ 壳（"Zelin's AI Assistant"）里渲染（普通浏览器无桥 → null） */}
          <ShellControls />
          <span
            className={`shell-connection is-${connection}${tight && (freshness?.stale || deploy?.warn) ? " is-warn" : ""}`}
            title={connectionTitle}
            role="status"
          >
            <span className="shell-connection-dot" aria-hidden="true" />
            <span className="shell-connection-text">{connectionText}</span>
          </span>
          <LanguageToggle />
          <ThemeToggle />
        </div>
      </header>
    </HeaderDensityContext.Provider>
  );
}
