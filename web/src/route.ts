// 深链路由：URL query 序列化模式，fork 自 dashi web/src/issueRoute.ts（Apache-2.0，NOTICE 登记）。
// 单页两个维度：?page=trash（回收站单独页，缺省 board）+ ?card=R-101（详情抽屉深链）。
// ?page=styleguide = 活体样式指南（开发者页，仅 URL 直达——看板头部不放入口）。
// 约定：路由只存"哪一页 + 哪张卡"，过滤器序列化由 A8 仿 dashi taskFilters.ts 在独立模块追加。
const CARD_QUERY_PARAM = "card";
const PAGE_QUERY_PARAM = "page";

export type AppPage = "board" | "trash" | "styleguide";

export function readCardId(search: string): string | null {
  // 保留大小写：id 由 SAFE_ID_RE 界定（允许小写），匹配按原样精确比对——不做 case 折叠
  const id = new URLSearchParams(search).get(CARD_QUERY_PARAM)?.trim();
  return id || null;
}

export function readPage(search: string): AppPage {
  const page = new URLSearchParams(search).get(PAGE_QUERY_PARAM);
  if (page === "trash" || page === "styleguide") return page;
  return "board";
}

export function buildAppUrl(href: string, page: AppPage, cardId: string | null): URL {
  const url = new URL(href);

  if (page !== "board") url.searchParams.set(PAGE_QUERY_PARAM, page);
  else url.searchParams.delete(PAGE_QUERY_PARAM);

  if (cardId) url.searchParams.set(CARD_QUERY_PARAM, cardId.trim());
  else url.searchParams.delete(CARD_QUERY_PARAM);

  return url;
}
