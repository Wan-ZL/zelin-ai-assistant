// store.syncRouteFromUrl（D40 客户端路由，CONTRACT §49 / §54.4 2026-09-06 追记）：navigate / popstate 之后，URL 里不进历史栈的
// 两样东西跟回 store——过滤器（URL 是唯一持久化）与 ?card= 抽屉（后退回到开着抽屉的那一版就重开；换页链接不带 card → 关）。
// 幂等：同一 URL 再同步一次不 setState、不重拉详情。页本身不进 AppState（组件经 route.useRoute 读 URL）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchCard } from "./api";
import { getState, resetStoreForTests, setFilters, subscribe, syncRouteFromUrl } from "./store";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchCard: vi.fn().mockResolvedValue({ id: "R-1", lane: "running" }) };
});

beforeEach(() => {
  resetStoreForTests();
  window.history.replaceState(null, "", "/");
  vi.mocked(fetchCard).mockClear();
});

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("store.syncRouteFromUrl", () => {
  it("过滤器跟 URL：后退带回 ?q= / tier= / deadline= / reraised=；没有 = 清空", () => {
    window.history.replaceState(null, "", "/?page=settings&q=foo&tier=T1,T2&deadline=soon&reraised=1");
    syncRouteFromUrl();
    expect(getState().filters).toEqual({ tiers: ["T1", "T2"], deadline: "soon", reraisedOnly: true, search: "foo" });
    window.history.replaceState(null, "", "/?page=trash");
    syncRouteFromUrl();
    expect(getState().filters).toEqual({ tiers: [], deadline: "all", reraisedOnly: false, search: "" });
  });

  it("?card= 跟 URL：有 → 选中并拉详情；换页链接不带 card → 关抽屉；同一张不重拉", () => {
    window.history.replaceState(null, "", "/?card=R-1");
    syncRouteFromUrl();
    expect(getState().selectedCardId).toBe("R-1");
    expect(fetchCard).toHaveBeenCalledTimes(1);
    syncRouteFromUrl();
    expect(fetchCard).toHaveBeenCalledTimes(1); // 幂等
    window.history.replaceState(null, "", "/?page=settings");
    syncRouteFromUrl();
    expect(getState().selectedCardId).toBeNull();
    expect(getState().cardDetail).toBeNull();
  });

  it("URL 没变化时不 setState（订阅者不被叫醒）", () => {
    window.history.replaceState(null, "", "/?q=foo");
    setFilters({ search: "foo" });
    const listener = vi.fn();
    const stop = subscribe(listener);
    syncRouteFromUrl();
    expect(listener).not.toHaveBeenCalled();
    stop();
  });
});
