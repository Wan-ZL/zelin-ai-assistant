// 「筛选」面板的另外两个关闭源与它们的守卫半边（§49 追记 2026-09-04 D31「⎋ / 点外面 / 视口变化 关闭」；issue #420 的孪生 R-222）。
// D31 的五个动词里，「视口变化关闭」此前在 web/src 与 web/e2e 里一条判例都没有；「点外面」只钉了正路
// （FilterBar.test.tsx 把 pointerdown 派到 document.body），守卫半边——点面板里、点「筛选」按钮本身**不**关——没人钉。
// 这里补齐，且不依赖 #420 的修复（#425 / #427 没有碰 closeFromOutside / closeFromViewportChange）：
//   1) window resize → 关；
//   2) 面板外的 scroll（window 上 capture 监听）→ 关；面板**里**滚（listbox / 面板自身内容）→ 不关；
//   3) pointerdown 落在面板里、落在「筛选」按钮上 → 不关（按钮自己的 click 才负责 toggle）。
// jsdom 没有真布局，resize / scroll 都是派发事件、不是真的改视口——钉的是「这些事件来了面板怎么做」，不是几何。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HeaderDensityContext } from "../shell/headerDensity";
import { resetStoreForTests } from "../../store";
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

describe("FilterPopover 关闭源 — 视口变化", () => {
  it("window resize：面板关", () => {
    openPanel();
    fireEvent(window, new Event("resize"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("面板外的 scroll：面板关；面板里的 scroll：不关", () => {
    const { dialog } = openPanel();
    // 面板里滚——目标是面板的子节点（chips 那一行）：留着
    fireEvent.scroll(dialog.querySelector(".chrome-filter-panel-row")!);
    expect(screen.getByRole("dialog", { name: "Filters" })).toBeTruthy();
    // 面板外滚——目标是文档：关
    fireEvent.scroll(document);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("FilterPopover 关闭源 — 点外面的守卫半边", () => {
  it("pointerdown 落在面板里（chip / 页脚按钮）：不关", () => {
    const { dialog } = openPanel();
    fireEvent.pointerDown(screen.getByRole("button", { name: "Filter by tier" }));
    fireEvent.pointerDown(screen.getByRole("button", { name: "Select" }));
    fireEvent.pointerDown(dialog);
    expect(screen.getByRole("dialog", { name: "Filters" })).toBeTruthy();
  });

  it("pointerdown 落在「筛选」按钮本身：不关（toggle 归它自己的 click）；点 body 才关", () => {
    const { trigger } = openPanel();
    fireEvent.pointerDown(trigger);
    expect(screen.getByRole("dialog", { name: "Filters" })).toBeTruthy();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
