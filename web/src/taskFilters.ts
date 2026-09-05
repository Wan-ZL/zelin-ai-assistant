// 过滤器序列化 + 客户端匹配。模式 fork 自 dashi web/src/taskFilters.ts（Apache-2.0，NOTICE 登记）：
// URL query 即过滤器唯一持久化（readTaskFilters/writeTaskFilters 形制），replaceState 不进历史栈。
// 维度 = tier / deadline / reraised(回锅) + ⌘F 搜索。BUILD-CONTRACT §2.2 原钦点还有 type / channel(渠道)
// 两维，2026-09-04 owner 决策 D28（docs/design/vnext2-plan.md）去掉：旧 URL 里的 `type=` / `channel=`
// 容忍读取（忽略），下次写回时丢弃（LEGACY_PARAMS）。
//
// 跨分区匹配语义（保守实现，见 §0.9——投影各分区形状异构，契约未钦点统一语义）：
//   一个维度只约束「结构上携带该字段」的行——running/completed 行没有 tier，
//   选 tier=T2 时它们**保持可见**（过滤器绝不隐藏它读不懂的行）；search 例外，作用于全部行
//   （词表 + 归一化 AND 匹配 = CONTRACT §37.2，见下方 ⌘F 一节）。
//   识别提案形行：`tier` 为字符串（dashboard.py 只给 needs_approval 行发 tier）；
//   提案形行缺 `reraised` 字段 = false（会被「只看回锅」滤掉）。
//   提案列的 processing 占位行（raising / 捕获中）不参与过滤隐藏——那是 BoardLanes.pick 的事，
//   本模块的匹配函数对它们照常判定（原生 Store.boardApprovals 同一分工）。
// TODO(contract): 过滤器的跨分区语义未入 CONTRACT；A12 修宪草案可引用本注释钉死。
export type DeadlineFilter = "all" | "has" | "soon" | "overdue" | "none";

export interface CardFilters {
  tiers: string[];       // T0/T1/T2（多选，OR）
  deadline: DeadlineFilter;
  reraisedOnly: boolean; // 只看回锅（§re-raise 的 needs_approval.reraised）
  search: string;        // ⌘F 全局搜索词
}

export const EMPTY_CARD_FILTERS: CardFilters = {
  tiers: [],
  deadline: "all",
  reraisedOnly: false,
  search: "",
};

const DEADLINE_VALUES: readonly DeadlineFilter[] = ["all", "has", "soon", "overdue", "none"];

// D28 退役维度的 URL 参数：读时不认、写时清掉（旧书签 / 深链带着它们进来不报错）
const LEGACY_PARAMS = ["type", "channel"] as const;

// ----- URL 序列化（参数名与 route.ts 的 page/card 正交，互不覆写） ------------- #

function readList(params: URLSearchParams, key: string): string[] {
  return (params.get(key) ?? "").split(",").map((v) => v.trim()).filter(Boolean);
}

export function readCardFilters(search: string): CardFilters {
  const params = new URLSearchParams(search);
  const deadline = params.get("deadline") as DeadlineFilter | null;
  return {
    tiers: readList(params, "tier"),
    deadline: deadline && DEADLINE_VALUES.includes(deadline) ? deadline : "all",
    reraisedOnly: params.get("reraised") === "1",
    search: params.get("q") ?? "",
  };
}

/** 纯函数：把过滤器写进 URL（只动自己的参数；测试直接断言）。 */
export function applyCardFilters(url: URL, filters: CardFilters): URL {
  const setOrDelete = (key: string, value: string) => {
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  };
  setOrDelete("tier", filters.tiers.join(","));
  setOrDelete("deadline", filters.deadline === "all" ? "" : filters.deadline);
  setOrDelete("reraised", filters.reraisedOnly ? "1" : "");
  setOrDelete("q", filters.search.trim());
  for (const key of LEGACY_PARAMS) url.searchParams.delete(key);
  return url;
}

export function writeCardFilters(filters: CardFilters) {
  const url = applyCardFilters(new URL(window.location.href), filters);
  window.history.replaceState(null, "", url);
}

/** 激活维度数（清除按钮的角标；0 = 无过滤） */
export function cardFilterCount(filters: CardFilters): number {
  return Number(filters.tiers.length > 0)
    + Number(filters.deadline !== "all")
    + Number(filters.reraisedOnly)
    + Number(Boolean(filters.search.trim()));
}

/** 多选维度的开关切换（FilterBar 用） */
export function toggleFilterValue(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

// ----- 行匹配（任意分区行 = Record<string, unknown>，wire 字段绝不改写） ------- #

function rowDaysLeft(row: Record<string, unknown>): number | null {
  if (typeof row.days_left === "number") return row.days_left;
  if (typeof row.deadline !== "string" || !row.deadline) return null;
  const t = Date.parse(row.deadline);
  return Number.isNaN(t) ? null : Math.floor((t - Date.now()) / 86_400_000);
}

// ----- ⌘F 搜索（CONTRACT §37.2；原生 shared/Sources/SearchMatch.swift + Store.swift searchFields 的 web 孪生） -- #
// act/lib/match_corpus.normalize 是同一规则的 python 孪生——三边语义同步改（§38.1）。

/** §37.2 归一化：lowercase 并剥掉 `-` / `_` / `.` / 空白，拉丁 / 数字串按无分隔符比较
 *  （"eb1" 命中 "EB-1A"、"h1b" 命中 "H-1B"，"eb2" 不误命中 "EB-1A"）；CJK 与其余字符原样通过。 */
export function normalizeSearchText(s: string): string {
  return s.toLowerCase().replace(/[\s\-_.]+/g, "");
}

/** 查询按空白切词、逐词归一化、去空——AND 语义的词表；空查询 = []（直通）。 */
export function searchTerms(query: string): string[] {
  return query.split(/\s+/).map(normalizeSearchText).filter(Boolean);
}

// §37.2 词表（per lane 按行有什么搜什么，投影行缺的键静默跳过）：id + 冻结 title/name + display_title
// + former_titles + summary + notes_text（notes 折叠）+ plan/dod + delivered_summary/final_draft
// + source quotes + agent_name。web 追加（add-only）：§60 work_id / display_id（用户记的是 R-280，
// 主键可能是 P-012）、tier / type 与 sources 的 who / channel（D28 退役维度后仍可搜）。
// definition_of_done 是 registry 原名（详情行 / 兜底），投影行叫 dod——两个都收，字符串或数组皆可。
const SEARCH_FIELDS = [
  "id", "work_id", "display_id", "title", "name", "display_title", "former_titles", "summary", "notes_text",
  "plan", "dod", "definition_of_done", "delivered_summary", "final_draft", "agent_name", "tier", "type",
] as const;

function pushText(parts: string[], value: unknown) {
  if (typeof value === "string") parts.push(value);
  else if (Array.isArray(value)) for (const v of value) if (typeof v === "string") parts.push(v);
}

/** 一行的归一化 haystack（非空字段各自一条——AND 的每个词只需命中其中一条）。 */
export function searchHaystack(row: Record<string, unknown>): string[] {
  const parts: string[] = [];
  for (const key of SEARCH_FIELDS) pushText(parts, row[key]);
  if (Array.isArray(row.sources)) {
    for (const s of row.sources) {
      const src = s as Record<string, unknown> | null;
      for (const key of ["who", "quote", "channel"]) pushText(parts, src?.[key]);
    }
  }
  return parts.map(normalizeSearchText).filter(Boolean);
}

/** §37.2：每个查询词都得命中至少一个归一化字段；空查询直通。 */
export function matchesCardSearch(row: Record<string, unknown>, search: string): boolean {
  const terms = searchTerms(search);
  if (!terms.length) return true;
  const haystack = searchHaystack(row);
  return terms.every((t) => haystack.some((field) => field.includes(t)));
}

export function matchesCardFilters(row: Record<string, unknown>, filters: CardFilters): boolean {
  const isProposalShaped = typeof row.tier === "string"; // dashboard.py 只给提案行发 tier

  if (filters.tiers.length && typeof row.tier === "string" && !filters.tiers.includes(row.tier)) {
    return false;
  }
  if (filters.deadline !== "all" && isProposalShaped) {
    const hasDeadline = typeof row.deadline === "string" && row.deadline !== "";
    const days = rowDaysLeft(row);
    if (filters.deadline === "has" && !hasDeadline) return false;
    if (filters.deadline === "none" && hasDeadline) return false;
    if (filters.deadline === "soon" && !(days !== null && days >= 0 && days <= 7)) return false;
    if (filters.deadline === "overdue" && !(days !== null && days < 0)) return false;
  }
  if (filters.reraisedOnly && isProposalShaped && row.reraised !== true) return false;

  return matchesCardSearch(row, filters.search);
}
