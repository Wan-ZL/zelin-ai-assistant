// 设置页一区的开合壳（CONTRACT §68.1 追记，D44；原生 Settings.swift `CollapsibleSection` 的 web 版）。
// 页面级包每一区：区头 = <h3> 里一颗 <button aria-expanded aria-controls>（chevron + 目录标题，键盘可达），正文 = 常挂载、
// 折叠时 `hidden` 的容器——区块**不卸载**：目录区的草稿（§68.1 草稿守则）与搜索干草（正文也算）都得留在 DOM 里。
// 锚点 `#settings-<id>` 落在本壳（永远可见，折着也滚得到）；区内自己的 <h3> 留作 aria-labelledby 的名源，视觉由 settings.css
// 藏掉、由区头代替。搜索期间（`forced`）每个命中的区强制展开、toggle 禁用——记忆不动（原生 §1.9 `.disabled(searchActive)`）。
import type { ReactNode } from "react";
import { useI18n } from "../../i18n";

export interface SettingsFoldProps {
  /** section id（= SETTINGS_TOC 条目 id = 锚点 `settings-<id>` 的后缀） */
  id: string;
  title: string;
  /** 记忆里是展开的（store.expandedSettingsSections） */
  isExpanded: boolean;
  /** 搜索进行中：强制展开、toggle 禁用 */
  isForced: boolean;
  onToggle: (id: string) => void;
  children: ReactNode;
}

export function SettingsFold({ id, title, isExpanded, isForced, onToggle, children }: SettingsFoldProps) {
  const { text } = useI18n();
  const open = isForced || isExpanded;
  return (
    <div className={`settings-fold ${open ? "is-expanded" : "is-collapsed"}`} id={`settings-${id}`} data-section={id}>
      <h3 className="settings-fold-title">
        <button
          type="button"
          className="settings-fold-toggle"
          aria-expanded={open}
          aria-controls={`settings-${id}-body`}
          disabled={isForced}
          title={isForced ? text("搜索期间命中的区全部展开", "Every match stays expanded while searching") : undefined}
          onClick={() => onToggle(id)}
        >
          <span className="settings-fold-chevron" aria-hidden="true">▸</span>
          <span className="settings-fold-label">{title}</span>
        </button>
      </h3>
      <div className="settings-fold-body" id={`settings-${id}-body`} hidden={!open}>
        {children}
      </div>
    </div>
  );
}
