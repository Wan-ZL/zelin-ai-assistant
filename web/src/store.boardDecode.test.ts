// store.refreshBoard 的「dashboard.json 解不出来」分类（CONTRACT §49 追记 `store-resilience-drawer`）：server 答了 2xx 但
// 内容不是 JSON（api 合成 status 2xx 的 READ_FAILED）或顶层不是带字符串 generated_at 的对象 → **旧快照留着**、
// boardDecodeError = 「读取 dashboard.json 失败: <原因>」（原生 Store.swift:320-324「Keep the previously good dashboard rather
// than blanking the UI」+ `L("读取 dashboard.json 失败: ", …) + error.localizedDescription`）；与 boardError（断网）/
// boardMissing（404）互斥；成功清。经 vi.mock 替换 fetchBoard，零真实网络。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard } from "./api";
import { boardShapeProblem, getState, isBoardDecodeError, refreshBoard, resetStoreForTests, setLanguage } from "./store";
import type { Board } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchCard: vi.fn() };
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

/** api.request 对 2xx 非 JSON 体合成的错误原形（status 带真值） */
const invalidJson = () => new ApiError(200, {
  error: { code: "READ_FAILED", message: "The server response is not valid JSON (200)", details: { method: "GET", failure: "invalid-json" } },
});
/** api.request 对 fetch 抛错合成的读失败（status 0） */
const readFailed = () => new ApiError(0, { error: { code: "READ_FAILED", message: "Board data is temporarily unavailable." } });
const notFound = () => new ApiError(404, { error: { code: "NOT_FOUND", message: "dashboard.json not found" } });

beforeEach(() => {
  resetStoreForTests();
  setLanguage("en");
  vi.mocked(fetchBoard).mockReset();
});

describe("isBoardDecodeError", () => {
  it("READ_FAILED with a 2xx status is a decode failure; status-0 READ_FAILED, 404, 5xx and plain Errors are not", () => {
    expect(isBoardDecodeError(invalidJson())).toBe(true);
    expect(isBoardDecodeError(new ApiError(204, { error: { code: "READ_FAILED" } }))).toBe(true);
    expect(isBoardDecodeError(readFailed())).toBe(false);
    expect(isBoardDecodeError(notFound())).toBe(false);
    expect(isBoardDecodeError(new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "boom" } }))).toBe(false);
    expect(isBoardDecodeError(new Error("x"))).toBe(false);
  });
});

describe("boardShapeProblem", () => {
  it("accepts an object with a string generated_at (extra / missing lanes are the components' lenient business)", () => {
    expect(boardShapeProblem(BOARD)).toBeNull();
    expect(boardShapeProblem({ generated_at: "x" })).toBeNull();
    expect(boardShapeProblem({ generated_at: "x", brand_new_key: 1 })).toBeNull();
  });

  it("names the problem for non-objects and a missing / non-string generated_at", () => {
    expect(boardShapeProblem(null)).toBe("top level is not an object");
    expect(boardShapeProblem("str")).toBe("top level is not an object");
    expect(boardShapeProblem([1])).toBe("top level is not an object");
    expect(boardShapeProblem({})).toBe("generated_at is missing");
    expect(boardShapeProblem({ generated_at: 42 })).toBe("generated_at is missing");
  });

  it("speaks the store's UI language", () => {
    setLanguage("zh");
    expect(boardShapeProblem(null)).toBe("顶层不是对象");
    expect(boardShapeProblem({})).toBe("缺少 generated_at");
  });
});

describe("refreshBoard · boardDecodeError", () => {
  it("初值 null", () => {
    expect(getState().boardDecodeError).toBeNull();
  });

  it("有快照后 server 回了非 JSON：快照留着、boardDecodeError 是原生那一行、boardError / boardMissing 都 null", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    await refreshBoard();
    vi.mocked(fetchBoard).mockRejectedValue(invalidJson());
    await refreshBoard();
    const s = getState();
    expect(s.board).toEqual(BOARD);
    expect(s.boardDecodeError).toBe("Failed to read dashboard.json: The server response is not valid JSON (200)");
    expect(s.boardError).toBeNull();
    expect(s.boardMissing).toBe(false);
    expect(s.boardLoading).toBe(false);
  });

  it("有快照后 server 回了顶层形状不对的 JSON（`{}` / 数组 / null）：快照留着、说清原因", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    await refreshBoard();
    vi.mocked(fetchBoard).mockResolvedValue({} as unknown as Board);
    await refreshBoard();
    expect(getState().board).toEqual(BOARD);
    expect(getState().boardDecodeError).toBe("Failed to read dashboard.json: generated_at is missing");

    vi.mocked(fetchBoard).mockResolvedValue([] as unknown as Board);
    await refreshBoard();
    expect(getState().board).toEqual(BOARD);
    expect(getState().boardDecodeError).toBe("Failed to read dashboard.json: top level is not an object");

    vi.mocked(fetchBoard).mockResolvedValue(null as unknown as Board);
    await refreshBoard();
    expect(getState().board).toEqual(BOARD);
    expect(getState().boardDecodeError).toBe("Failed to read dashboard.json: top level is not an object");
  });

  it("从未有过快照时解不出来：board 仍 null、boardLoading 放下、boardDecodeError 有话（整页空态由 AppShell 说）", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(invalidJson());
    await refreshBoard();
    const s = getState();
    expect(s.board).toBeNull();
    expect(s.boardLoading).toBe(false);
    expect(s.boardDecodeError).not.toBeNull();
    expect(s.boardError).toBeNull();
    expect(s.boardMissing).toBe(false);
  });

  it("中文 UI：文案逐字原生 L(\"读取 dashboard.json 失败: \", …)", async () => {
    setLanguage("zh");
    vi.mocked(fetchBoard).mockResolvedValue({ counts: {} } as unknown as Board);
    await refreshBoard();
    expect(getState().boardDecodeError).toBe("读取 dashboard.json 失败: 缺少 generated_at");
  });

  it("下一版好快照落地 → boardDecodeError 清、快照替换", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(invalidJson());
    await refreshBoard();
    expect(getState().boardDecodeError).not.toBeNull();
    const next = { ...BOARD, generated_at: "2026-09-05T12:01:00Z" };
    vi.mocked(fetchBoard).mockResolvedValue(next);
    await refreshBoard();
    expect(getState().boardDecodeError).toBeNull();
    expect(getState().board).toEqual(next);
  });

  it("三态互斥：解码失败后断网 → boardError 有话、boardDecodeError 清；断网后解码失败 → 反过来", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    await refreshBoard();
    vi.mocked(fetchBoard).mockRejectedValue(invalidJson());
    await refreshBoard();
    vi.mocked(fetchBoard).mockRejectedValue(readFailed());
    await refreshBoard();
    expect(getState().boardError).toBe("Board data is temporarily unavailable.");
    expect(getState().boardDecodeError).toBeNull();
    expect(getState().board).toEqual(BOARD);

    vi.mocked(fetchBoard).mockRejectedValue(invalidJson());
    await refreshBoard();
    expect(getState().boardError).toBeNull();
    expect(getState().boardDecodeError).not.toBeNull();
    expect(getState().board).toEqual(BOARD);
  });

  it("解码失败后文件被删（404）：快照清、boardMissing:true、boardDecodeError 清（server 明说没了）", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    await refreshBoard();
    vi.mocked(fetchBoard).mockRejectedValue(invalidJson());
    await refreshBoard();
    vi.mocked(fetchBoard).mockRejectedValue(notFound());
    await refreshBoard();
    const s = getState();
    expect(s.board).toBeNull();
    expect(s.boardMissing).toBe(true);
    expect(s.boardDecodeError).toBeNull();
  });

  it("断网（status 0 READ_FAILED）仍是 boardError，不被误判成解码失败", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(readFailed());
    await refreshBoard();
    expect(getState().boardError).toBe("Board data is temporarily unavailable.");
    expect(getState().boardDecodeError).toBeNull();
  });
});
