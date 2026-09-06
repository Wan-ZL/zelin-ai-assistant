// fetchSlackDirectory 的 URL 形（CONTRACT §68.1 2026-09-05 追记）：?refresh=1 与 ?lang=zh|en 都是 add-only query；
// 不给 lang 不发该键（老 server 零改动）、不给 refresh 不发；两者同给按 refresh、lang 顺序拼。
// 语言本身由调用方（SlackDirectoryPicker 的 useI18n().language）决定——本模块不 import store、不猜语言。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSlackDirectory, resolveApiUrl } from "./api";

const fetchMock = vi.fn();

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(jsonResponse({ ok: true, channels: [], users: [] }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function calledUrl(): string {
  return String(fetchMock.mock.calls[0][0]);
}

describe("fetchSlackDirectory query shape", () => {
  it("no args → bare path (old behaviour, nothing injected)", async () => {
    await fetchSlackDirectory();
    expect(calledUrl()).toBe(resolveApiUrl("/api/slack/directory"));
  });

  it("refresh only → ?refresh=1", async () => {
    await fetchSlackDirectory(true);
    expect(calledUrl()).toBe(resolveApiUrl("/api/slack/directory?refresh=1"));
  });

  it("lang only → ?lang=zh", async () => {
    await fetchSlackDirectory(false, "zh");
    expect(calledUrl()).toBe(resolveApiUrl("/api/slack/directory?lang=zh"));
  });

  it("both → ?refresh=1&lang=en", async () => {
    await fetchSlackDirectory(true, "en");
    expect(calledUrl()).toBe(resolveApiUrl("/api/slack/directory?refresh=1&lang=en"));
  });

  it("the JSON row is returned verbatim", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: false, error: "no_token", message: "先粘贴并保存 token", channels: [], users: [] }));
    const dir = await fetchSlackDirectory(false, "zh");
    expect(dir).toEqual({ ok: false, error: "no_token", message: "先粘贴并保存 token", channels: [], users: [] });
  });
});
