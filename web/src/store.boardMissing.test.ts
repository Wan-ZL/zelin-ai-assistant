// store.refreshBoard 的「dashboard.json 不存在」分类（CONTRACT §49 / §54.1 追记）：`GET /api/board` 404 `NOT_FOUND`
// = server 在、文件不在 → boardMissing:true、boardError:null、快照清空（原生 Store.refresh 缺文件分支：dashboard=nil /
// missing=true / loadError=nil）；网络 / 5xx 仍走 boardError（离线）；成功清 boardMissing。经 vi.mock 替换 fetchBoard，零真实网络。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard } from "./api";
import { getState, isBoardMissingError, refreshBoard, resetStoreForTests } from "./store";
import type { Board } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchBoard: vi.fn() };
});

const BOARD: Board = {
  generated_at: "2026-09-05T12:00:00Z",
  counts: {},
  needs_approval: [],
  running: [],
  needs_input: [],
  review: [],
  completed: [],
  debt: [],
  trash: [],
};

/** server/board_source.py 对缺席文件抛的 envelope 原形（NotFoundError → 404 NOT_FOUND） */
const notFound = () => new ApiError(404, {
  error: { code: "NOT_FOUND", message: "dashboard.json not found — is actd (or the demo seeder) pointed at this AIASSISTANT_HOME?" },
});

/** api.ts 对 fetch 抛错合成的读失败（status 0 / READ_FAILED） */
const readFailed = () => new ApiError(0, { error: { code: "READ_FAILED", message: "Board data is temporarily unavailable." } });

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchBoard).mockReset();
});

describe("isBoardMissingError", () => {
  it("404 / NOT_FOUND 算文件缺席；网络读失败、5xx、非 ApiError 都不算", () => {
    expect(isBoardMissingError(notFound())).toBe(true);
    expect(isBoardMissingError(new ApiError(404, {}))).toBe(true);
    expect(isBoardMissingError(readFailed())).toBe(false);
    expect(isBoardMissingError(new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "boom" } }))).toBe(false);
    expect(isBoardMissingError(new Error("connection refused"))).toBe(false);
  });
});

describe("refreshBoard · boardMissing", () => {
  it("初值：不缺席、无错、首载中", () => {
    expect(getState().boardMissing).toBe(false);
    expect(getState().boardError).toBeNull();
    expect(getState().boardLoading).toBe(true);
  });

  it("404 → boardMissing:true、boardError:null、boardLoading:false（不是离线）", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(notFound());
    await refreshBoard();
    const s = getState();
    expect(s.boardMissing).toBe(true);
    expect(s.boardError).toBeNull();
    expect(s.boardLoading).toBe(false);
    expect(s.board).toBeNull();
  });

  it("网络读失败 → boardError 有话、boardMissing:false（离线文案归 ErrorBanner / 整页空态）", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(readFailed());
    await refreshBoard();
    const s = getState();
    expect(s.boardMissing).toBe(false);
    expect(s.boardError).toBe("Board data is temporarily unavailable.");
    expect(s.boardLoading).toBe(false);
  });

  it("有快照后文件被删（404）：快照一并清——server 明说没了，不留旧板 + 离线横幅两句谎话", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    await refreshBoard();
    expect(getState().board).toEqual(BOARD);

    vi.mocked(fetchBoard).mockRejectedValue(notFound());
    await refreshBoard();
    const s = getState();
    expect(s.board).toBeNull();
    expect(s.boardMissing).toBe(true);
    expect(s.boardError).toBeNull();
  });

  it("有快照后网络断：旧快照留着、boardError 有话（既有降级语义不变）", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    await refreshBoard();
    vi.mocked(fetchBoard).mockRejectedValue(readFailed());
    await refreshBoard();
    const s = getState();
    expect(s.board).toEqual(BOARD);
    expect(s.boardMissing).toBe(false);
    expect(s.boardError).not.toBeNull();
  });

  it("文件出现后成功 → boardMissing 复位、boardError 仍 null、快照落地", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(notFound());
    await refreshBoard();
    expect(getState().boardMissing).toBe(true);

    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    await refreshBoard();
    const s = getState();
    expect(s.boardMissing).toBe(false);
    expect(s.boardError).toBeNull();
    expect(s.board).toEqual(BOARD);
  });

  it("404 之后网络断：两态互斥——boardError 有话、boardMissing 回 false（离线时不知道文件在不在）", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(notFound());
    await refreshBoard();
    vi.mocked(fetchBoard).mockRejectedValue(readFailed());
    await refreshBoard();
    const s = getState();
    expect(s.boardMissing).toBe(false);
    expect(s.boardError).not.toBeNull();
  });
});
