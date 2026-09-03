// 永久性完成书立条（原生 Kanban.swift 右侧 collapsibleColumn）：折叠只显 counts.archived；
// 展开 = 搜索框 + 行（你封存/自动封存 章、原来在：<列名>、相对时间）+「放回看板」→ {action:"unarchive"}；
// 搜索按 title/summary 客户端过滤；被 cap 时给「仅显示最近 N 条」。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { fetchBoard, postAction } from "../../api";
import { refreshBoard, resetStoreForTests } from "../../store";
import type { Board } from "../../types";
import { ArchiveStrip } from "./ArchiveStrip";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchCard: vi.fn(), postAction: vi.fn().mockResolvedValue({ ok: true }) };
});

const board = {
  generated_at: "2026-09-01T12:00:00Z",
  counts: { archived: 3 },
  needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [],
  archived: [
    { id: "R-701", title: "旧的 onboarding 文档", summary: "已彻底结束", kind: "suggestion",
      archived_at: new Date(Date.now() - 2 * 86400_000).toISOString(), archive_reason: "user", prev_status: "delivered" },
    { id: "R-702", title: "周会纪要模板", kind: "debt", archived_at: new Date(Date.now() - 3600_000).toISOString(),
      archive_reason: "auto", prev_status: "detected" },
  ],
} as unknown as Board;

beforeEach(async () => {
  resetStoreForTests();
  vi.mocked(fetchBoard).mockResolvedValue(board);
  vi.mocked(postAction).mockClear();
  await refreshBoard();
});

afterEach(cleanup);

describe("ArchiveStrip", () => {
  it("默认折叠：只显真实总数 3（不是数组长度 2）；点击展开列出行 + cap 提示", () => {
    const { container } = render(<ArchiveStrip />);
    expect(container.querySelector(".backlog-strip")?.classList.contains("is-collapsed")).toBe(true);
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.queryByText(/onboarding/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Done for good/ }));
    expect(screen.getByText("已彻底结束")).toBeTruthy(); // summary 优先为展示名
    expect(screen.getByText("周会纪要模板")).toBeTruthy();
    expect(screen.getByText("Showing the latest 2 only")).toBeTruthy();
  });

  it("行 chips：你封存 绿 / 自动封存 中性、原来在：<列名>、相对时间", () => {
    render(<ArchiveStrip />);
    fireEvent.click(screen.getByRole("button", { name: /Done for good/ }));
    expect(screen.getByText("You sealed").className).toContain("chip-success");
    expect(screen.getByText("Auto-sealed").className).toBe("chip");
    expect(screen.getByText((_, el) => el?.classList.contains("card-meta-text") === true && /was in: Done/.test(el.textContent ?? ""))).toBeTruthy();
    expect(screen.getByText((_, el) => el?.classList.contains("card-meta-text") === true && /was in: Backlog/.test(el.textContent ?? ""))).toBeTruthy();
    expect(screen.getByText("2d ago")).toBeTruthy();
    expect(screen.getByText("1h ago")).toBeTruthy();
  });

  it("放回看板 → {action:'unarchive', comment:null, id}", () => {
    render(<ArchiveStrip />);
    fireEvent.click(screen.getByRole("button", { name: /Done for good/ }));
    fireEvent.click(screen.getAllByRole("button", { name: "Put back" })[0]);
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "unarchive", comment: null, id: "R-701" });
  });

  it("搜索框按 title/summary 过滤；无命中给「No matches」", () => {
    render(<ArchiveStrip />);
    fireEvent.click(screen.getByRole("button", { name: /Done for good/ }));
    const search = screen.getByPlaceholderText("Search title / summary…");
    fireEvent.change(search, { target: { value: "纪要" } });
    expect(screen.queryByText("已彻底结束")).toBeNull();
    expect(screen.getByText("周会纪要模板")).toBeTruthy();
    fireEvent.change(search, { target: { value: "zzz" } });
    expect(screen.getByText("No matches")).toBeTruthy();
  });

  it("再点一次收起", () => {
    const { container } = render(<ArchiveStrip />);
    const toggle = screen.getByRole("button", { name: /Done for good/ });
    fireEvent.click(toggle);
    expect(container.querySelector(".backlog-strip")?.classList.contains("is-collapsed")).toBe(false);
    fireEvent.click(toggle);
    expect(container.querySelector(".backlog-strip")?.classList.contains("is-collapsed")).toBe(true);
  });
});
