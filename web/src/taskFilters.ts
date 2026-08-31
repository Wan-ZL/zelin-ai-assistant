// 过滤器序列化 + 客户端匹配。模式 fork 自 dashi web/src/taskFilters.ts（Apache-2.0，NOTICE 登记）：
// URL query 即过滤器唯一持久化（readTaskFilters/writeTaskFilters 形制），replaceState 不进历史栈。
// 维度 = BUILD-CONTRACT §2.2 钦点：tier / type / channel(渠道) / deadline / reraised(回锅) + ⌘F 搜索。
//
// 跨分区匹配语义（保守实现，见 §0.9——投影各分区形状异构，契约未钦点统一语义）：
//   一个维度只约束「结构上携带该字段」的行——running/completed 行没有 tier，
//   选 tier=T2 时它们**保持可见**（过滤器绝不隐藏它读不懂的行）；search 例外，作用于全部行。
//   识别提案形行：`tier` 为字符串（dashboard.py 只给 needs_approval 行发 tier）；
//   提案形行缺 `reraised` 字段 = false（会被「只看回锅」滤掉）。
// TODO(contract): 过滤器的跨分区语义未入 CONTRACT；A12 修宪草案可引用本注释钉死。
import type { Board } from "./types";

export type DeadlineFilter = "all" | "has" | "soon" | "overdue" | "none";

export interface CardFilters {
  tiers: string[];       // T0/T1/T2（多选，OR）
  types: string[];       // debt/trash 行的 type（多选，OR）
  channels: string[];    // sources[].channel（多选，OR）
  deadline: DeadlineFilter;
  reraisedOnly: boolean; // 只看回锅（§re-raise 的 needs_approval.reraised）
  search: string;        // ⌘F 全局搜索词
}

export const EMPTY_CARD_FILTERS: CardFilters = {
  tiers: [],
  types: [],
  channels: [],
  deadline: "all",
  reraisedOnly: false,
  search: "",
};

const DEADLINE_VALUES: readonly DeadlineFilter[] = ["all", "has", "soon", "overdue", "none"];

// ----- URL 序列化（参数名与 route.ts 的 page/card 正交，互不覆写） ------------- #

function readList(params: URLSearchParams, key: string): string[] {
  return (params.get(key) ?? "").split(",").map((v) => v.trim()).filter(Boolean);
}

export function readCardFilters(search: string): CardFilters {
  const params = new URLSearchParams(search);
  const deadline = params.get("deadline") as DeadlineFilter | null;
  return {
    tiers: readList(params, "tier"),
    types: readList(params, "type"),
    channels: readList(params, "channel"),
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
  setOrDelete("type", filters.types.join(","));
  setOrDelete("channel", filters.channels.join(","));
  setOrDelete("deadline", filters.deadline === "all" ? "" : filters.deadline);
  setOrDelete("reraised", filters.reraisedOnly ? "1" : "");
  setOrDelete("q", filters.search.trim());
  return url;
}

export function writeCardFilters(filters: CardFilters) {
  const url = applyCardFilters(new URL(window.location.href), filters);
  window.history.replaceState(null, "", url);
}

/** 激活维度数（清除按钮的角标；0 = 无过滤） */
export function cardFilterCount(filters: CardFilters): number {
  return Number(filters.tiers.length > 0)
    + Number(filters.types.length > 0)
    + Number(filters.channels.length > 0)
    + Number(filters.deadline !== "all")
    + Number(filters.reraisedOnly)
    + Number(Boolean(filters.search.trim()));
}

/** 多选维度的开关切换（FilterBar 用） */
export function toggleFilterValue(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

// ----- 行匹配（任意分区行 = Record<string, unknown>，wire 字段绝不改写） ------- #

function rowChannels(row: Record<string, unknown>): string[] | null {
  if (!Array.isArray(row.sources)) return null; // 无 sources 概念的行：维度不适用
  const out: string[] = [];
  for (const s of row.sources) {
    const channel = (s as Record<string, unknown> | null)?.channel;
    if (typeof channel === "string" && channel) out.push(channel);
  }
  return out;
}

function rowDaysLeft(row: Record<string, unknown>): number | null {
  if (typeof row.days_left === "number") return row.days_left;
  if (typeof row.deadline !== "string" || !row.deadline) return null;
  const t = Date.parse(row.deadline);
  return Number.isNaN(t) ? null : Math.floor((t - Date.now()) / 86_400_000);
}

const SEARCH_FIELDS = ["id", "title", "name", "summary", "delivered_summary", "tier", "type"] as const;

export function matchesCardSearch(row: Record<string, unknown>, search: string): boolean {
  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  const parts: string[] = [];
  for (const key of SEARCH_FIELDS) {
    if (typeof row[key] === "string") parts.push(row[key] as string);
  }
  if (Array.isArray(row.sources)) {
    for (const s of row.sources) {
      const src = s as Record<string, unknown> | null;
      for (const key of ["who", "quote", "channel"]) {
        if (typeof src?.[key] === "string") parts.push(src[key] as string);
      }
    }
  }
  return parts.join(" ").toLowerCase().includes(needle);
}

export function matchesCardFilters(row: Record<string, unknown>, filters: CardFilters): boolean {
  const isProposalShaped = typeof row.tier === "string"; // dashboard.py 只给提案行发 tier

  if (filters.tiers.length && typeof row.tier === "string" && !filters.tiers.includes(row.tier)) {
    return false;
  }
  if (filters.types.length && typeof row.type === "string" && !filters.types.includes(row.type)) {
    return false;
  }
  if (filters.channels.length) {
    const channels = rowChannels(row);
    if (channels !== null && !channels.some((c) => filters.channels.includes(c))) return false;
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

// ----- 词表来源（chip 选项从当前看板动态收集，未知枚举值原样展示） ------------- #

function collectFrom(rows: unknown[] | undefined, pick: (row: Record<string, unknown>) => string[]): string[] {
  const seen = new Set<string>();
  for (const row of rows ?? []) {
    if (row && typeof row === "object") pick(row as Record<string, unknown>).forEach((v) => seen.add(v));
  }
  return [...seen].sort();
}

export function collectTypes(board: Board | null): string[] {
  if (!board) return [];
  return collectFrom(
    [...board.debt, ...board.trash],
    (row) => (typeof row.type === "string" && row.type ? [row.type] : []),
  );
}

export function collectChannels(board: Board | null): string[] {
  if (!board) return [];
  return collectFrom(
    [...board.needs_approval, ...board.review, ...board.debt],
    (row) => rowChannels(row) ?? [],
  );
}
