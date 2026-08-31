// AppShell 行为测试（G7）：整页状态分派（loading / 从未加载成功的离线空态 / 正常渲染）
// 与有旧快照时的离线横幅。api 层 mock 掉，经 store 真实 action 驱动状态。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, resetStoreForTests, setConnection } from "../../store";
import type { Board } from "../../types";
import { AppShell } from "./AppShell";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchBoard: vi.fn(), fetchCard: vi.fn() };
});

const fetchBoardMock = vi.mocked(fetchBoard);

function makeBoard(): Board {
  return {
    generated_at: new Date().toISOString(),
    counts: {},
    needs_approval: [],
    running: [],
    needs_input: [],
    review: [],
    completed: [],
    debt: [],
    trash: [],
  };
}

function renderShell() {
  return render(
    <LanguageContext.Provider value="en">
      <AppShell>
        <div>page-content</div>
      </AppShell>
    </LanguageContext.Provider>,
  );
}

describe("AppShell", () => {
  beforeEach(() => {
    resetStoreForTests();
    fetchBoardMock.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("首载中：渲染 loading 空态，不渲染页面内容", () => {
    renderShell();
    expect(screen.getByText("Loading the board…")).toBeTruthy();
    expect(screen.queryByText("page-content")).toBeNull();
  });

  it("从未加载成功 + 读失败：诚实离线空态（原因 + dev-preview 恢复路径 + 重试按钮）", async () => {
    fetchBoardMock.mockRejectedValue(new Error("connection refused"));
    await refreshBoard();
    renderShell();
    expect(screen.getByText("Can't reach the local server")).toBeTruthy();
    expect(screen.getByText(/dev-preview\.sh/)).toBeTruthy();
    expect(screen.queryByText("page-content")).toBeNull();

    // 重试按钮走同一条 refreshBoard 通道
    fetchBoardMock.mockResolvedValue(makeBoard());
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await vi.waitFor(() => expect(fetchBoardMock).toHaveBeenCalledTimes(2));
  });

  it("有旧快照 + SSE 断线：页面照常渲染 + 顶部离线横幅声明快照降级", async () => {
    fetchBoardMock.mockResolvedValue(makeBoard());
    await refreshBoard();
    setConnection("reconnecting");
    renderShell();
    expect(screen.getByText("page-content")).toBeTruthy();
    const banner = screen.getByRole("alert");
    expect(banner.textContent).toContain("Can't reach the local server");
    expect(banner.textContent).toContain("last successfully loaded snapshot");
  });

  it("正常在线：渲染页面内容，无横幅", async () => {
    fetchBoardMock.mockResolvedValue(makeBoard());
    await refreshBoard();
    setConnection("live");
    renderShell();
    expect(screen.getByText("page-content")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
