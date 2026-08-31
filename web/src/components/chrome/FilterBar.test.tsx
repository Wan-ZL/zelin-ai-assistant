// FilterBar 行为：chip 多选写进 store + URL（?tier=）、⌘F 聚焦搜索、清除按钮复位。
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { getState, resetStoreForTests } from "../../store";
import { FilterBar } from "./FilterBar";

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
});

afterEach(cleanup);

describe("FilterBar", () => {
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

  it("挂载时从 URL 水合深链过滤器", () => {
    window.history.replaceState(null, "", "/?tier=T1&q=readme&reraised=1");
    render(<FilterBar />);
    const { filters } = getState();
    expect(filters.tiers).toEqual(["T1"]);
    expect(filters.search).toBe("readme");
    expect(filters.reraisedOnly).toBe(true);
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
