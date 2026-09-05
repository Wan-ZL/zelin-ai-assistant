// 通用空态原语（G7 shell，自写非 fork）：图标 + 标题 + 提示 + 可选动作。
// 供 shell 整页空态使用；A6 各列空态、A7 抽屉空 tab 也应复用本组件保持视觉一致。
// 纯展示组件：不读 store、不发请求，一切内容由调用方传入。
import type { ReactNode } from "react";

export interface EmptyStateProps {
  /** 可选图标（inline SVG / 字符）；不传则只渲染文字 */
  icon?: ReactNode;
  title: string;
  /** 次级说明（诚实描述现状与恢复路径，别只写"暂无数据"） */
  hint?: string;
  /** 可选动作区（按钮等），由调用方自带语义与回调 */
  action?: ReactNode;
  /** 对齐：整页居中空态用 center（默认）；原生 VStack(alignment: .leading) 式的顶左空态用 start */
  align?: "center" | "start";
}

export function EmptyState({ icon, title, hint, action, align = "center" }: EmptyStateProps) {
  return (
    <div className={`shell-empty${align === "start" ? " is-start" : ""}`} role="status">
      {icon != null && (
        <div className="shell-empty-icon" aria-hidden="true">
          {icon}
        </div>
      )}
      <p className="shell-empty-title">{title}</p>
      {hint != null && <p className="shell-empty-hint">{hint}</p>}
      {action != null && <div className="shell-empty-action">{action}</div>}
    </div>
  );
}
