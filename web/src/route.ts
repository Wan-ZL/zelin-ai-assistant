// 深链路由：URL query 序列化模式，fork 自 dashi web/src/issueRoute.ts（Apache-2.0，NOTICE 登记）。
// 单页两个维度：?page=trash（回收站单独页，缺省 board）+ ?card=R-101（详情抽屉深链）。
// ?page=styleguide = 活体样式指南（开发者页，仅 URL 直达——看板头部不放入口）。
// ?page=settings = 设置页（§59 首个 section「模型」；顶栏齿轮入口）。
// ?page=recaps = 会议纪要页（§63；顶栏入口）——recap 不是卡，不走 ?card= 抽屉。
// ?page=archive（永久性完成页）/ permissions（权限体检）/ diagnostics（诊断）/ setup（首次运行向导）= §68 P4 parity 页。
// ?anchor=<section id>：设置页滚到某个 section（字幕悬浮窗齿轮深链 live_captions，§61.3）。
// 约定：路由只存"哪一页 + 哪张卡"，过滤器序列化由 A8 仿 dashi taskFilters.ts 在独立模块追加。
const CARD_QUERY_PARAM = "card";
const PAGE_QUERY_PARAM = "page";
const ANCHOR_QUERY_PARAM = "anchor";

export type AppPage = "board" | "trash" | "styleguide" | "settings" | "recaps" | "archive" | "permissions" | "diagnostics" | "setup";
const PAGES: readonly AppPage[] = ["board", "trash", "styleguide", "settings", "recaps", "archive", "permissions", "diagnostics", "setup"];

export function readCardId(search: string): string | null {
  // 保留大小写：id 由 SAFE_ID_RE 界定（允许小写），匹配按原样精确比对——不做 case 折叠
  const id = new URLSearchParams(search).get(CARD_QUERY_PARAM)?.trim();
  return id || null;
}

export function readPage(search: string): AppPage {
  const page = new URLSearchParams(search).get(PAGE_QUERY_PARAM);
  return page && (PAGES as readonly string[]).includes(page) ? (page as AppPage) : "board";
}

/** ?anchor=<id>（设置页 section 深链）；只认 [a-z0-9_-]，其余当没有 */
export function readAnchor(search: string): string | null {
  const anchor = new URLSearchParams(search).get(ANCHOR_QUERY_PARAM)?.trim() ?? "";
  return /^[a-z0-9_-]{1,40}$/i.test(anchor) ? anchor : null;
}

export function buildAppUrl(href: string, page: AppPage, cardId: string | null): URL {
  const url = new URL(href);

  if (page !== "board") url.searchParams.set(PAGE_QUERY_PARAM, page);
  else url.searchParams.delete(PAGE_QUERY_PARAM);

  if (cardId) url.searchParams.set(CARD_QUERY_PARAM, cardId.trim());
  else url.searchParams.delete(CARD_QUERY_PARAM);

  return url;
}

/** 整页导航（向导完成 / 重跑向导 / 壳命令回看板）：集中一处便于测试替身；replace=true 不进历史栈 */
export function navigate(url: URL | string, replace = false): void {
  const href = url.toString();
  if (replace) window.location.replace(href);
  else window.location.assign(href);
}
