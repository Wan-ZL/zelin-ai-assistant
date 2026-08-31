// TrashPage 行为：§9 搜索/恢复/pin 的 wire payload 零多余字段、§40.5 倒计时三态、501 降级提示。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ApiError, fetchBoard, postAction } from "../api";
import { refreshBoard, resetStoreForTests } from "../store";
import type { Board } from "../types";
import { TrashPage } from "./TrashPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

const DAY = 86_400_000;

function boardWithTrash(): Board {
  return {
    generated_at: "2026-08-30T12:00:00Z",
    counts: { trash: 3 },
    needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [],
    trash: [
      { id: "R-201", title: "自动回复 bot", summary: "不想要自动回复。", kind: "suggestion",
        trashed_at: "2026-08-28T00:00:00Z", trash_reason: "rejected", permanent: false,
        purge_at: new Date(Date.now() + 3 * DAY).toISOString() },
      { id: "R-202", title: "永久保留的项", permanent: true,
        trashed_at: "2026-08-01T00:00:00Z", trash_reason: "deleted", purge_at: null },
      { id: "R-203", title: "无 purge_at 的旧行", permanent: false,
        trashed_at: "2026-08-20T00:00:00Z" },
    ],
  } as unknown as Board;
}

beforeEach(async () => {
  window.history.replaceState(null, "", "/?page=trash");
  resetStoreForTests();
  vi.mocked(fetchBoard).mockResolvedValue(boardWithTrash());
  vi.mocked(postAction).mockReset();
  await refreshBoard();
});

afterEach(cleanup);

describe("TrashPage", () => {
  it("倒计时三态：3 天行显示倒计时；pinned 行显「已永久保留」；无 purge_at 不显示", () => {
    render(<TrashPage />);
    expect(screen.getAllByText("Deleted for good in 3d")).toHaveLength(1); // 只有 R-201
    expect(screen.getByText("Kept forever")).toBeTruthy();               // R-202
  });

  it("恢复 → postAction 收到 {action:'restore', id} 且零多余字段（server 400 零容忍）", async () => {
    vi.mocked(postAction).mockResolvedValue({});
    render(<TrashPage />);

    fireEvent.click(screen.getAllByRole("button", { name: "Restore" })[0]);
    await screen.findByText("Restore requested");

    expect(postAction).toHaveBeenCalledTimes(1);
    const payload = vi.mocked(postAction).mock.calls[0][0];
    expect(payload).toEqual({ action: "restore", id: "R-201" });
    expect(Object.keys(payload)).toHaveLength(2);
  });

  it("永久保存 → {action:'pin', id}；pinned/已 pin 行不显示 Pin 按钮", async () => {
    vi.mocked(postAction).mockResolvedValue({});
    render(<TrashPage />);

    // R-202 permanent=true：整页只有 R-201/R-203 两个 Pin 按钮
    expect(screen.getAllByRole("button", { name: "Pin" })).toHaveLength(2);

    fireEvent.click(screen.getAllByRole("button", { name: "Pin" })[0]);
    await screen.findAllByText("Kept forever");
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "pin", id: "R-201" });
    // 本地回执：R-201 的 Pin 按钮消失，倒计时变「已永久保留」
    expect(screen.getAllByRole("button", { name: "Pin" })).toHaveLength(1);
    expect(screen.getAllByText("Kept forever")).toHaveLength(2);
  });

  it("搜索客户端过滤 title/summary", () => {
    render(<TrashPage />);
    fireEvent.change(screen.getByRole("searchbox", { name: "Search trash" }),
      { target: { value: "自动回复" } });
    expect(screen.getByText(/不想要自动回复/)).toBeTruthy();
    expect(screen.queryByText(/永久保留的项/)).toBeNull();
  });

  it("server 501（G1 未接线）→ 显式降级提示，不吞错", async () => {
    vi.mocked(postAction).mockRejectedValue(
      new ApiError(501, { error: { code: "NOT_IMPLEMENTED", message: "stub" } }),
    );
    render(<TrashPage />);
    fireEvent.click(screen.getAllByRole("button", { name: "Restore" })[0]);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("not wired yet");
  });
});
