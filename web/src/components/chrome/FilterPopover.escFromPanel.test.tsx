// 「筛选」面板：焦点**在面板里的后代元素上**按 ⎋（§49 追记 2026-09-04 D31「⎋ 关闭、焦点还给触发按钮」+「面板里开着 listbox 时
// ⎋ 归子弹层」；issue #420 的孪生 R-222）。三条判例都从真实焦点元素派发，与既有两处互补：
//   · FilterBar.test.tsx 把 ⎋ 派到面板节点本身（fireEvent.keyDown(dialog, …)）；#425 / #427 带来的 FilterPopover.escDelivery.test.tsx
//     从 document.body 派发（#420 修复本体钉的那一侧）；这里的 target 是面板的**后代**（chip / 页脚按钮 / 下拉里的 option）。
//   · 出口断言比既有的多一句「这一下被 preventDefault」：修复前是面板 React onKeyDown 拦的，修复后是 window 监听拦的，
//     两边都得拦——这是判例真正判到机制的那半。「搜索词不动」那句在 main 上是 §34 双保险（面板 stopPropagation 与 FilterBar 的
//     panelOpen 让位互相遮蔽，任一半单独删都不红、两半同删才红），如实写在这里，不把功劳记给某一半。
//   · 「面板里开着 chip 下拉时 ⎋ 只收下拉、面板留着」在 main 上此前零判例（删掉 FilterPopover 的 listbox 守卫整棵 web 单测全绿），
//     这里从 option 上派发钉住它；#425 的 escDelivery 有同义一条（从 body 派发），合入后一并折进去。
// 本文件不依赖 #420 的修复：修复前走面板的 React onKeyDown、修复后走 window 监听，出口相同。
// 单独成文件只因为 escDelivery.test.tsx 还不在 main 上（那是 #425 / #427 的 add/add 新文件，从 main 出发碰它必冲突）；
// 那两个 PR 合入后，这三条应并进 escDelivery.test.tsx。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HeaderDensityContext } from "../shell/headerDensity";
import { getState, resetStoreForTests, setFilters } from "../../store";
import { FilterBar } from "./FilterBar";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

/** compact 档开面板，返回触发按钮与面板 */
function openPanel() {
  render(
    <HeaderDensityContext.Provider value="compact">
      <FilterBar />
    </HeaderDensityContext.Provider>,
  );
  const trigger = screen.getByRole("button", { name: "Filters" });
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Filters" });
  return { trigger, dialog };
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
});

afterEach(cleanup);

describe("FilterPopover ⎋ — 从面板内的真实焦点元素派发", () => {
  for (const [where, name] of [["第一站（Tier chip）", "Filter by tier"], ["最后一站（页脚「选择」）", "Select"]] as const) {
    it(`焦点在环的${where}上按 ⎋：这一下被拦下、面板关、搜索词不动、焦点还给「筛选」`, () => {
      const { trigger, dialog } = openPanel();
      act(() => { setFilters({ search: "readme" }); });
      const target = screen.getByRole("button", { name });
      expect(dialog.contains(target)).toBe(true);
      target.focus();
      expect(document.activeElement).toBe(target);

      expect(fireEvent.keyDown(target, { key: "Escape" })).toBe(false); // false = defaultPrevented：面板认领了这一下

      expect(screen.queryByRole("dialog")).toBeNull();
      expect(getState().filters.search).toBe("readme"); // §34 双保险兜着（见文件头），不归功于某一半
      expect(document.activeElement).toBe(trigger);
    });
  }

  it("面板里开着 Tier 下拉、焦点在 option 上按 ⎋：只收下拉、面板留着；再从 chip 上按一下才关面板", () => {
    const { trigger, dialog } = openPanel();
    const tier = screen.getByRole("button", { name: "Filter by tier" });
    fireEvent.click(tier);
    const listbox = screen.getByRole("listbox");
    expect(dialog.contains(listbox)).toBe(true); // 子弹层 portal 进面板本体
    const option = screen.getByRole("option", { name: /T1/ });
    option.focus();
    expect(document.activeElement).toBe(option);

    fireEvent.keyDown(option, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.getByRole("dialog", { name: "Filters" })).toBeTruthy(); // 一下只收一层

    tier.focus();
    expect(fireEvent.keyDown(tier, { key: "Escape" })).toBe(false);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});
