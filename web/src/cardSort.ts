// 卡片排序——镜像原生 mac/Sources/Store.swift sortCards（v0.10.3 契约一）：
//   "newest"（默认）：id 数字后缀降序；无法解析的 id 排最后、保持原序；
//   "oldest"：id 数字后缀升序，同样的不可解析尾规则；
//   "deadline"：有 deadline 的先按 YYYY-MM-DD 字符串升序，其余按 newest；
//               列的行模型没有 deadline 字段 → 整列退化为 newest。
// 一律稳定排序（decorate 原始下标，不依赖引擎的稳定性承诺）。数字后缀不看前缀
// ——R-013 / P-201（#135 两段 id）/ MS-7 同一把尺；同后缀按原序。
// 偏好名逐字镜像原生 UserDefaults 键 cardSortOrder，持久化在 localStorage。
export type SortOrder = "newest" | "oldest" | "deadline";

export const SORT_ORDERS: readonly SortOrder[] = ["newest", "oldest", "deadline"];
export const DEFAULT_SORT_ORDER: SortOrder = "newest";
export const SORT_STORAGE_KEY = "cardSortOrder";

/** 任意字符串 → 合法排序值（未知/缺省 → newest，同原生 default 分支） */
export function normalizeSortOrder(value: unknown): SortOrder {
  return value === "oldest" || value === "deadline" ? value : DEFAULT_SORT_ORDER;
}

/** id 的尾部数字串（"R-013" → 13，"P-201" → 201）；没有 → null */
export function idSuffix(id: string): number | null {
  const match = /(\d+)$/.exec(id);
  if (!match) return null;
  const n = Number(match[1]);
  return Number.isSafeInteger(n) ? n : null;
}

interface Row<T> {
  offset: number;
  element: T;
  suffix: number | null;
}

function bySuffix<T>(a: Row<T>, b: Row<T>, descending: boolean): number {
  if (a.suffix != null && b.suffix != null) {
    if (a.suffix === b.suffix) return a.offset - b.offset;
    return descending ? b.suffix - a.suffix : a.suffix - b.suffix;
  }
  if (a.suffix != null) return -1; // 可解析的在前
  if (b.suffix != null) return 1;
  return a.offset - b.offset;
}

export function sortCards<T extends { id: string }>(
  items: readonly T[],
  order: SortOrder,
  deadline?: (item: T) => string | null | undefined,
): T[] {
  const rows: Row<T>[] = items.map((element, offset) => ({ offset, element, suffix: idSuffix(element.id) }));
  const newestFirst = (a: Row<T>, b: Row<T>) => bySuffix(a, b, true);
  if (order === "oldest") {
    return rows.sort((a, b) => bySuffix(a, b, false)).map((r) => r.element);
  }
  if (order === "deadline" && deadline) {
    const dated = (r: Row<T>) => {
      const d = deadline(r.element);
      return typeof d === "string" && d ? d : null;
    };
    return rows
      .sort((a, b) => {
        const da = dated(a);
        const db = dated(b);
        if (da != null && db != null) return da === db ? a.offset - b.offset : da < db ? -1 : 1;
        if (da != null) return -1; // 有期限的在前
        if (db != null) return 1;
        return newestFirst(a, b);
      })
      .map((r) => r.element);
  }
  return rows.sort(newestFirst).map((r) => r.element);
}

/** 读持久化偏好（localStorage 不可用/未设 → newest） */
export function readSortOrder(): SortOrder {
  try {
    return normalizeSortOrder(window.localStorage.getItem(SORT_STORAGE_KEY));
  } catch {
    return DEFAULT_SORT_ORDER;
  }
}

/** 写持久化偏好（写失败静默——本次会话仍然生效） */
export function writeSortOrder(order: SortOrder): void {
  try {
    window.localStorage.setItem(SORT_STORAGE_KEY, order);
  } catch {
    /* 隐私模式等 localStorage 不可写 */
  }
}
