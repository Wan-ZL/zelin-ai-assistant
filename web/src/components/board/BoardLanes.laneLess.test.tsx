// 看板装配对「缺列的合法快照」不崩（CONTRACT §49 追记 `store-resilience-drawer`）：server 回了带 generated_at 的对象、
// 却一列都没有（或 counts 不是对象）——store.normalizeBoardShape 落地前补成 `[]` / `{}`（原生 Dashboard.init(from:) 的列级
// 宽容），BoardLanes 照常渲染各列空态而不是 TypeError 进错误边界。此前这种体原样落成 board，`board.<lane>.filter`
// 炸掉整板、旧快照已换掉、「重试」拉回同一份体只会再炸。api 层 mock 掉，零真实网络。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, fetchLanes } from "../../api";
import { getState, refreshBoard, resetStoreForTests, setLanguage } from "../../store";
import type { Board } from "../../types";
import { BoardLanes } from "./BoardLanes";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchLanes: vi.fn(), fetchCard: vi.fn() };
});

const GOOD: Board = {
  generated_at: "2026-09-05T12:00:00Z",
  counts: { debt: 1 },
  needs_approval: [],
  running: [],
  needs_input: [],
  review: [],
  completed: [],
  debt: [{ id: "R-001", title: "one backlog card", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] }],
  trash: [],
};

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  setLanguage("en");
  vi.mocked(fetchBoard).mockReset();
  vi.mocked(fetchLanes).mockResolvedValue({ lanes: [] });
});

afterEach(cleanup);

describe("BoardLanes · lane-less snapshot", () => {
  it("renders the lanes (empty) after a good board is followed by an object with generated_at + counts only", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(GOOD);
    await refreshBoard();
    vi.mocked(fetchBoard).mockResolvedValue({ generated_at: "2026-09-05T12:01:00Z", counts: {} } as unknown as Board);
    await refreshBoard();
    expect(getState().boardDecodeError).toBeNull();

    const { container } = render(<BoardLanes />);
    expect(container.querySelectorAll(".board-column").length).toBeGreaterThan(0);
    expect(screen.queryByText("one backlog card")).toBeNull(); // 新版说潜在任务空了，就照新版渲染
    expect(screen.getByText("Machine-filed items park here — nothing runs on its own, nothing expires; “Run it” starts one")).toBeTruthy();
  });

  it("renders when counts is missing and a lane is the wrong type (badge falls back to lane length)", async () => {
    vi.mocked(fetchBoard).mockResolvedValue({
      generated_at: "2026-09-05T12:02:00Z",
      debt: "corrupt",
      running: [{ id: "R-009", name: "still running", state: "working" }],
    } as unknown as Board);
    await refreshBoard();
    const { container } = render(<BoardLanes />);
    expect(container.querySelectorAll(".board-column").length).toBeGreaterThan(0);
    expect(screen.getByText("still running")).toBeTruthy();
  });
});
