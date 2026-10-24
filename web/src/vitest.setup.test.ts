// vitest.setup —— 防腐 #7 的 web 半边：判例环境里的 globalThis.fetch 是不开 socket 的拒绝桩（vite.config.ts `test.setupFiles`）。
//   · 没 mock 住的 api.ts 调用立刻以 TypeError 拒绝（与 Node 真 fetch 断网时的 `TypeError: fetch failed` 同型），消息点名 URL 与补救办法；
//   · `vi.stubGlobal("fetch", …)` 照常盖过本桩、`vi.unstubAllGlobals()` 还原回来的仍是本桩（不是 Node 的真 fetch）；
//   · store 的读路径（refreshRecaps）在 api 未 mock 时走到的就是这条路：READ_FAILED → 一句错误、旧行留着，不碰网卡、不抛。
// 本文件故意不 vi.mock("./api")——它钉的正是「没 mock 时会怎样」。
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchRecaps } from "./api";
import { getState, refreshRecaps, resetStoreForTests } from "./store";

const settled = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  vi.unstubAllGlobals();
  resetStoreForTests();
});

describe("globalThis.fetch is a no-network stub under vitest", () => {
  it("un-mocked fetch rejects at once with a TypeError naming the URL — never Node's real `fetch failed`", async () => {
    const attempt = globalThis.fetch("http://localhost:3000/api/recaps");
    await expect(attempt).rejects.toBeInstanceOf(TypeError);
    await expect(attempt).rejects.toThrow(/fetch blocked in vitest \(no network in unit tests\): http:\/\/localhost:3000\/api\/recaps/);
    await expect(attempt).rejects.toThrow(/vi\.mock/);
  });

  it("describes string / URL / Request inputs alike", async () => {
    await expect(globalThis.fetch(new URL("http://localhost:3000/api/health"))).rejects.toThrow(/\/api\/health/);
    await expect(globalThis.fetch(new Request("http://localhost:3000/api/about"))).rejects.toThrow(/\/api\/about/);
  });

  it("vi.stubGlobal overrides the stub for a test and unstubAllGlobals restores the stub, not the real fetch", async () => {
    const stubbed = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    expect((await globalThis.fetch("/api/recaps")).status).toBe(200);
    vi.unstubAllGlobals();
    expect(globalThis.fetch).toBe(stubbed);
    await expect(globalThis.fetch("/api/recaps")).rejects.toThrow(/fetch blocked in vitest/);
  });
});

describe("store reads with an un-mocked api never reach the network", () => {
  it("api.fetchRecaps → READ_FAILED without a socket (GET retries are backoff timers, still no socket)", async () => {
    await expect(fetchRecaps()).rejects.toMatchObject({ code: "READ_FAILED", details: { failure: "browser-network" } });
  });

  it("refreshRecaps lands an error line, keeps the rows null, throws nothing", async () => {
    resetStoreForTests();
    await refreshRecaps();
    await settled();
    expect(getState().recaps).toBeNull();
    expect(getState().recapsError).toMatch(/temporarily unavailable/);
    expect(getState().recapsLoading).toBe(false);
  });
});
