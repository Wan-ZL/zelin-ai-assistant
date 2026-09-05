// 顶层错误边界（CONTRACT §49 追记 `store-resilience-drawer`）：渲染期未捕获的异常 → 「看板渲染失败」+ 原话 + 「重试」，
// 而不是 React 卸载整棵树留白页（原生 Store.swift:320-324「Keep the previously good dashboard rather than blanking the UI」
// 的最后一道）。重试 = 重挂子树 + refreshBoard；正常渲染时边界透明；文案随 store 语言。api 层 mock 掉，零真实网络。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "../../api";
import { resetStoreForTests, setLanguage } from "../../store";
import { AppErrorBoundary, renderErrorText } from "./AppErrorBoundary";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchBoard: vi.fn(), fetchCard: vi.fn() };
});

let crash = true;
function Bomb() {
  if (crash) throw new TypeError("Cannot read properties of undefined (reading 'filter')");
  return <div>board-content</div>;
}

// React dev 模式把被边界接住的异常再经 window 的 error 事件抛一遍给 jsdom 报告——判例里它是预期的，吞掉免得刷屏
const swallowExpectedCrash = (event: ErrorEvent) => event.preventDefault();

beforeEach(() => {
  resetStoreForTests();
  setLanguage("en");
  crash = true;
  vi.mocked(fetchBoard).mockReset();
  vi.mocked(fetchBoard).mockResolvedValue({ generated_at: "x", counts: {}, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [] });
  vi.spyOn(console, "error").mockImplementation(() => undefined); // React 的错误边界日志 + 我们自己的 console.error
  window.addEventListener("error", swallowExpectedCrash);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.removeEventListener("error", swallowExpectedCrash);
});

describe("AppErrorBoundary", () => {
  it("is transparent while children render", () => {
    crash = false;
    render(<AppErrorBoundary><Bomb /></AppErrorBoundary>);
    expect(screen.getByText("board-content")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a render crash shows the line + the error's own words + Retry instead of a blank tree", () => {
    render(<AppErrorBoundary><Bomb /></AppErrorBoundary>);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("The board failed to render");
    expect(alert.textContent).toContain("Cannot read properties of undefined (reading 'filter')");
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
    expect(screen.queryByText("board-content")).toBeNull();
  });

  it("Retry remounts the children and refetches the board through refreshBoard", async () => {
    render(<AppErrorBoundary><Bomb /></AppErrorBoundary>);
    screen.getByRole("alert");
    crash = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("board-content")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(fetchBoard).toHaveBeenCalledTimes(1);
  });

  it("Retry while the cause persists shows the boundary again (no blank page either way)", async () => {
    render(<AppErrorBoundary><Bomb /></AppErrorBoundary>);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("speaks the store's language (LanguageContext lives inside App, the boundary outside it)", () => {
    setLanguage("zh");
    render(<AppErrorBoundary><Bomb /></AppErrorBoundary>);
    expect(screen.getByText("看板渲染失败")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });

  it("renderErrorText: Error → message (name when the message is empty); non-Error throws → String()", () => {
    expect(renderErrorText(new RangeError("too deep"))).toBe("too deep");
    expect(renderErrorText(new RangeError(""))).toBe("RangeError");
    expect(renderErrorText("plain string")).toBe("plain string");
    expect(renderErrorText(42)).toBe("42");
  });
});
