// BacklogStrip 行为：出厂展开（§78 / D80.3）、点列头收起再展开、吃全局过滤器（计数 x/y）、
// 行点击开抽屉 + ?card= 深链、条头的快速捕获框（§78 从退役的提案列头搬来：无 mode 的
// `{action:"capture", text}` → `detected`；提交成功强制展开这条，回执不能落在收起的条里）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { getState, refreshBoard, resetStoreForTests, setBacklogStripExpanded, setFilters } from "../../store";
import { fetchBoard, fetchCard, postAction } from "../../api";
import type { Board } from "../../types";
import { BacklogStrip } from "./BacklogStrip";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchCard: vi.fn(), postAction: vi.fn() };
});

const CAPTURE_PLACEHOLDER = "One line — jot it down, the AI fills in the plan…";

const board = {
  generated_at: "2026-08-30T12:00:00Z",
  counts: { debt: 2 },
  needs_approval: [], running: [], needs_input: [], review: [], completed: [],
  debt: [
    { id: "R-301", title: "README 安装一节过时", type: "engineering",
      sources: [{ who: "sam", channel: "slack", date: "d", quote: "q" }] },
    { id: "R-302", title: "周会纪要没人整理", type: "process",
      sources: [{ who: "manager", channel: "meeting", date: "d", quote: "q" }] },
  ],
  trash: [],
} as unknown as Board;

beforeEach(async () => {
  window.history.replaceState(null, "", "/");
  window.localStorage.clear();
  resetStoreForTests();
  vi.mocked(fetchBoard).mockResolvedValue(board);
  vi.mocked(fetchCard).mockResolvedValue({ id: "R-302" });
  vi.mocked(postAction).mockReset().mockResolvedValue({ ok: true, file: "capture-1.json" });
  await refreshBoard();
});

afterEach(cleanup);

describe("BacklogStrip", () => {
  // §78 / D80.3：这条条是机器卡的收件箱，出厂就得开着——藏在折叠开关后面 = 雷达卡没人看见
  it("出厂展开就列出 debt 行；点列头收起只剩计数，再点回来", () => {
    render(<BacklogStrip />);
    expect(getState().backlogStripExpanded).toBe(true);
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.getByText(/README/)).toBeTruthy();
    expect(screen.getByText(/周会纪要/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Backlog/ }));
    expect(screen.queryByText(/README/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Backlog/ }));
    expect(screen.getByText(/README/)).toBeTruthy();
  });

  it("吃全局 ⌘F 搜索（D28 后 debt 行唯一适用的维度）：计数显 1/2，只剩匹配行", () => {
    render(<BacklogStrip />);
    act(() => setFilters({ search: "周会" }));

    expect(screen.getByText("1/2")).toBeTruthy();
    expect(screen.queryByText(/README/)).toBeNull();
    expect(screen.getByText(/周会纪要/)).toBeTruthy();
  });

  it("行点击 = selectCard + ?card= 深链同步", () => {
    render(<BacklogStrip />);
    act(() => {
      fireEvent.click(screen.getByText(/周会纪要/));
    });
    expect(getState().selectedCardId).toBe("R-302");
    expect(new URLSearchParams(window.location.search).get("card")).toBe("R-302");
  });
});

// §78 / §34 追记：捕获框从退役的提案列头搬到这条条的条头——意图 = 记一件事（落 detected），
// 所以 wire 形是**无 mode** 的 capture；直跑（mode:"run" → approved）仍只在运行中列头。
describe("BacklogStrip 条头的快速捕获框（§78）", () => {
  it("条头有捕获框，发的是无 mode 的 {action:capture,text}；title 仍是原生那句「快速捕获」", async () => {
    render(<BacklogStrip />);
    const field = screen.getByPlaceholderText(CAPTURE_PLACEHOLDER) as HTMLTextAreaElement;
    expect(field.getAttribute("title")).toBe("Quick capture"); // 壳不在场 = 不报键（§41 追记 (e)）
    // 列表的兄弟节点、钉在条顶：不在滚动的 .backlog-strip-list 里
    expect(field.closest(".backlog-strip-list")).toBeNull();
    expect(field.closest(".backlog-strip")).toBeTruthy();

    fireEvent.change(field, { target: { value: "记一下：周会纪要该有人写" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Capture" }));
    });
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "capture", text: "记一下：周会纪要该有人写" });
  });

  it("条收起 = 框不在 DOM 里（⌘L 的落点也随之缺席，见 focusComposer 判例）", () => {
    render(<BacklogStrip />);
    fireEvent.click(screen.getByRole("button", { name: /Backlog/ }));
    expect(screen.queryByPlaceholderText(CAPTURE_PLACEHOLDER)).toBeNull();
  });

  it("提交成功 = 强制展开这条（旗被扳成 true）：过滤器一清，回执与刚落的卡不许跟着条一起消失", async () => {
    act(() => setBacklogStripExpanded(false));
    act(() => setFilters({ search: "周会" })); // 过滤命中 → 条被强制展开，但旗还是 false
    render(<BacklogStrip />);
    const field = screen.getByPlaceholderText(CAPTURE_PLACEHOLDER);
    fireEvent.change(field, { target: { value: "再记一件事" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Capture" }));
    });
    expect(getState().backlogStripExpanded).toBe(true);
  });

  it("POST 被拒：不扳旗（没有回执要落进条里），草稿原样留着", async () => {
    vi.mocked(postAction).mockRejectedValue(new Error("boom"));
    act(() => setBacklogStripExpanded(false));
    act(() => setFilters({ search: "周会" }));
    render(<BacklogStrip />);
    const field = screen.getByPlaceholderText(CAPTURE_PLACEHOLDER) as HTMLTextAreaElement;
    fireEvent.change(field, { target: { value: "写不进去的一句" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Capture" }));
    });
    expect(getState().backlogStripExpanded).toBe(false);
    expect(field.value).toBe("写不进去的一句");
  });
});
