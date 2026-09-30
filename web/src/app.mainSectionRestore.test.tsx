// App 级判例：mainSection（CONTRACT §66.2 setting:prefs:mainSection；原生 MainNav.section didSet / MainNav.init 的 UserDefaults
// 同名键）走真实的启动路——app.tsx 的换页副作用（restoreMainSection → navigate(replace) / rememberMainSection）+ NavRail 的 rail 链接。
// NavRail.test.tsx 与 parity.test.tsx 钉的是两个纯函数；app.clientRouting.test.tsx 特意在 sessionStorage 打上 zai.launched 让它不回跳。
// 这里钉「从 App 挂载读回这把键」：
//   1) 冷启动（本窗口会话没启动过）+ URL 没指定去处 + 键里是 trash → 落到回收站页，URL 变 ?page=trash 且 replaceState 不进历史栈，
//      本窗口会话打上 zai.launched，键保持 trash；
//   2) URL 指定了去处（?page=about）→ 尊重 URL 落到关于页，并把 about 写进键（覆盖上次的 trash）；
//   3) 同一窗口会话第二次整页加载（zai.launched 已在）不回跳：留在看板并把 dashboard 写进键（原生 didSet 语义）；
//   4) 键里是退役 / 未知的值（ask / deps / nowhere / 空串）→ 不跳、不崩，落到看板后键被改写成 dashboard；
//   5) 点 rail 链接换页写键：设置 → settings；web 自有页（会议纪要 recaps）不写——原生 UserDefaults 没有这个值；
//      下一次冷启动回到键里的设置页。
// api 全 mock（fetchBoard 回最小快照，其余 offline）、realtime mock——同 app.clientRouting.test.tsx。
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./app";
import { resetRouterForTests } from "./route";
import { resetShellBridgeForTests } from "./shellBridge";
import { resetStoreForTests } from "./store";

const { BOARD, realtimeStart, realtimeStop } = vi.hoisted(() => ({
  BOARD: {
    generated_at: "2026-09-17T00:00:00Z",
    needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [], counts: {},
  },
  realtimeStart: vi.fn(),
  realtimeStop: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  const offline: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(actual)) {
    if (typeof value === "function" && /^(fetch|post|put|verify)/.test(name)) {
      offline[name] = vi.fn().mockRejectedValue(new Error("offline"));
    }
  }
  return { ...actual, ...offline, fetchBoard: vi.fn().mockResolvedValue(BOARD) };
});

vi.mock("./realtime", () => ({
  createBoardRealtime: vi.fn(() => ({ start: realtimeStart, stop: realtimeStop })),
}));

const railLink = (slug: string) => document.querySelector<HTMLAnchorElement>(`[data-rail-item="${slug}"]`)!;
const railExtra = (slug: string) => document.querySelector<HTMLAnchorElement>(`[data-rail-extra="${slug}"]`)!;
const section = () => window.localStorage.getItem("mainSection");

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear(); // 冷启动：没有 zai.launched（app.clientRouting.test.tsx 反过来特意打上它）
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  resetRouterForTests();
  resetShellBridgeForTests();
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

describe("App — mainSection 冷启动读回（原生 MainNav.init）", () => {
  it("URL 没指定去处 + 键里是 trash → 落到回收站页；replaceState 不进历史栈；本窗口会话标记已启动；键保持 trash", async () => {
    window.localStorage.setItem("mainSection", "trash");
    const depth = window.history.length;
    render(<App />);
    await waitFor(() => expect(document.querySelector(".trash-page")).toBeTruthy());
    expect(window.location.search).toBe("?page=trash");
    expect(document.title).toMatch(/— (回收站|Trash)$/);
    expect(document.querySelector(".board-column")).toBeNull();
    expect(window.history.length).toBe(depth);
    expect(window.sessionStorage.getItem("zai.launched")).toBe("1");
    expect(section()).toBe("trash");
  });

  it("URL 指定了去处（?page=about）→ 尊重 URL 落到关于页，键改写成 about", async () => {
    window.localStorage.setItem("mainSection", "trash");
    window.history.replaceState(null, "", "/?page=about");
    render(<App />);
    await waitFor(() => expect(document.title).toMatch(/— (关于|About)$/));
    expect(window.location.search).toBe("?page=about");
    expect(document.querySelector(".trash-page")).toBeNull();
    await waitFor(() => expect(section()).toBe("about"));
  });

  it("同一窗口会话第二次整页加载（zai.launched 已在）不回跳：留在看板，键改写成 dashboard", async () => {
    window.sessionStorage.setItem("zai.launched", "1");
    window.localStorage.setItem("mainSection", "trash");
    render(<App />);
    await waitFor(() => expect(document.querySelector(".board-column")).toBeTruthy());
    expect(window.location.search).toBe("");
    await waitFor(() => expect(section()).toBe("dashboard"));
  });

  it.each(["ask", "deps", "nowhere", ""])("键里是退役 / 未知的值 %j → 不跳、不崩，落到看板后键改写成 dashboard", async (stale) => {
    window.localStorage.setItem("mainSection", stale);
    render(<App />);
    await waitFor(() => expect(document.querySelector(".board-column")).toBeTruthy());
    expect(window.location.search).toBe("");
    expect(window.sessionStorage.getItem("zai.launched")).toBe("1");
    await waitFor(() => expect(section()).toBe("dashboard"));
  });
});

describe("App — 换页写键（原生 MainNav.section didSet）", () => {
  it("rail 点设置 → mainSection=settings；点 web 自有页（会议纪要）不写；下一次冷启动回到设置页", async () => {
    window.sessionStorage.setItem("zai.launched", "1");
    render(<App />);
    await waitFor(() => expect(document.querySelector(".board-column")).toBeTruthy());
    await waitFor(() => expect(section()).toBe("dashboard"));
    fireEvent.click(railLink("settings"));
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    expect(window.location.search).toBe("?page=settings");
    expect(section()).toBe("settings");
    fireEvent.click(railExtra("recaps"));
    await waitFor(() => expect(window.location.search).toBe("?page=recaps"));
    expect(section()).toBe("settings"); // 原生 UserDefaults 没有 recaps 这个值：不写
    cleanup();
    // 下一次冷启动（新窗口会话、URL 没指定去处）：回到键里的设置页
    window.sessionStorage.clear();
    window.history.replaceState(null, "", "/");
    resetStoreForTests();
    resetRouterForTests();
    render(<App />);
    await waitFor(() => expect(document.querySelector(".settings-page")).toBeTruthy());
    expect(window.location.search).toBe("?page=settings");
    expect(section()).toBe("settings");
  });
});
