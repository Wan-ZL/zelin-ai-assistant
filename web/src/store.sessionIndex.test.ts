// store.sessionIndex —— §37.2 会话内容层的懒加载与重验（CONTRACT §37.2 第三条 / §49 `GET /api/search-index`；D45；
// 原生 Store.reloadSearchIndexIfNeeded：有查询时才按 (mtime,size) 重验、正文归一化一次存着）：
//   · 启动 / 空搜索不拉；搜索从空变非空（键入第一个字 / `?q=` 深链 / 后退带回）才拉——第一次全量；
//   · 继续键入（非空 → 非空）不再拉；搜索开着时每版看板落地条件 GET 重验（带 ETag，304 → 缓存不动）；
//   · 缺席（server 200 空表、无 ETag）/ 断网 → 空层（不抛、不清旧缓存以外的东西）；缺席 → 缺席不换对象；并发重验合并成一个在途请求。
// 经 vi.mock 替换 fetchSearchIndex / fetchBoard，零真实网络。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard, fetchSearchIndex } from "./api";
import { getState, initFiltersFromUrl, refreshBoard, refreshSessionIndex, resetStoreForTests, setFilters, syncRouteFromUrl } from "./store";
import type { Board } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchSearchIndex: vi.fn(), fetchCard: vi.fn() };
});

const board = (generatedAt: string): Board => ({
  generated_at: generatedAt,
  counts: {},
  needs_approval: [],
  running: [],
  needs_input: [],
  review: [],
  completed: [],
  debt: [],
  trash: [],
});

const INDEX_V1 = { etag: '"1-100"', snapshot: { entries: { "P-1": "Chen's H-1B draft", "P-2": "" }, truncated: false } };
const INDEX_V2 = { etag: '"2-200"', snapshot: { entries: { "P-1": "Chen's H-1B draft", "P-3": "推荐信 EB-1A" }, truncated: false } };
const ABSENT = { etag: null, snapshot: { entries: {}, truncated: false } };

/** 让在途的 fetchSearchIndex → setState 链落地（一个 macrotask 把全部 microtask 冲完；不自己再发请求） */
const settled = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  vi.mocked(fetchSearchIndex).mockReset();
  vi.mocked(fetchBoard).mockReset();
  vi.mocked(fetchBoard).mockResolvedValue(board("2026-09-06T10:00:00Z"));
  vi.mocked(fetchSearchIndex).mockResolvedValue(INDEX_V1);
});

describe("lazy load on the first non-empty search", () => {
  it("初值 null；空搜索 / 只改 chips 不拉", async () => {
    expect(getState().sessionIndex).toBeNull();
    setFilters({ tiers: ["T1"] });
    setFilters({ search: "" });
    await refreshBoard();
    expect(fetchSearchIndex).not.toHaveBeenCalled();
    expect(getState().sessionIndex).toBeNull();
  });

  it("键入第一个字 → 拉一次（无 ETag）→ 正文归一化一次落 texts；继续键入不再拉", async () => {
    setFilters({ search: "c" });
    expect(fetchSearchIndex).toHaveBeenCalledTimes(1);
    expect(fetchSearchIndex).toHaveBeenCalledWith(null);
    await settled();
    expect(getState().sessionIndex).toEqual({ etag: '"1-100"', texts: { "P-1": "chen'sh1bdraft" } }); // 空正文的 P-2 丢掉
    setFilters({ search: "ch" });
    setFilters({ search: "chen" });
    expect(fetchSearchIndex).toHaveBeenCalledTimes(1);
  });

  it("`?q=` 深链进场（initFiltersFromUrl）与后退带回搜索词（syncRouteFromUrl）同样触发", async () => {
    window.history.replaceState(null, "", "/?q=chen");
    initFiltersFromUrl();
    expect(fetchSearchIndex).toHaveBeenCalledTimes(1);
    await settled();
    resetStoreForTests();
    vi.mocked(fetchSearchIndex).mockClear();
    window.history.replaceState(null, "", "/?q=h1b");
    syncRouteFromUrl();
    expect(fetchSearchIndex).toHaveBeenCalledTimes(1);
  });
});

describe("revalidation (ETag / 304)", () => {
  it("搜索开着时每版看板落地都条件 GET（带 ETag）；304 → 缓存对象不动", async () => {
    setFilters({ search: "chen" });
    await settled();
    const cached = getState().sessionIndex;
    vi.mocked(fetchSearchIndex).mockResolvedValue(null); // 304
    await refreshBoard();
    await settled();
    expect(fetchSearchIndex).toHaveBeenLastCalledWith('"1-100"');
    expect(getState().sessionIndex).toBe(cached);
  });

  it("ETag 变了 → 200 新体替换缓存（新 ETag、新正文）", async () => {
    setFilters({ search: "chen" });
    await settled();
    vi.mocked(fetchSearchIndex).mockResolvedValue(INDEX_V2);
    await refreshBoard();
    await settled();
    expect(getState().sessionIndex).toEqual({ etag: '"2-200"', texts: { "P-1": "chen'sh1bdraft", "P-3": "推荐信eb1a" } });
  });

  it("搜索清空后看板落地不重验；再搜（空 → 非空）带着旧 ETag 重验一次", async () => {
    setFilters({ search: "chen" });
    await settled();
    setFilters({ search: "" });
    vi.mocked(fetchSearchIndex).mockClear();
    await refreshBoard();
    expect(fetchSearchIndex).not.toHaveBeenCalled();
    setFilters({ search: "eb1" });
    expect(fetchSearchIndex).toHaveBeenCalledTimes(1);
    expect(fetchSearchIndex).toHaveBeenCalledWith('"1-100"');
  });

  it("并发重验合并成一个在途请求", async () => {
    setFilters({ search: "chen" });
    const a = refreshSessionIndex();
    const b = refreshSessionIndex();
    expect(a).toBe(b);
    await a;
    expect(fetchSearchIndex).toHaveBeenCalledTimes(1);
  });
});

describe("absent layer never breaks search", () => {
  it("缺席（200 空表、无 ETag）→ 空层（etag null、texts 空）；第二次缺席不换对象", async () => {
    vi.mocked(fetchSearchIndex).mockResolvedValue(ABSENT);
    setFilters({ search: "chen" });
    await settled();
    const absent = getState().sessionIndex;
    expect(absent).toEqual({ etag: null, texts: {} });
    await refreshBoard();
    await settled();
    expect(fetchSearchIndex).toHaveBeenCalledTimes(2);
    expect(getState().sessionIndex).toBe(absent);
  });

  it("读失败（断网 / 5xx）→ 不抛、第一次落空层、之后留着旧缓存", async () => {
    vi.mocked(fetchSearchIndex).mockRejectedValue(new ApiError(0, { error: { code: "READ_FAILED", message: "offline" } }));
    setFilters({ search: "chen" });
    await expect(refreshSessionIndex()).resolves.toBeUndefined(); // 在途的那一个：失败不外抛
    await settled();
    expect(getState().sessionIndex).toEqual({ etag: null, texts: {} });

    vi.mocked(fetchSearchIndex).mockResolvedValue(INDEX_V1);
    await refreshBoard();
    await settled();
    const good = getState().sessionIndex;
    expect(good?.etag).toBe('"1-100"');
    vi.mocked(fetchSearchIndex).mockRejectedValue(new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "boom" } }));
    await refreshBoard();
    await settled();
    expect(getState().sessionIndex).toBe(good); // 失败不清好缓存
  });

  it("resetStoreForTests 回到 null 且在途请求作废", () => {
    setFilters({ search: "chen" });
    resetStoreForTests();
    expect(getState().sessionIndex).toBeNull();
  });
});
