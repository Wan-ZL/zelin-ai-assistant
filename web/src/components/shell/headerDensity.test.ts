// 顶栏密度档判定（CONTRACT §49 追记 2026-09-04）：宽度 → 档位的映射、阈值从 tokens.css 的
// --header-density-* 读（含壳 / 英文加项与 --text-scale 放大）、ResizeObserver 驱动、jsdom 无 RO 或读不到
// token 时恒为 full、override 优先。
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRef } from "react";
import { densityForWidth, readDensityThresholds, useMeasuredHeaderDensity, type DensityExtras } from "./headerDensity";

const TOKENS: Record<string, string> = {
  "--header-density-full-min": "1220px",
  "--header-density-compact-min": "800px",
  "--header-density-shell-extra": "180px",
  "--header-density-en-extra": "200px",
  "--text-scale": "1",
};

function stubTokens(overrides: Record<string, string> = {}) {
  const table = { ...TOKENS, ...overrides };
  vi.spyOn(window, "getComputedStyle").mockReturnValue({
    getPropertyValue: (name: string) => table[name] ?? "",
  } as unknown as CSSStyleDeclaration);
}

const NONE: DensityExtras = { shell: false, english: false };

/** 假 ResizeObserver：记住回调，测试里手动喂宽度 */
class FakeResizeObserver {
  static instances: FakeResizeObserver[] = [];
  observed: Element[] = [];
  disconnected = false;
  constructor(public callback: ResizeObserverCallback) {
    FakeResizeObserver.instances.push(this);
  }
  observe(el: Element) { this.observed.push(el); }
  unobserve() {}
  disconnect() { this.disconnected = true; }
}

function installResizeObserver() {
  FakeResizeObserver.instances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
}

function useHarness(width: { current: number }, extras: DensityExtras, override?: "full" | "compact" | "tight") {
  const ref = useRef<HTMLElement | null>(null);
  if (!ref.current) {
    const el = document.createElement("header");
    el.getBoundingClientRect = () => ({ width: width.current } as DOMRect);
    ref.current = el;
  }
  return useMeasuredHeaderDensity(ref, { override, extras });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("densityForWidth", () => {
  const thresholds = { fullMin: 1220, compactMin: 800 };

  it("≥ fullMin → full；compactMin ≤ w < fullMin → compact；再窄 → tight（边界含下限）", () => {
    expect(densityForWidth(1600, thresholds)).toBe("full");
    expect(densityForWidth(1220, thresholds)).toBe("full");
    expect(densityForWidth(1219, thresholds)).toBe("compact");
    expect(densityForWidth(800, thresholds)).toBe("compact");
    expect(densityForWidth(799, thresholds)).toBe("tight");
    expect(densityForWidth(0, thresholds)).toBe("tight");
  });
});

describe("readDensityThresholds（阈值单源 = tokens.css）", () => {
  it("浏览器 zh：直接读两把阈值", () => {
    stubTokens();
    expect(readDensityThresholds(document.body, NONE)).toEqual({ fullMin: 1220, compactMin: 800 });
  });

  it("壳桥在场 / 英文 各加一个静态加项，两者可叠加", () => {
    stubTokens();
    expect(readDensityThresholds(document.body, { shell: true, english: false })).toEqual({ fullMin: 1400, compactMin: 980 });
    expect(readDensityThresholds(document.body, { shell: false, english: true })).toEqual({ fullMin: 1420, compactMin: 1000 });
    expect(readDensityThresholds(document.body, { shell: true, english: true })).toEqual({ fullMin: 1600, compactMin: 1180 });
  });

  it("--text-scale 放大字号 → 阈值等比放大（显示偏好 xl = 1.25）", () => {
    stubTokens({ "--text-scale": "1.25" });
    expect(readDensityThresholds(document.body, NONE)).toEqual({ fullMin: 1525, compactMin: 1000 });
  });

  it("读不到 token（样式没加载 / jsdom）→ null，不判档", () => {
    stubTokens({ "--header-density-full-min": "", "--header-density-compact-min": "" });
    expect(readDensityThresholds(document.body, NONE)).toBeNull();
  });
});

describe("useMeasuredHeaderDensity", () => {
  it("没有 ResizeObserver（jsdom 默认）→ 恒为 full，也不读样式", () => {
    const spy = vi.spyOn(window, "getComputedStyle");
    const width = { current: 300 };
    const { result } = renderHook(() => useHarness(width, NONE));
    expect(result.current).toBe("full");
    expect(spy).not.toHaveBeenCalled();
  });

  it("挂载即按当前宽判档，之后随 ResizeObserver 回调更新；卸载时 disconnect", () => {
    installResizeObserver();
    stubTokens();
    const width = { current: 1000 };
    const { result, unmount } = renderHook(() => useHarness(width, NONE));
    expect(result.current).toBe("compact");
    const observer = FakeResizeObserver.instances[0];
    expect(observer.observed).toHaveLength(1);

    width.current = 600;
    act(() => observer.callback([], observer as unknown as ResizeObserver));
    expect(result.current).toBe("tight");

    width.current = 1500;
    act(() => observer.callback([], observer as unknown as ResizeObserver));
    expect(result.current).toBe("full");

    unmount();
    expect(observer.disconnected).toBe(true);
  });

  it("壳里同一宽度判得更紧：1300 在浏览器是 full，在壳里（+180）是 compact", () => {
    installResizeObserver();
    stubTokens();
    const width = { current: 1300 };
    const browser = renderHook(() => useHarness(width, NONE));
    expect(browser.result.current).toBe("full");
    const shell = renderHook(() => useHarness(width, { shell: true, english: false }));
    expect(shell.result.current).toBe("compact");
  });

  it("override 优先于实测，且不建 observer", () => {
    installResizeObserver();
    stubTokens();
    const width = { current: 1600 };
    const { result } = renderHook(() => useHarness(width, NONE, "tight"));
    expect(result.current).toBe("tight");
    expect(FakeResizeObserver.instances).toHaveLength(0);
  });

  it("有 ResizeObserver 但读不到 token → 留在 full", () => {
    installResizeObserver();
    stubTokens({ "--header-density-full-min": "" });
    const width = { current: 300 };
    const { result } = renderHook(() => useHarness(width, NONE));
    expect(result.current).toBe("full");
    expect(FakeResizeObserver.instances).toHaveLength(0);
  });
});
