// 「筛选」面板：焦点**真的在面板里的元素上**（不是 body）按 ⎋（§49 追记 2026-09-04 D31「⎋ 关闭、焦点还给触发按钮」；
// issue #420 的孪生 R-222）。#425 / #427 带来的 FilterPopover.escDelivery.test.tsx 钉的是焦点还停在 body 上时 ⎋ 的交付路径
// （#420 的修复本体）；它那条「焦点在面板里的常规路」实际仍从 document.body 派发——这里补上它没走到的那条路：从真实焦点元素派发，
// 环的首站与末站各一次。既有 FilterBar.test.tsx 那条把 ⎋ 派到面板节点本身，也不是从焦点元素派发。
// 本文件不依赖 #420 的修复：修复前走面板的 React onKeyDown、修复后走 window 监听，出口相同（面板关、搜索词不动、焦点还给「筛选」）。
// 它单独成文件只因为 escDelivery.test.tsx 还不在 main 上（那是 #425 / #427 的 add/add 新文件，从 main 出发碰它必冲突）；
// 那两个 PR 合入后，这两条应并进 escDelivery.test.tsx。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HeaderDensityContext } from "../shell/headerDensity";
import { getState, resetStoreForTests, setFilters } from "../../store";
import { FilterBar } from "./FilterBar";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

// 与 FilterPopover.tsx 的 FOCUSABLE 同义——判例自己数一遍环的站点，不信组件的说法
const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])";

/** compact 档开面板，返回触发按钮与环的站点（DOM 顺序） */
function openPanel() {
  render(
    <HeaderDensityContext.Provider value="compact">
      <FilterBar />
    </HeaderDensityContext.Provider>,
  );
  const trigger = screen.getByRole("button", { name: "Filters" });
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Filters" });
  const stops = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE));
  expect(stops.length).toBeGreaterThanOrEqual(5);
  return { trigger, stops };
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
});

afterEach(cleanup);

describe("FilterPopover ⎋ — 从面板内的真实焦点元素派发", () => {
  for (const stop of ["first", "last"] as const) {
    it(`焦点在环的${stop === "first" ? "第一站（Tier chip）" : "最后一站（页脚「选择」）"}上按 ⎋：面板关、搜索词不动、焦点还给「筛选」`, () => {
      const { trigger, stops } = openPanel();
      act(() => { setFilters({ search: "readme" }); });
      const target = stop === "first" ? stops[0] : stops[stops.length - 1];
      target.focus();
      expect(document.activeElement).toBe(target);
      expect(target).not.toBe(document.body);

      fireEvent.keyDown(target, { key: "Escape" });

      expect(screen.queryByRole("dialog")).toBeNull();
      expect(getState().filters.search).toBe("readme"); // 面板吃掉这一下，FilterBar 的两段 ⎋ 不插手
      expect(document.activeElement).toBe(trigger);
    });
  }
});
