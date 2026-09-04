// FilterBar 行为：chip 只剩 Tier / 期限 / 回锅 + 搜索（D28：类型 / 渠道退役）、chip 多选写进 store + URL（?tier=）、
// ⌘F 聚焦搜索、清除按钮复位、⎋ 两段（清词 → 退出多选，原生 契约七）。
// 顶栏三档密度（§49 追记 2026-09-04）：compact 把 chips / 排序 / 清除 / 选择 收进「筛选 · N」popover（role=dialog，
// 反映并改写同一份 store / URL 状态，⎋ 关面板不碰搜索词，焦点还给按钮）；tight 搜索折成放大镜（点击 / ⌘F 展开，
// ⎋ 两段 = 清词 → 收起，有词带点）、「筛选」「提建议」只留图标但 aria-label = 全档的可见文案。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HeaderDensityContext, type HeaderDensity } from "../shell/headerDensity";
import { getState, refreshBoard, resetStoreForTests, setFilters, setSelectionMode } from "../../store";
import { fetchBoard } from "../../api";
import type { Board } from "../../types";
import { FilterBar } from "./FilterBar";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn() };
});

// 带 type 与 sources[].channel 的看板——退役前会让「类型」「渠道」两颗 chip 长出来
const boardWithTypesAndChannels = {
  generated_at: "2026-09-04T00:00:00Z",
  counts: {},
  needs_approval: [{ id: "P-1", title: "t", tier: "T1", processing: false, show_cost: false,
    sources: [{ who: "a", channel: "slack", date: "d", quote: "q" }], plan: [], dod: [] }],
  running: [], needs_input: [], review: [], completed: [],
  debt: [{ id: "P-3", title: "t", type: "process",
    sources: [{ who: "c", channel: "meeting", date: "d", quote: "q" }] }],
  trash: [],
} as unknown as Board;

function renderAt(density: HeaderDensity) {
  return render(
    <HeaderDensityContext.Provider value={density}>
      <FilterBar />
    </HeaderDensityContext.Provider>,
  );
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
});

afterEach(cleanup);

describe("FilterBar", () => {
  it("D28：chip 恰好三颗 Tier / 期限 / 回锅 + 搜索框——看板带 type / channel 也不长出类型、渠道 chip", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(boardWithTypesAndChannels);
    await act(async () => { await refreshBoard(); });
    render(<FilterBar />);

    expect(screen.getByRole("searchbox", { name: "Search cards" })).toBeTruthy();
    const chips = Array.from(document.querySelectorAll<HTMLElement>(".chrome-chip"));
    expect(chips.map((c) => c.textContent?.trim())).toEqual(["Tier", "Deadline", "↩︎ Re-raised"]);
    expect(screen.getByRole("button", { name: "Filter by tier" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Filter by deadline" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Filter by type/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Filter by channel/ })).toBeNull();
  });

  it("tier chip 多选：toggle 进 store 并序列化进 URL；再点取消", () => {
    render(<FilterBar />);

    fireEvent.click(screen.getByRole("button", { name: "Filter by tier" }));
    fireEvent.click(screen.getByRole("option", { name: /T2/ }));

    expect(getState().filters.tiers).toEqual(["T2"]);
    expect(new URLSearchParams(window.location.search).get("tier")).toBe("T2");
    // 多选弹层保持打开，二次点击取消勾选
    fireEvent.click(screen.getByRole("option", { name: /T2/ }));
    expect(getState().filters.tiers).toEqual([]);
    expect(new URLSearchParams(window.location.search).get("tier")).toBeNull();
  });

  it("挂载时从 URL 水合深链过滤器；旧书签的 type= / channel= 被忽略、首次写回即丢弃", () => {
    window.history.replaceState(null, "", "/?tier=T1&q=readme&reraised=1&type=engineering&channel=slack");
    render(<FilterBar />);
    const { filters } = getState();
    expect(filters).toEqual({ tiers: ["T1"], deadline: "all", reraisedOnly: true, search: "readme" });
    expect(screen.getByRole("button", { name: "Filter by tier" }).textContent).toContain("T1");

    fireEvent.click(screen.getByRole("button", { name: "Filter by tier" }));
    fireEvent.click(screen.getByRole("option", { name: /T2/ }));
    const params = new URLSearchParams(window.location.search);
    expect(params.get("tier")).toBe("T1,T2");
    expect(params.has("type")).toBe(false);
    expect(params.has("channel")).toBe(false);
  });

  it("⎋ 两段：有搜索词先清词、再按退出多选（原生 Kanban.swift:98 契约七）", () => {
    render(<FilterBar />);
    act(() => { setFilters({ search: "readme" }); setSelectionMode(true); });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(getState().filters.search).toBe("");
    expect(getState().selectionMode).toBe(true);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(getState().selectionMode).toBe(false);
  });

  it("⌘F 聚焦搜索框；输入写 store + URL q=", () => {
    render(<FilterBar />);
    const input = screen.getByRole("searchbox", { name: "Search cards" });

    fireEvent.keyDown(window, { key: "f", metaKey: true });
    expect(document.activeElement).toBe(input);

    fireEvent.change(input, { target: { value: "lint" } });
    expect(getState().filters.search).toBe("lint");
    expect(new URLSearchParams(window.location.search).get("q")).toBe("lint");
  });

  it("清除按钮复位全部维度并清 URL", () => {
    window.history.replaceState(null, "", "/?tier=T1&deadline=soon");
    render(<FilterBar />);
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /Clear \(2\)/ }));
    });
    expect(getState().filters.tiers).toEqual([]);
    expect(getState().filters.deadline).toBe("all");
    expect(window.location.search).toBe("");
  });
});

describe("FilterBar · compact（「筛选」popover）", () => {
  it("chips / 排序 / 选择 不在条上，只剩 搜索框 + 筛选 + 提建议；角标 = 面板内生效维度数（不数搜索词）", () => {
    window.history.replaceState(null, "", "/?tier=T1&deadline=soon&q=readme");
    renderAt("compact");
    expect(screen.getByRole("searchbox", { name: "Search cards" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Filter by tier" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Card sorting" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Select" })).toBeNull();
    const trigger = screen.getByRole("button", { name: "Filters" });
    expect(trigger.getAttribute("aria-haspopup")).toBe("dialog");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.textContent).toBe("Filters· 2");
    expect(trigger.getAttribute("title")).toBe("Filters · 2");
    expect(trigger.className).toContain("is-active");
    expect(screen.getByRole("button", { name: "Send feedback" }).textContent).toBe("Send feedback");
  });

  it("点开 = role=dialog 面板：反映当前选择、改写同一份 store + URL；清除关面板并复位", () => {
    window.history.replaceState(null, "", "/?tier=T1&deadline=soon");
    renderAt("compact");
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    const dialog = screen.getByRole("dialog", { name: "Filters" });
    expect(screen.getByRole("button", { name: "Filters" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: "Filter by tier" }).textContent).toContain("T1");
    expect(screen.getByRole("button", { name: "Filter by deadline" }).textContent).toContain("Due within 7 days");
    expect(dialog.querySelector("select.chrome-sort-select")).toBeTruthy();
    expect(dialog.contains(screen.getByRole("button", { name: "Select" }))).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Filter by tier" }));
    fireEvent.click(screen.getByRole("option", { name: /T2/ }));
    expect(getState().filters.tiers).toEqual(["T1", "T2"]);
    expect(new URLSearchParams(window.location.search).get("tier")).toBe("T1,T2");
    // 子弹层 portal 进面板本体（点它不算「点外面」）
    expect(dialog.contains(screen.getByRole("listbox"))).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /Clear \(2\)/ }));
    expect(getState().filters.tiers).toEqual([]);
    expect(getState().filters.deadline).toBe("all");
    expect(window.location.search).toBe("");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Filters" }).textContent).toBe("Filters");
  });

  it("⎋ 关面板、不清搜索词、焦点回到「筛选」；点面板外也关", () => {
    renderAt("compact");
    act(() => { setFilters({ search: "readme" }); });
    const trigger = screen.getByRole("button", { name: "Filters" });
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Filters" });
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(getState().filters.search).toBe("readme");
    expect(document.activeElement).toBe(trigger);

    fireEvent.click(trigger);
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("面板里的「选择」切进多选并关面板；⌘F 也关面板并聚焦搜索框", () => {
    renderAt("compact");
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    fireEvent.click(screen.getByRole("button", { name: "Select" }));
    expect(getState().selectionMode).toBe(true);
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    fireEvent.keyDown(window, { key: "f", metaKey: true });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("searchbox", { name: "Search cards" }));
  });
});

describe("FilterBar · tight（搜索折成放大镜）", () => {
  it("默认只有放大镜（aria-label = 搜索框的名字，title 带 ⌘F）；有词时带点；「筛选」「提建议」只留图标但名字不变", () => {
    window.history.replaceState(null, "", "/?tier=T1&q=readme");
    renderAt("tight");
    expect(screen.queryByRole("searchbox")).toBeNull();
    const toggle = screen.getByRole("button", { name: "Search cards" });
    expect(toggle.getAttribute("title")).toBe("Search ⌘F");
    expect(toggle.querySelector(".chrome-search-dot")).toBeTruthy();
    const filters = screen.getByRole("button", { name: "Filters" });
    expect(filters.className).toContain("is-icon");
    expect(filters.textContent).toBe("1"); // 只剩角标数字，「Filters」在 aria-label / title
    expect(filters.getAttribute("title")).toBe("Filters · 1");
    const feedback = screen.getByRole("button", { name: "Send feedback" });
    expect(feedback.className).toContain("chrome-icon-button");
    expect(feedback.textContent).toBe("");
  });

  it("点放大镜 → 输入框出现并聚焦；失焦收起（词保留，放大镜带点）", () => {
    renderAt("tight");
    fireEvent.click(screen.getByRole("button", { name: "Search cards" }));
    const input = screen.getByRole("searchbox", { name: "Search cards" });
    expect(document.activeElement).toBe(input);
    fireEvent.change(input, { target: { value: "lint" } });
    expect(getState().filters.search).toBe("lint");
    fireEvent.blur(input);
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(getState().filters.search).toBe("lint");
    expect(screen.getByRole("button", { name: "Search cards" }).querySelector(".chrome-search-dot")).toBeTruthy();
  });

  it("搜索框展开着点「筛选」/「提建议」：pointerdown 不抢焦点（不先收起重排），一下就开；「筛选」的 click 顺手收起搜索框", () => {
    renderAt("tight");
    fireEvent.click(screen.getByRole("button", { name: "Search cards" }));
    const input = screen.getByRole("searchbox", { name: "Search cards" });
    expect(document.activeElement).toBe(input);

    // 旁边的图标：pointerdown 被 preventDefault（= 浏览器不会把焦点移走、输入框不 blur）；输入框自己不拦（要能点中放光标）
    const filters = screen.getByRole("button", { name: "Filters" });
    expect(fireEvent.pointerDown(filters)).toBe(false);
    expect(fireEvent.pointerDown(screen.getByRole("button", { name: "Send feedback" }))).toBe(false);
    expect(fireEvent.pointerDown(input)).toBe(true);
    expect(screen.getByRole("searchbox")).toBeTruthy();

    // 第一下 click 就开面板；搜索框在同一次渲染里收起（面板量到的锚点已是收起后的位置）
    fireEvent.click(filters);
    expect(screen.getByRole("dialog", { name: "Filters" })).toBeTruthy();
    expect(screen.queryByRole("searchbox")).toBeNull();

    // 搜索框收着时不拦：图标正常拿焦点
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(fireEvent.pointerDown(filters)).toBe(true);
  });

  it("⌘F 展开并聚焦；⎋ 两段：先清词（框留着）、再收起；再按退出多选", () => {
    renderAt("tight");
    act(() => { setFilters({ search: "readme" }); setSelectionMode(true); });
    fireEvent.keyDown(window, { key: "f", metaKey: true });
    const input = screen.getByRole("searchbox", { name: "Search cards" });
    expect(document.activeElement).toBe(input);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(getState().filters.search).toBe("");
    expect(screen.getByRole("searchbox")).toBeTruthy();
    expect(getState().selectionMode).toBe(true);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(getState().selectionMode).toBe(true);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(getState().selectionMode).toBe(false);
  });

  it("档位回到 full：面板与展开态都收掉，条回到行内布局", () => {
    const view = render(
      <HeaderDensityContext.Provider value="tight">
        <FilterBar />
      </HeaderDensityContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    view.rerender(
      <HeaderDensityContext.Provider value="full">
        <FilterBar />
      </HeaderDensityContext.Provider>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("button", { name: "Filters" })).toBeNull();
    expect(screen.getByRole("button", { name: "Filter by tier" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Card sorting" })).toBeTruthy();
    expect(screen.getByRole("searchbox", { name: "Search cards" })).toBeTruthy();
  });
});
