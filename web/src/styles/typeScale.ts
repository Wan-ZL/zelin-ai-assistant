// 字号/字重梯的对照表：原生看板角色（mac/Sources 冻结规格，D3）→ tokens.css 的 --type-* token。
// 单源纪律：CSS 值的真源是 tokens.css 的 type-scale 块；本表只是它的可读镜像，供
//   ① 活体样式指南第 5 节逐行渲染（角色 / Swift 行 / token / 值），
//   ② typeScale.test.ts 钉三方相等（CSS 值 ↔ 本表 ↔ Swift 源行真的写着这个 size/weight）。
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
    token: "--type-card-title-lg", font: "600 15px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1074, size: 15, weight: "semibold" },
    zh: "提案卡摘要（ApprovalCardView 大白话一句）", en: "Proposal card summary (ApprovalCardView plain-language line)",
  },
  {
    token: "--type-card-title", font: "500 12px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1562, size: 12, weight: "medium" },
    zh: "行标题：运行中 / 待验收 / 阶段性完成 / 潜在任务 / 归档 / 回收站（TaskRow.rowTitle）", en: "Row title: running / review / done / backlog / archive / trash (TaskRow.rowTitle)",
  },
  {
    token: "--type-card-placeholder", font: "400 13px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 946, size: 13, weight: "regular" },
    zh: "AI 研究中占位卡主句（processingBody）", en: "Processing placeholder headline (processingBody)",
  },
  {
    token: "--type-card-line", font: "500 11px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1146, size: 11, weight: "medium" },
    zh: "落点行 🟢/📄/🟠 · 回锅新增 · 修改意见合并中（targetLine）", en: "Target line 🟢/📄/🟠 · returned note · merging feedback (targetLine)",
  },
  {
    token: "--type-card-body", font: "400 11px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1724, size: 11, weight: "regular" },
    zh: "正文：详情 summary · 📋 计划条目 · 交付了什么正文 · 提问 · 分歧", en: "Body: detail summary · plan items · delivered text · question · disagreement",
  },
  {
    token: "--type-card-body-strong", font: "600 11px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 530, size: 11, weight: "semibold" },
    zh: "📋 计划里的「[修改方向]」行（PlanListView rework 分支，橙）", en: "Plan rows starting with [修改方向] (PlanListView rework branch, orange)",
  },
  {
    token: "--type-card-meta", font: "400 10px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1593, size: 10, weight: "regular" },
    zh: "meta 小字：相对时间 · 耗时 · 验收于 · 怎样算办完条目 · 错误一句 · 引文", en: "Meta: relative time · took · accepted · DoD items · error line · quotes",
  },
  {
    token: "--type-card-meta-strong", font: "500 10px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1307, size: 10, weight: "medium" },
    zh: "需求来自 who · channel · date 行 / 截止 / T2 提示", en: "Source who · channel · date / deadline / T2 hint",
  },
  {
    token: "--type-card-fine", font: "400 9px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1639, size: 9, weight: "regular" },
    zh: "单击复制指令 行 · claude agents 列表名", en: "Click-to-copy line · claude agents list name",
  },
  {
    token: "--type-card-fine-mono", font: "400 9px/1.4 var(--font-mono)",
    swift: { file: "Cards.swift", line: 307, size: 9, weight: "regular", mono: true },
    zh: "右上角卡 id（idTag）· 日志/指令 路径行 · 会话 ID", en: "Card id (idTag) · log / command path lines · session id",
  },
  {
    token: "--type-card-error-mono", font: "400 10px/1.5 var(--font-mono)",
    swift: { file: "Cards.swift", line: 768, size: 10, weight: "regular", mono: true },
    zh: "错误全文块（ErrorTextBlock）", en: "Full error block (ErrorTextBlock)",
  },
  {
    token: "--type-chip", font: "600 10px/16px var(--font-sans)",
    swift: { file: "Cards.swift", line: 69, size: 10, weight: "semibold" },
    zh: "章 / chip（Badge：tier · 状态 · repo · 已并入×N · 被提×N …）", en: "Chips (Badge: tier · state · repo · folded ×N · raised ×N …)",
  },
  {
    token: "--type-button", font: "400 11px/18px var(--font-sans)",
    swift: { file: "Cards.swift", line: 362, size: 11, weight: "regular" },
    zh: "动作行按钮（CardSurface .bordered .small）", en: "Action-row buttons (CardSurface .bordered .small)",
  },
  {
    token: "--type-details-toggle", font: "400 11px/18px var(--font-sans)",
    swift: { file: "Cards.swift", line: 362, size: 11, weight: "regular" },
    zh: "展开详情 ▸ / 收起 ▾（动作行同字号，plain 灰链接）", en: "Details ▸ / Collapse ▾ (action-row size, plain grey link)",
  },
  {
    token: "--type-detail-title", font: "500 12px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1277, size: 12, weight: "medium" },
    zh: "展开后的长技术标题（expandedDetail 首行）", en: "Long technical title inside details (expandedDetail first line)",
  },
  {
    token: "--type-detail-heading", font: "600 11px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1301, size: 11, weight: "semibold" },
    zh: "详情小节头：💬 需求来自 · 📋 要做什么 · 💰 费用 · 📎 折叠进来的信息", en: "Detail section heads: requested by · plan · cost · folded-in updates",
  },
  {
    token: "--type-detail-subheading", font: "600 10px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 1089, size: 10, weight: "semibold" },
    zh: "怎样算办完： · 交付了什么： · 验收清单 · 错误全文", en: "Definition of done · Delivered · Acceptance checklist · Full error",
  },
  {
    token: "--type-lane-title", font: "600 12px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 22, size: 12, weight: "semibold" },
    zh: "列头标题（SectionHeader，次级色）· 书立条竖排标题", en: "Lane header title (SectionHeader, secondary) · bookend strip title",
  },
  {
    token: "--type-lane-count", font: "700 11px/16px var(--font-sans)",
    swift: { file: "Cards.swift", line: 43, size: 11, weight: "bold" },
    zh: "列头计数胶囊（SectionHeader count）", en: "Lane header count capsule (SectionHeader count)",
  },
  {
    token: "--type-lane-help", font: "400 12px/1.55 var(--font-sans)",
    swift: { file: "Cards.swift", line: 36, size: 12, weight: "regular" },
    zh: "列头「?」气泡正文", en: "Lane header ? popover text",
  },
  {
    token: "--type-lane-empty", font: "400 11px/1.4 var(--font-sans)",
    swift: { file: "Cards.swift", line: 58, size: 11, weight: "regular" },
    zh: "空列 / 无匹配（EmptyRow · lanePlaceholder）", en: "Empty lane / no matches (EmptyRow · lanePlaceholder)",
  },
  {
    token: "--type-composer", font: "400 12px/1.4 var(--font-sans)",
    swift: { file: "Composer.swift", line: 114, size: 12, weight: "regular" },
    zh: "列顶输入框正文与占位文案（Composer TextField）", en: "Lane composer input + placeholder (Composer TextField)",
  },
  {
    token: "--type-composer-hint", font: "400 10px/1.4 var(--font-sans)",
    swift: { file: "Composer.swift", line: 147, size: 10, weight: "regular" },
    zh: "输入框下的提示 / 回执 / 错误一行", en: "Composer hint / receipt / error line",
  },
  {
    token: "--type-header-title", font: "600 14px/1.4 var(--font-sans)",
    swift: { file: "MainWindow.swift", line: 266, size: 14, weight: "semibold" },
    zh: "顶栏 app 标题（MainWindow 侧栏标题）", en: "Header app title (MainWindow sidebar title)",
  },
  {
    token: "--type-header-control", font: "400 12px/1.4 var(--font-sans)",
    swift: { file: "Kanban.swift", line: 183, size: 12, weight: "regular" },
    zh: "顶栏控件：搜索框 · 提建议 / 选择 类文字按钮", en: "Header controls: search field · text buttons",
  },
  {
    token: "--type-header-meta", font: "400 10px/1.4 var(--font-sans)",
    swift: { file: "Freshness.swift", line: 32, size: 10, weight: "regular" },
    zh: "顶栏「数据生成于 …」新鲜度 / 部署状态小字", en: "Header freshness / deploy status small text",
  },
];
