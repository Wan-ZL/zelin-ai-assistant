// vitest.setup —— 防腐 #7 的 web 半边：判例环境里的 globalThis.fetch 是不开 socket 的拒绝桩（vite.config.ts `test.setupFiles`）。
//   · 没 mock 住的 api.ts 调用立刻以 TypeError 拒绝（与 Node 真 fetch 断网时的 `TypeError: fetch failed` 同型），消息点名 URL 与补救办法；
//   · `vi.stubGlobal("fetch", …)` 照常盖过本桩、`vi.unstubAllGlobals()` 还原回来的仍是本桩（不是 Node 的真 fetch）；
//   · §37.2 会话层的懒加载（D45）在 api 未 mock 时走到的就是这条路：READ_FAILED → 空层，不碰网卡、不抛。
// 本文件故意不 vi.mock("./api")——它钉的正是「没 mock 时会怎样」。
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchSearchIndex } from "./api";
import { getState, resetStoreForTests, setFilters } from "./store";

const settled = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  vi.unstubAllGlobals();
  resetStoreForTests();
});

describe("globalThis.fetch is a no-network stub under vitest", () => {
  it("un-mocked fetch rejects at once with a TypeError naming the URL — never Node's real `fetch failed`", async () => {
    const attempt = globalThis.fetch("http://localhost:3000/api/board");
    await expect(attempt).rejects.toBeInstanceOf(TypeError);
    await expect(attempt).rejects.toThrow(/fetch blocked in vitest \(no network in unit tests\): http:\/\/localhost:3000\/api\/board/);
    await expect(attempt).rejects.toThrow(/vi\.mock/);
  });

  it("describes string / URL / Request inputs alike", async () => {
    await expect(globalThis.fetch(new URL("http://localhost:3000/api/radars"))).rejects.toThrow(/\/api\/radars/);
    await expect(globalThis.fetch(new Request("http://localhost:3000/api/skills"))).rejects.toThrow(/\/api\/skills/);
  });

  it("vi.stubGlobal overrides the stub for a test and unstubAllGlobals restores the stub, not the real fetch", async () => {
    const stubbed = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    expect((await globalThis.fetch("/api/board")).status).toBe(200);
    vi.unstubAllGlobals();
    expect(globalThis.fetch).toBe(stubbed);
    await expect(globalThis.fetch("/api/board")).rejects.toThrow(/fetch blocked in vitest/);
  });
});

describe("D45 lazy load with an un-mocked api never reaches the network", () => {
  it("api.fetchSearchIndex → READ_FAILED without a socket", async () => {
    await expect(fetchSearchIndex(null)).rejects.toMatchObject({ code: "READ_FAILED", details: { failure: "network" } });
  });

  it("setFilters({ search }) lands the absent layer, nothing thrown", async () => {
    resetStoreForTests();
    setFilters({ search: "eb1" });
    await settled();
    expect(getState().sessionIndex).toEqual({ etag: null, texts: {} });
    expect(getState().filters.search).toBe("eb1");
  });
});
