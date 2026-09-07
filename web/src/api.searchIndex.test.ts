// api.fetchSearchIndex —— §37.2 会话内容层的条件 GET（CONTRACT §49 路由 `GET /api/search-index`，D45）：
// 带 ETag 时发 If-None-Match；304 → null（缓存不动）；200 空表无 ETag（文件缺席）→ etag null；404 → 空快照（防御：层缺席永不当错误）；200 → 快照 + server 的 ETag；
// 断网 → READ_FAILED；5xx → ApiError；坏体 → READ_FAILED。读路径 token-light：不带 X-Zai-Token。fetch 全程 stub，零真实网络。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, EMPTY_SEARCH_INDEX, fetchSearchIndex } from "./api";

const fetchMock = vi.fn();

function lastRequest(): { url: string; headers: Headers } {
  const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
  return { url, headers: new Headers(init.headers) };
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  (window as Window & { __ZAI_TOKEN__?: string }).__ZAI_TOKEN__ = "tok-123";
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as Window & { __ZAI_TOKEN__?: string }).__ZAI_TOKEN__;
});

describe("fetchSearchIndex · conditional GET", () => {
  it("200 → 快照 + ETag；没有上次的 ETag 就不发 If-None-Match；读路径不带 token", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ entries: { "P-1": "推荐信 chen" }, truncated: false }), {
      status: 200, headers: { ETag: '"10-20"', "Content-Type": "application/json" },
    }));
    const result = await fetchSearchIndex(null);
    expect(result).toEqual({ etag: '"10-20"', snapshot: { entries: { "P-1": "推荐信 chen" }, truncated: false } });
    const { url, headers } = lastRequest();
    expect(url).toMatch(/\/api\/search-index$/);
    expect(headers.has("If-None-Match")).toBe(false);
    expect(headers.has("X-Zai-Token")).toBe(false);
  });

  it("带上次的 ETag → If-None-Match 原样回带；304 → null（缓存不动）", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 304, headers: { ETag: '"10-20"' } }));
    expect(await fetchSearchIndex('"10-20"')).toBeNull();
    expect(lastRequest().headers.get("If-None-Match")).toBe('"10-20"');
  });

  it("文件缺席 = server 200 空表、无 ETag → etag null + 空快照（§49：层缺席不是错误）", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ entries: {}, truncated: false }), { status: 200, headers: { "Cache-Control": "no-store" } }));
    expect(await fetchSearchIndex('"10-20"')).toEqual({ etag: null, snapshot: { entries: {}, truncated: false } });
    expect(lastRequest().headers.get("If-None-Match")).toBe('"10-20"'); // 旧 ETag 照带；server 缺文件时忽略它
  });

  it("404（防御）→ 空快照、无 ETag（层缺席永不当错误）", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: "search_index.json not found", details: {} } }), { status: 404 }));
    expect(await fetchSearchIndex(null)).toEqual({ etag: null, snapshot: EMPTY_SEARCH_INDEX });
  });

  it("truncated 缺席 / 非 bool → false；entries 以外的键保留（add-only）", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ entries: {}, extra: 1 }), { status: 200 }));
    const result = await fetchSearchIndex(null);
    expect(result?.snapshot).toEqual({ entries: {}, extra: 1, truncated: false });
    expect(result?.etag).toBeNull();
  });

  it("断网 → ApiError READ_FAILED（status 0），不重试", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = await fetchSearchIndex(null).then(() => { throw new Error("expected reject"); }, (e) => e as ApiError);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.code).toBe("READ_FAILED");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("5xx → ApiError 带 server envelope；2xx 却不是带 entries 的对象 → READ_FAILED", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: { code: "INTERNAL_ERROR", message: "boom", details: {} } }), { status: 500 }));
    const server = await fetchSearchIndex(null).then(() => { throw new Error("expected reject"); }, (e) => e as ApiError);
    expect(server.status).toBe(500);
    expect(server.code).toBe("INTERNAL_ERROR");
    expect(server.message).toBe("boom");

    fetchMock.mockResolvedValue(new Response("[1, 2]", { status: 200 }));
    const shape = await fetchSearchIndex(null).then(() => { throw new Error("expected reject"); }, (e) => e as ApiError);
    expect(shape.code).toBe("READ_FAILED");
    expect(shape.status).toBe(200);

    fetchMock.mockResolvedValue(new Response("{not json", { status: 200 }));
    const broken = await fetchSearchIndex(null).then(() => { throw new Error("expected reject"); }, (e) => e as ApiError);
    expect(broken.code).toBe("READ_FAILED");
  });

  it("AbortError 原样上抛（调用方的取消不是读失败）", async () => {
    const abort = new DOMException("aborted", "AbortError");
    fetchMock.mockRejectedValue(abort);
    await expect(fetchSearchIndex(null)).rejects.toBe(abort);
  });
});
