// §37.1 摘要优先面的标题链（原生 shared/Sources/Contract.swift `displaySummary`）：
//   用户钦定名（user_titled && display_title）> summary > display_title > 冻结 title。
// 提案 / AI 研究中占位 / 潜在任务 / 回收站 / 永久性完成 五个面读它（原生 Cards.swift 945 / 1073 / 2028 /
// 2594 / 2728）；运行中 / 待验收 / 已完成是名字优先面（原生 rowTitle = display_title > name），不走这里。
// 纯函数、零依赖——server 投影的 display_title 恒非空（dashboard.py `_display_title`），所以没有这条链
// 的面上 LLM 短名永远压过大白话摘要，用户看不到摘要；钦定名（user_titled）则必须压过一切。
export interface HeadlineRow {
  title?: unknown;
  /** running 族行的冻结名（TaskRow.name）——只作最后兜底 */
  name?: unknown;
  summary?: unknown;
  display_title?: unknown;
  user_titled?: unknown;
  /** 投影行 / CardDetail 的其余键原样通过（wire add-only） */
  [key: string]: unknown;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/** 卡面标题（摘要优先面）；四级都空时回落 name，再空返回 ""——渲染端自己决定兜底词 */
export function cardHeadline(row: HeadlineRow): string {
  const displayTitle = str(row.display_title);
  if (row.user_titled === true && displayTitle) return displayTitle;
  return str(row.summary) ?? displayTitle ?? str(row.title) ?? str(row.name) ?? "";
}
