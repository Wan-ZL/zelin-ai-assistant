// App 级判例（D40 × D42，CONTRACT §54.4 2026-09-06 追记）：D42 起文档不滚——页面在 `<main class="shell-main">` 里滚；
// D40 的换页滚动记忆因此挂在这个容器上（data-scroll-memory="shell-main"；v2.0.0 看板每列的 lane:<slug> 随看板退役）：
//   · 设置页滚到 400 → 去关于页：.shell-main 归零（跨页常驻的 <main> 不把设置页的位置带进第一次到的页）→ 回设置页：还原 400；
//   · 会议纪要页滚到 120 → 去关于页 → 回会议纪要：还原 120（页面重新挂载，靠记忆不靠 DOM 残留）。
// 与 app.clientRouting.test.tsx 同一套 mock（api offline、realtime 计数、壳桥假 messageHandler）。
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchRecaps } from "./api";
import { App } from "./app";
import { createRecapsRealtime } from "./realtime";
import { resetRouterForTests } from "./route";
import { resetShellBridgeForTests } from "./shellBridge";
import { resetStoreForTests } from "./store";

const { SNAPSHOT, realtimeStart, realtimeStop } = vi.hoisted(() => ({
  SNAPSHOT: { recaps: [] },
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
  return { ...actual, ...offline, fetchRecaps: vi.fn().mockResolvedValue(SNAPSHOT) };
});

vi.mock("./realtime", () => ({
  createRecapsRealtime: vi.fn(() => ({ start: realtimeStart, stop: realtimeStop })),
}));

const railLink = (slug: string) => document.querySelector<HTMLAnchorElement>(`[data-rail-item="${slug}"]`)!;
const main = () => document.querySelector<HTMLElement>("main.shell-main")!;

function go(slug: string) {
  act(() => {
    fireEvent.click(railLink(slug));
  });
}

async function renderRecaps() {
  render(<App />);
  await waitFor(() => expect(document.querySelector(".recaps-page")).toBeTruthy());
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.sessionStorage.setItem("zai.launched", "1");
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  resetRouterForTests();
  resetShellBridgeForTests();
  vi.mocked(fetchRecaps).mockClear();
  vi.mocked(createRecapsRealtime).mockClear();
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  Element.prototype.scrollIntoView = vi.fn();
  window.webkit = { messageHandlers: { zaiShell: { postMessage: vi.fn().mockResolvedValue({}) } } };
});

afterEach(() => {
  cleanup();
  delete window.webkit;
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

describe("App — 换页滚动记忆挂在 .shell-main 上（D40 × D42）", () => {
  it("<main class=shell-main> 带 data-scroll-memory=shell-main", async () => {
    await renderRecaps();
    expect(main().dataset.scrollMemory).toBe("shell-main");
  });

  it("设置页滚到 400 → 关于页的 .shell-main 从 0 开始（不带上一页的位置）→ 回设置页还原 400", async () => {
    await renderRecaps();
    go("settings");
    await waitFor(() => expect(document.title).toMatch(/— (设置|Settings)$/));
    main().scrollTop = 400; // jsdom 不布局：scrollTop 只是个属性，rememberScroll 读它
    go("about");
    await waitFor(() => expect(document.title).toMatch(/— (关于|About)$/));
    expect(main().scrollTop).toBe(0);
    go("settings");
    await waitFor(() => expect(document.title).toMatch(/— (设置|Settings)$/));
    expect(main().scrollTop).toBe(400);
  });

  it("会议纪要页滚到 120 → 去关于页 → 回会议纪要：还原 120", async () => {
    await renderRecaps();
    main().scrollTop = 120;
    go("about");
    await waitFor(() => expect(document.title).toMatch(/— (关于|About)$/));
    expect(document.querySelector(".recaps-page")).toBeNull(); // 页面卸载了：回来靠记忆
    go("recaps");
    await waitFor(() => expect(document.querySelector(".recaps-page")).toBeTruthy());
    expect(main().scrollTop).toBe(120);
  });
});
