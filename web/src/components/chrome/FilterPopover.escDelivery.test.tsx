// 「筛选」面板的 ⎋ **交付路径**（§49 追记 2026-09-18，issue #420）——与 FilterBar.test.tsx 里那条
// 「⎋ 关面板、不清搜索词、焦点回到「筛选」」互补：那条把 ⎋ 直接派到面板节点上（`fireEvent.keyDown(dialog, …)`），
// 走的是 React 的委托监听，**焦点在哪儿都能跑通**，所以它判不出本 issue。这里判的正是它判不到的那一半：
//   1) 焦点还没进面板（activeElement = body）时按 ⎋，面板照样关——真浏览器里这是常态而不是边角：
//      tight 档点「筛选」开面板的那一下，pointerdown 被 keepSearchFocus 拦下（按钮不拿焦点）、click 又把正
//      聚焦的搜索框卸掉，焦点掉回 body；修复前这一下 ⎋ 谁都收不到（面板的 React 监听要 target 在面板里，
//      FilterBar 的 window 监听因 panelOpen 让位），面板永久卡开；
//   2) 打开面板即把焦点放进去——同步，不等下一帧（此前排在 requestAnimationFrame 里）；
//   3) 让位次序：面板里开着 listbox → ⎋ 归子弹层；上面压着模态（详情侧栏 / 原生 <dialog>）→ ⎋ 归模态；
//      IME 候选期间的 ⎋ 归输入法（§15 红线）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HeaderDensityContext, type HeaderDensity } from "../shell/headerDensity";
import { getState, resetStoreForTests, setFilters } from "../../store";
import { FilterBar } from "./FilterBar";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

function renderAt(density: HeaderDensity) {
  return render(
    <HeaderDensityContext.Provider value={density}>
      <FilterBar />
    </HeaderDensityContext.Provider>,
  );
}

/** 开面板，返回触发按钮 */
function openPanel(density: HeaderDensity = "compact") {
  renderAt(density);
  const trigger = screen.getByRole("button", { name: "Filters" });
  fireEvent.click(trigger);
  expect(screen.getByRole("dialog", { name: "Filters" })).toBeTruthy();
  return trigger;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
});

afterEach(cleanup);

describe("FilterPopover ⎋ — 判据是「面板开着」，不是「焦点在面板里」（#420）", () => {
  it("焦点停在 body 上按 ⎋：面板照样关、搜索词不动、焦点还给「筛选」", () => {
    const trigger = openPanel();
    act(() => { setFilters({ search: "readme" }); });

    // 真浏览器里 tight 档开面板那一下焦点就落在 body；这里显式退回去，把当初只有加载重的 runner 才撞上
    // 的那一下变成每次都跑的判例
    (document.activeElement as HTMLElement | null)?.blur();
    expect(document.activeElement).toBe(document.body);

    fireEvent.keyDown(document.body, { key: "Escape" });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(getState().filters.search).toBe("readme"); // 面板吃掉这一下，FilterBar 的两段 ⎋ 不插手
    expect(document.activeElement).toBe(trigger);
  });

  it("焦点在面板里的常规路也照关（同一个 window 监听，两条路一个出口）", () => {
    const trigger = openPanel();
    const tier = screen.getByRole("button", { name: "Filter by tier" });
    tier.focus();
    expect(document.activeElement).toBe(tier);

    fireEvent.keyDown(document.body, { key: "Escape" });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("tight 档：搜索框展开着点「筛选」——焦点掉回 body 的那一下 ⎋ 就能关（CI 上卡开的正是这条）", () => {
    renderAt("tight");
    fireEvent.click(screen.getByRole("button", { name: "Search cards" }));
    expect(document.activeElement).toBe(screen.getByRole("searchbox", { name: "Search cards" }));

    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    expect(screen.getByRole("dialog", { name: "Filters" })).toBeTruthy();
    expect(screen.queryByRole("searchbox")).toBeNull(); // 同一次渲染里收起
    (document.activeElement as HTMLElement | null)?.blur();
    expect(document.activeElement).toBe(document.body);

    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("FilterPopover 焦点 — 打开即进面板，不等下一帧（#420）", () => {
  it("点开面板：焦点当场落在面板第一个可聚焦项上，Tab 的环因此从一开始就成立", () => {
    openPanel();
    const first = screen.getByRole("button", { name: "Filter by tier" });
    // 修复前这一下排在 requestAnimationFrame 里：帧来之前 activeElement 还是 body，而 Tab 判定读的
    // 就是 activeElement——body 既不是 first 也不是 last，焦点会直接跑出面板
    expect(document.activeElement).toBe(first);
    expect(screen.getByRole("dialog", { name: "Filters" }).contains(first)).toBe(true);
  });
});

describe("FilterPopover ⎋ — 让位次序", () => {
  it("面板里开着 listbox：⎋ 归子弹层——listbox 关、面板留着；再一下才关面板", () => {
    openPanel();
    fireEvent.click(screen.getByRole("button", { name: "Filter by tier" }));
    const dialog = screen.getByRole("dialog", { name: "Filters" });
    expect(dialog.contains(screen.getByRole("listbox"))).toBe(true); // 子弹层 portal 进面板本体

    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryByRole("dialog", { name: "Filters" })).toBeTruthy();

    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("上面压着模态（详情侧栏那类 aria-modal 面）：⎋ 归模态，面板不动", () => {
    openPanel();
    const modal = document.createElement("div");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    document.body.appendChild(modal);
    try {
      fireEvent.keyDown(document.body, { key: "Escape" });
      expect(screen.queryByRole("dialog", { name: "Filters" })).toBeTruthy();
    } finally {
      modal.remove();
    }
    // 模态走了，⎋ 又归面板
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("上面压着原生 <dialog open>：⎋ 归它，面板不动", () => {
    openPanel();
    const native = document.createElement("dialog");
    native.setAttribute("open", "");
    document.body.appendChild(native);
    try {
      fireEvent.keyDown(document.body, { key: "Escape" });
      expect(screen.queryByRole("dialog", { name: "Filters" })).toBeTruthy();
    } finally {
      native.remove();
    }
    // 尾巴不能省：没有它，这条在「⎋ 根本到不了面板」的旧实现上也会绿——判例得判得出错才算判例
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("别人先认领了这一下（defaultPrevented）：⎋ 不归面板——顺序无关的那半边让位判据", () => {
    openPanel();
    // 面板外的文字框只 preventDefault、不 stopPropagation（设置页搜索框那类），事件照样冒到 window；
    // ⌘L 之类的快捷键能把焦点直接送进这种框，不经 pointerdown 也不经 Tab，所以这个状态真到得了
    const foreign = document.createElement("input");
    foreign.type = "search";
    foreign.addEventListener("keydown", (event) => { if (event.key === "Escape") event.preventDefault(); });
    document.body.appendChild(foreign);
    foreign.focus();
    try {
      fireEvent.keyDown(foreign, { key: "Escape" });
      expect(screen.queryByRole("dialog", { name: "Filters" })).toBeTruthy();
    } finally {
      foreign.remove();
    }
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("面板与子弹层同一次 commit 出生（档位变窄前点开过 chip）：⎋ 只收子弹层，面板留着", () => {
    // 前提是既有状态卫生的一个洞：openChip 只在 panelOpen 跳变时复位（FilterBar），full 档下 panelOpen
    // 恒 false，所以 chip 开着时档位变窄，openChip 会留下来。下一次点「筛选」，面板与它里面的 listbox
    // 在同一次 commit 里出生——此时 React 的「子先于父」让 TaskPropertyPicker 的 window 监听排在本面板
    // 之前，「面板先挂载所以先跑」的假设反了。
    // 诚实交代这条钉得到什么：jsdom 不做真浏览器在同一 target 的两个监听之间那次 microtask checkpoint，
    // 所以这里 React 还没来得及把 listbox 摘掉，救场的仍是 querySelector 那道门。真正钉住「顺序反了也
    // 不出事」的是上面那条 defaultPrevented 判例（浏览器里实测过顺序确实会反）。这条钉的是结果：这个
    // 状态下一下 ⎋ 只许收一层。
    const view = render(
      <HeaderDensityContext.Provider value="full">
        <FilterBar />
      </HeaderDensityContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Filter by tier" }));
    expect(screen.getByRole("listbox")).toBeTruthy();
    view.rerender(
      <HeaderDensityContext.Provider value="compact">
        <FilterBar />
      </HeaderDensityContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    expect(screen.getByRole("dialog", { name: "Filters" })).toBeTruthy();
    expect(screen.getByRole("listbox")).toBeTruthy(); // 面板带着开着的 chip 一起出生

    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryByRole("dialog", { name: "Filters" })).toBeTruthy(); // 一下只收一层
    // 尾巴不能省：没有它这条在「⎋ 到不了面板」的旧实现上也绿——「面板还在」为了错误的理由成立
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("IME 候选期间的 ⎋ 归输入法（§15 红线）：isComposing / keyCode 229 都不关面板", () => {
    openPanel();
    fireEvent.keyDown(document.body, { key: "Escape", isComposing: true });
    expect(screen.queryByRole("dialog", { name: "Filters" })).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "Escape", keyCode: 229 });
    expect(screen.queryByRole("dialog", { name: "Filters" })).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
