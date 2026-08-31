// 看板列外壳：列头（色点 + 标题 + 计数徽章）+ LaneHelp 一行定义文案（§41 网页 lane help，
// zh 逐字镜像 shared/Sources/Lanes.swift）+ 可选的列顶输入框槽位 + 卡片列表。
import type { ReactNode } from "react";
import { useI18n } from "../../i18n";

interface LaneProps {
  title: string;
  help: string;
  /** 徽章文本：counts 真实总数；过滤生效时「命中/总数」（BacklogStrip 同款格式） */
  countLabel: string;
  /** 列语义色 token 名（CONVENTIONS §5 的列→token 映射），如 "--status-progress" */
  colorToken: string;
  /** 列顶输入框（提案列 propose / 运行中列 direct-run） */
  composer?: ReactNode;
  /** 列表被 cap 截断时的补充说明（如 completed「仅显示最近 N 条」） */
  capNote?: string;
  isEmpty: boolean;
  children: ReactNode;
}

export function Lane({ title, help, countLabel, colorToken, composer, capNote, isEmpty, children }: LaneProps) {
  const { text } = useI18n();
  return (
    <section className="board-column">
      <header className="column-header">
        <span className="lane-dot" style={{ background: `var(${colorToken})` }} />
        <span>{title}</span>
        <span className="lane-count">{countLabel}</span>
      </header>
      <p className="column-help">{help}</p>
      {composer}
      <div className="column-list">
        {isEmpty ? <p className="column-empty">{text("空", "Empty")}</p> : children}
      </div>
      {capNote && <p className="column-cap-note">{capNote}</p>}
    </section>
  );
}
