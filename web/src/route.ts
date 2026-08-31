// 深链路由：URL query 序列化模式，fork 自 dashi web/src/issueRoute.ts（Apache-2.0，NOTICE 登记）。
// 单页两个维度：?page=trash（回收站单独页，缺省 board）+ ?card=R-101（详情抽屉深链）。
// 约定：路由只存"哪一页 + 哪张卡"，过滤器序列化由 A8 仿 dashi taskFilters.ts 在独立模块追加。
const CARD_QUERY_PARAM = "card";
const PAGE_QUERY_PARAM = "page";

export type AppPage = "board" | "trash";

export function readCardId(search: string): string | null {
  const id = new URLSearchParams(search).get(CARD_QUERY_PARAM)?.trim().toUpperCase();
  return id || null;
}

export function readPage(search: string): AppPage {
  return new URLSearchParams(search).get(PAGE_QUERY_PARAM) === "trash" ? "trash" : "board";
}

export function buildAppUrl(href: string, page: AppPage, cardId: string | null): URL {
  const url = new URL(href);

  if (page !== "board") url.searchParams.set(PAGE_QUERY_PARAM, page);
  else url.searchParams.delete(PAGE_QUERY_PARAM);

  if (cardId) url.searchParams.set(CARD_QUERY_PARAM, cardId.trim().toUpperCase());
  else url.searchParams.delete(CARD_QUERY_PARAM);

  return url;
}
