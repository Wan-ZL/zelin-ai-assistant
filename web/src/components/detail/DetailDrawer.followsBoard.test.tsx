// 侧栏开着时看板换版 → 侧栏跟上（CONTRACT §49 追记 `store-resilience-drawer`；原生 Store.swift:56-57 @Published dashboard）：
// 改名回流后「✎ 改名」的预填是新名字（此前冻结在打开那一刻，第二次改名还拿旧名比对）；换列（approve → running）后
// DetailFields 按新 lane 选积木（提案的「💰」块退场）；重拉在途不闪「加载详情…」。fetch 全程 stub——按调用次序回不同版本。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DetailDrawer } from "./DetailDrawer";
import { getState, refreshBoard, resetStoreForTests, selectCard } from "../../store";

const V1 = { id: "R-101", title: "给 example-bench 加导出", display_title: "给 example-bench 加导出", lane: "needs_approval", plan: ["step A"] };
const V2 = { ...V1, display_title: "导出功能", user_titled: true, former_titles: ["给 example-bench 加导出"], lane: "running", plan: ["step A", "step B"] };

function board(generated_at: string) {
  return { generated_at, counts: {}, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [] };
}

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

let cardVersions: unknown[];
let boardVersion: ReturnType<typeof board>;

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  cardVersions = [V1, V2];
  boardVersion = board("2026-09-05T10:00:00Z");
  vi.stubGlobal("fetch", vi.fn(async (url: unknown) => {
    const u = String(url);
    if (u.includes("/api/cards/")) return jsonResponse(cardVersions.length > 1 ? cardVersions.shift() : cardVersions[0]);
    if (u.includes("/api/board")) return jsonResponse(boardVersion);
    return jsonResponse({ ok: true });
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("DetailDrawer follows board versions", () => {
  it("rename prefill, lane blocks and former names refresh when a new generated_at lands; no loading flicker", async () => {
    await refreshBoard();
    render(<DetailDrawer />);
    act(() => selectCard("R-101"));
    await screen.findByText("step A");
    expect(screen.queryByText("step B")).toBeNull();
    expect(screen.getByText("💰 Cost unknown")).toBeTruthy(); // 提案列积木：§40 永远说钱

    // 看板换版（actd 一个 pass：改名 + 批准落地）
    boardVersion = board("2026-09-05T10:01:00Z");
    await act(async () => { await refreshBoard(); });
    await screen.findByText("step B");
    expect(screen.queryByText("Loading detail…")).toBeNull(); // 中途没闪过占位
    expect(screen.queryByText("💰 Cost unknown")).toBeNull();   // running 列不渲染提案块
    expect(screen.getByText("给 example-bench 加导出", { selector: ".zai-former-names span" })).toBeTruthy(); // 曾用名

    // 改名框预填 = 此刻的卡面标题（新名字），不是打开时冻结的旧名
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect((screen.getByRole("textbox", { name: "New title" }) as HTMLInputElement).value).toBe("导出功能");
    expect(getState().cardDetail).toEqual(V2);
  });

  it("a same-version refetch leaves the drawer untouched (no extra card request)", async () => {
    await refreshBoard();
    render(<DetailDrawer />);
    act(() => selectCard("R-101"));
    await screen.findByText("step A");
    const calls = () => (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter((c) => String(c[0]).includes("/api/cards/")).length;
    expect(calls()).toBe(1);
    await act(async () => { await refreshBoard(); });
    expect(calls()).toBe(1);
    expect(screen.queryByText("step B")).toBeNull();
  });
});
