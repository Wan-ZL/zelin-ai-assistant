// 看板列外壳：列头（色点 + 标题 + 「?」说明 + 计数徽章）+ 可选的列顶输入框槽位 + 卡片列表。
// 列说明 = 原生 SectionHeader 的 ? 图标（Cards.swift）：常显、点击开气泡、hover 走 title——
// 文案来自 server-owned 目录 GET /api/lanes（store.lanes，按 UI 语言取 zh/en；防腐 #10），
// 目录未到时不渲染「?」（client 端不内联第二份文案）。
// 滚动（D42，§54.4 2026-09-06 追记；原生 Kanban.swift column：VStack { header; ScrollView(.vertical) { cards } }）：
// 只有 `.column-list` 是滚动容器（board.css overflow-y: auto），列头 / 输入框槽位 / 「仅显示最近 N 条」是它的
// 兄弟节点、钉在列里不随卡滚走；卡多了在列内滚，列与列互不牵动。键盘焦点落到列外的卡上时由浏览器把这一列滚过去。
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";

interface LaneProps {
  title: string;
  /** dashboard 分区名 = /api/lanes 目录里的 slug（needs_approval / running / review / completed / debt / archived） */
  slug: string;
  /** 徽章文本：counts 真实总数；过滤生效时「命中/总数」（BacklogStrip 同款格式） */
  countLabel: string;
  /** 列语义色 token 名（CONVENTIONS §5 的列→token 映射），如 "--status-progress" */
  colorToken: string;
  /** 列顶输入框（提案列 propose / 运行中列 direct-run） */
  composer?: ReactNode;
  /** 列表被 cap 截断时的补充说明（如 completed「仅显示最近 N 条」） */
  capNote?: string;
  /** 空列文案（原生每列一句；过滤生效时 BoardLanes 换成「无匹配卡片」）；缺省「空」 */
  emptyText?: string;
  isEmpty: boolean;
  children: ReactNode;
}

/** 从 store 的列目录取当前语言的说明；目录未加载 / 无此 slug → null */
export function useLaneHelp(slug: string): string | null {
  const { language } = useI18n();
  const { lanes } = useAppState();
  const entry = lanes?.lanes.find((lane) => lane.slug === slug);
  const help = entry?.help[language];
  return typeof help === "string" && help ? help : null;
}

/** 原生 SectionHeader 的 ? ：常显图标；点击 = 即时气泡（主路径），hover title = 次路径；Esc / 点外面 关 */
export function LaneHelpButton({ help }: { help: string }) {
  const { text } = useI18n();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  return (
    <span className="lane-help" ref={rootRef}>
      <button
        type="button"
        className="lane-help-button"
        title={help}
        aria-label={text("这一列是什么", "About this lane")}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        ?
      </button>
      {open && <span className="lane-help-popover" role="tooltip">{help}</span>}
    </span>
  );
}

export function Lane({ title, slug, countLabel, colorToken, composer, capNote, emptyText, isEmpty, children }: LaneProps) {
  const { text } = useI18n();
  const help = useLaneHelp(slug);
  return (
    <section className="board-column">
      <header className="column-header">
        <span className="lane-dot" style={{ background: `var(${colorToken})` }} />
        <span>{title}</span>
        {help && <LaneHelpButton help={help} />}
        <span className="lane-count">{countLabel}</span>
      </header>
      {composer}
      {/* data-scroll-memory：换页再回看板时这一列滚到哪还原到哪（route.rememberScroll / restoreScroll，D40；同文档内的记忆，不持久化） */}
      <div className="column-list" data-scroll-memory={`lane:${slug}`}>
        {isEmpty ? <p className="column-empty">{emptyText ?? text("空", "Empty")}</p> : children}
      </div>
      {capNote && <p className="column-cap-note">{capNote}</p>}
    </section>
  );
}
