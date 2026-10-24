// 字号/字重梯的对照表：原生看板角色（mac/Sources 冻结规格，D3）→ tokens.css 的 --type-* token。
// 单源纪律：CSS 值的真源是 tokens.css 的 type-scale 块；本表只是它的可读镜像，供 typeScale.test.ts 钉三方相等
// （CSS 值 ↔ 本表 ↔ Swift 源行真的写着这个 size/weight）。v2.0.0：只消费在退役看板 / 详情 / 列上的九个角色（卡面细字 /
// 落点行 / 占位卡 / 详情标题两级 / 展开详情 / 顶栏小字 / 列计数 / 空列）随之删除；活体样式指南随 §66 退役。
// 改任何一处不改另两处 = 测试红。字重词表 = SF Pro：regular 400 / medium 500 / semibold 600 / bold 700。

export type SwiftWeight = "regular" | "medium" | "semibold" | "bold";

export interface TypeRole {
  /** tokens.css 里的自定义属性名 */
  token: string;
  /** tokens.css 里的完整 `font` 简写值（逐字） */
  font: string;
  /** 原生源行：file 相对 mac/Sources，line 为 1-based 行号；该行必须含 `.font(.system(size: N[, weight: .W][, design: .monospaced]))` */
  swift: { file: string; line: number; size: number; weight: SwiftWeight; mono?: boolean };
  /** 角色说明（styleguide 展示） */
  zh: string;
  en: string;
}

export const WEIGHT_OF: Record<SwiftWeight, number> = { regular: 400, medium: 500, semibold: 600, bold: 700 };

export const TYPE_SCALE: TypeRole[] = [
  {
    token: "--type-card-title-lg", font: "var(--w-semibold) calc(15px * var(--text-scale))/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1074, size: 15, weight: "semibold" },
    zh: "提案卡摘要（ApprovalCardView 大白话一句）", en: "Proposal card summary (ApprovalCardView plain-language line)",
  },
  {
    token: "--type-card-title", font: "var(--w-medium) calc(12px * var(--text-scale))/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1562, size: 12, weight: "medium" },
    zh: "行标题：运行中 / 待验收 / 阶段性完成 / 潜在任务 / 归档 / 回收站（TaskRow.rowTitle）", en: "Row title: running / review / done / backlog / archive / trash (TaskRow.rowTitle)",
  },
  {
    token: "--type-card-body", font: "var(--w-regular) calc(11px * var(--text-scale))/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1724, size: 11, weight: "regular" },
    zh: "正文：合并建议「接受后将执行」条目 · 提问 · 分歧", en: "Body: merge-suggestion action-plan items · question · disagreement",
  },
  {
    token: "--type-card-meta", font: "var(--w-regular) calc(10px * var(--text-scale))/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1593, size: 10, weight: "regular" },
    zh: "meta 小字：相对时间 · 耗时 · 验收于 · 错误一句", en: "Meta: relative time · took · accepted · error line",
  },
  {
    token: "--type-card-meta-strong", font: "var(--w-medium) calc(10px * var(--text-scale))/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1307, size: 10, weight: "medium" },
    zh: "会议纪要行的小标（recaps.css）/ 截止 / T2 提示", en: "Recap row small heading (recaps.css) / deadline / T2 hint",
  },
  {
    token: "--type-card-fine-mono", font: "var(--w-regular) calc(9px * var(--text-scale))/1.4 var(--font-mono)",
    swift: { file: "Cards.swift", line: 307, size: 9, weight: "regular", mono: true },
    zh: "右上角卡 id（idTag）· 等宽小字", en: "Card id (idTag) · fine monospace",
  },
  {
    token: "--type-chip", font: "var(--w-semibold) calc(10px * var(--text-scale))/calc(16px * var(--text-scale)) var(--font-sans)",
    swift: { file: "Cards.swift", line: 69, size: 10, weight: "semibold" },
    zh: "章 / chip（Badge：tier · 状态 · repo · 已并入×N · 被提×N …）", en: "Chips (Badge: tier · state · repo · folded ×N · raised ×N …)",
  },
  {
    token: "--type-button", font: "var(--w-regular) calc(11px * var(--text-scale))/calc(18px * var(--text-scale)) var(--font-sans)",
    swift: { file: "Cards.swift", line: 362, size: 11, weight: "regular" },
    zh: "动作行按钮（CardSurface .bordered .small）", en: "Action-row buttons (CardSurface .bordered .small)",
  },
  {
    token: "--type-lane-title", font: "var(--w-semibold) calc(12px * var(--text-scale))/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 22, size: 12, weight: "semibold" },
    zh: "列头标题（SectionHeader，次级色）· 书立条竖排标题", en: "Lane header title (SectionHeader, secondary) · bookend strip title",
  },
  {
    token: "--type-lane-help", font: "var(--w-regular) calc(12px * var(--text-scale))/1.55 var(--font-sans)",
    swift: { file: "Cards.swift", line: 36, size: 12, weight: "regular" },
    zh: "列头「?」气泡正文", en: "Lane header ? popover text",
  },
  {
    token: "--type-composer", font: "var(--w-regular) calc(12px * var(--text-scale))/1.4 var(--font-sans)",
    swift: { file: "Composer.swift", line: 114, size: 12, weight: "regular" },
    zh: "列顶输入框正文与占位文案（Composer TextField）", en: "Lane composer input + placeholder (Composer TextField)",
  },
  {
    token: "--type-composer-hint", font: "var(--w-regular) calc(10px * var(--text-scale))/1.4 var(--font-sans)",
    swift: { file: "Composer.swift", line: 147, size: 10, weight: "regular" },
    zh: "输入框下的提示 / 回执 / 错误一行", en: "Composer hint / receipt / error line",
  },
  {
    token: "--type-header-title", font: "var(--w-semibold) calc(14px * var(--text-scale))/1.4 var(--font-sans)",
    swift: { file: "MainWindow.swift", line: 266, size: 14, weight: "semibold" },
    zh: "侧栏 app 标题（MainWindow sidebar 标题行）", en: "Sidebar app title (MainWindow sidebar title row)",
  },
  {
    token: "--type-rail-item", font: "var(--w-regular) calc(13px * var(--text-scale))/1.4 var(--font-sans)",
    swift: { file: "MainWindow.swift", line: 308, size: 13, weight: "regular" },
    zh: "左侧导航栏条目文字（MainWindow sectionRow）", en: "Sidebar item label (MainWindow sectionRow)",
  },
  {
    token: "--type-header-control", font: "var(--w-regular) calc(12px * var(--text-scale))/1.4 var(--font-sans)",
    swift: { file: "Kanban.swift", line: 183, size: 12, weight: "regular" },
    zh: "顶栏控件：搜索框 · 提建议 / 选择 类文字按钮", en: "Header controls: search field · text buttons",
  },
];
