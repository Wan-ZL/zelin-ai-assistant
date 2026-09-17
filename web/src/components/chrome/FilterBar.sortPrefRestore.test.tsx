// 顶栏「排序」select 对 cardSortOrder（CONTRACT §66.2 setting:prefs:cardSortOrder；原生 Settings「卡片排序」Picker 的
// UserDefaults 同名键）的**读回**：parity.test.tsx 同名 it() 钉「改 select → 键里有值」，cardSort.test.ts 钉两个纯函数，
// BoardLanes.test.tsx 钉 setSortOrder 重排；这里钉「键里已有值 → store 首帧读它 → 挂载的真控件显示它」：
//   1) 存储 "deadline" / "oldest" / "newest" → select.value 同值，getState().sortOrder 同值；
//   2) 存储未知值 → 控件显示 newest（normalizeSortOrder），键本身不改写——直到用户再选一次才写回合法值；
//   3) 没有键 → newest，挂载不写键；
//   4) 坏值之上改一次 → 键 / store / 控件三处一致；重新挂载读回的还是它。
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getState, resetStoreForTests } from "../../store";
import { FilterBar } from "./FilterBar";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

const KEY = "cardSortOrder";

/** 键先落、再重置 store（store 的 sortOrder 在初始化时读一次 localStorage——首帧就是这个顺序），再挂载 */
function mountWithStored(raw: string | null) {
  window.localStorage.clear();
  if (raw !== null) window.localStorage.setItem(KEY, raw);
  resetStoreForTests();
  const view = render(<FilterBar />); // 默认 HeaderDensity full：排序 select 在条上
  return view.container.querySelector<HTMLSelectElement>(".chrome-sort-select")!;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("FilterBar — cardSortOrder 读回", () => {
  it.each(["deadline", "oldest", "newest"])("存储 %j → 挂载的 select 与 store 都是它", (order) => {
    const select = mountWithStored(order);
    expect(select).toBeTruthy();
    expect(select.value).toBe(order);
    expect(getState().sortOrder).toBe(order);
  });

  it.each(["garbage", "Newest", "", "null"])("存储未知值 %j → 控件显示 newest，键不改写", (raw) => {
    const select = mountWithStored(raw);
    expect(select.value).toBe("newest");
    expect(getState().sortOrder).toBe("newest");
    expect(window.localStorage.getItem(KEY)).toBe(raw);
  });

  it("没有键 → newest，挂载不写键", () => {
    const select = mountWithStored(null);
    expect(select.value).toBe("newest");
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it("坏值之上改一次 → 键 / store / 控件三处一致；重新挂载读回的还是它", () => {
    const select = mountWithStored("garbage");
    fireEvent.change(select, { target: { value: "oldest" } });
    expect(window.localStorage.getItem(KEY)).toBe("oldest");
    expect(getState().sortOrder).toBe("oldest");
    expect(select.value).toBe("oldest");
    cleanup();
    resetStoreForTests();
    const again = render(<FilterBar />).container.querySelector<HTMLSelectElement>(".chrome-sort-select")!;
    expect(again.value).toBe("oldest");
    expect(getState().sortOrder).toBe("oldest");
  });
});
