// App 级判例（D40 × D42，CONTRACT §54.4 2026-09-06 追记）：D42 起文档不滚——非看板页在 `<main class="shell-main">` 里滚、
// 看板页每列的 `.column-list` 各自滚；D40 的换页滚动记忆因此挂在这两种容器上（data-scroll-memory="shell-main" / "lane:<slug>"）：
//   · 设置页滚到 400 → 去关于页：.shell-main 归零（跨页常驻的 <main> 不把设置页的位置带进第一次到的页）→ 回设置页：还原 400；
//   · 看板提案列滚到 120 → 去关于页 → 回看板：这一列还原 120（列重新挂载，靠记忆不靠 DOM 残留）。
// 与 app.clientRouting.test.tsx 同一套 mock（api offline、realtime 计数、壳桥假 messageHandler）。
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "./api";
import { App } from "./app";
import { createBoardRealtime } from "./realtime";
import { resetRouterForTests } from "./route";
import { resetShellBridgeForTests } from "./shellBridge";
import { resetStoreForTests } from "./store";

const { BOARD, realtimeStart, realtimeStop } = vi.hoisted(() => ({
  BOARD: {
    generated_at: "2026-09-06T12:00:00Z",
    needs_approval: [
      { id: "P-201", title: "a proposal", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] },
    ],
    running: [], needs_input: [], review: [], completed: [], debt: [], trash: [],
    counts: { needs_approval: 1 },
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
const main = () => document.querySelector<HTMLElement>("main.shell-main")!;

function go(slug: string) {
  act(() => {
    fireEvent.click(railLink(slug));
  });
}

async function renderBoard() {
  render(<App />);
  await waitFor(() => expect(document.querySelector(".board-column")).toBeTruthy());
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.sessionStorage.setItem("zai.launched", "1");
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  resetRouterForTests();
  resetShellBridgeForTests();
  vi.mocked(fetchBoard).mockClear();
  vi.mocked(createBoardRealtime).mockClear();
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

describe("App — 换页滚动记忆挂在 .shell-main 与每列的 .column-list 上（D40 × D42）", () => {
  it("<main class=shell-main> 与每列的 .column-list 都带 data-scroll-memory（键：shell-main / lane:<slug>）", async () => {
    await renderBoard();
    expect(main().dataset.scrollMemory).toBe("shell-main");
    const keys = Array.from(document.querySelectorAll<HTMLElement>(".column-list")).map((el) => el.dataset.scrollMemory);
    expect(keys).toEqual(["lane:needs_approval", "lane:running", "lane:review", "lane:completed"]);
  });

  it("设置页滚到 400 → 关于页的 .shell-main 从 0 开始（不带上一页的位置）→ 回设置页还原 400", async () => {
    await renderBoard();
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

  it("看板提案列滚到 120 → 去关于页 → 回看板：这一列还原 120，其余列在 0", async () => {
    await renderBoard();
    document.querySelector<HTMLElement>('.column-list[data-scroll-memory="lane:needs_approval"]')!.scrollTop = 120;
    go("about");
    await waitFor(() => expect(document.title).toMatch(/— (关于|About)$/));
    expect(document.querySelector(".column-list")).toBeNull(); // 看板卸载了：回来靠记忆
    go("dashboard");
    await waitFor(() => expect(document.querySelector(".board-column")).toBeTruthy());
    const lists = Array.from(document.querySelectorAll<HTMLElement>(".column-list"));
    expect(lists.map((el) => el.scrollTop)).toEqual([120, 0, 0, 0]);
  });
});
