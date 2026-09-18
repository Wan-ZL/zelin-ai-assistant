// store.refreshBoard 的第四态「读不到 dashboard.json、而且不是因为它不在」（CONTRACT §49 追记 2026-09-18，issue #423）：
// `GET /api/board` 503 `BOARD_UNREADABLE` → boardUnreadable 有话（带 errno）、**旧快照留着**、boardError:null
// （它一置位四条横幅集体闭嘴，而这一态恰恰要让健康面说话）、boardMissing:false、boardDecodeError:null。
// 别的 5xx 仍走离线分支——判据是 status **且** code，不是「5xx 一律」。经 vi.mock 替换 fetchBoard，零真实网络。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard } from "./api";
import {
  getState,
  isBoardMissingError,
  isBoardUnreadableError,
  refreshBoard,
  resetStoreForTests,
  setLanguage,
} from "./store";
import type { Board } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchBoard: vi.fn() };
});

const BOARD: Board = {
  generated_at: "2026-09-18T12:00:00Z",
  counts: {},
  needs_approval: [],
  running: [],
  needs_input: [],
  review: [],
  completed: [],
  debt: [],
  trash: [],
};

/** server/board_source.py 的 503 原句（逐字镜像，不是「文件在」——errno 证不出那个，§49 追记 2026-09-18） */
const SERVER_MESSAGE =
  "dashboard.json could not be read by this server process"
  + " (the read failed with an error other than 'no such file')";

/** server/board_source.py 对「读不了、而且不是因为它不在」抛的 envelope 原形（BoardUnreadableError → 503） */
const unreadable = (errno: number | null = 1, strerror: string | null = "Operation not permitted") =>
  new ApiError(503, {
    error: {
      code: "BOARD_UNREADABLE",
      message:
        "dashboard.json could not be read by this server process"
        + " (the read failed with an error other than 'no such file')",
      details: { path: "/h/state/dashboard.json", errno, strerror },
    },
  });

/** 缺席（404）与网络读失败（status 0）——用来证互斥 */
const notFound = () => new ApiError(404, {
  error: { code: "NOT_FOUND", message: "dashboard.json not found" },
});
const readFailed = () => new ApiError(0, {
  error: { code: "READ_FAILED", message: "Board data is temporarily unavailable." },
});

beforeEach(() => {
  resetStoreForTests();
  setLanguage("en");   // 这一行文案在 action 里生成，跟的是 store 的语言不是渲染上下文
  vi.mocked(fetchBoard).mockReset();
});

describe("isBoardUnreadableError", () => {
  it("只认 503 + BOARD_UNREADABLE；别的 5xx / 404 / 网络失败都不算", () => {
    expect(isBoardUnreadableError(unreadable())).toBe(true);
    // 「5xx → 连不上」这条既有前提一字不动：判据不是 status >= 500
    expect(isBoardUnreadableError(new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "boom" } }))).toBe(false);
    expect(isBoardUnreadableError(new ApiError(503, { error: { code: "SHELL_UNAVAILABLE", message: "no shell" } }))).toBe(false);
    expect(isBoardUnreadableError(notFound())).toBe(false);
    expect(isBoardUnreadableError(readFailed())).toBe(false);
    expect(isBoardUnreadableError(new Error("connection refused"))).toBe(false);
  });

  it("503 不是「文件不在」——两条判据不重叠（否则又回到 issue #423 那句谎）", () => {
    expect(isBoardMissingError(unreadable())).toBe(false);
  });
});

describe("refreshBoard · boardUnreadable", () => {
  it("初值：null", () => {
    expect(getState().boardUnreadable).toBeNull();
  });

  it("503 → 一行带 errno 的实话，boardError 仍是 null（健康横幅不许被它闭麦）", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(unreadable());
    await refreshBoard();
    const s = getState();
    expect(s.boardUnreadable).toBe(
      "Can't read dashboard.json: " + SERVER_MESSAGE + " (errno 1 Operation not permitted)",
    );
    expect(s.boardError).toBeNull();
    expect(s.boardMissing).toBe(false);
    expect(s.boardDecodeError).toBeNull();
    expect(s.boardLoading).toBe(false);
  });

  it("errno 缺席时只说 server 那句话，不编造括号", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(unreadable(null, null));
    await refreshBoard();
    expect(getState().boardUnreadable).toBe("Can't read dashboard.json: " + SERVER_MESSAGE);
  });

  it("有快照后读不了：旧快照**留着**（一块 425 KB 的旧板仍是眼下最好的真相）", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    await refreshBoard();
    vi.mocked(fetchBoard).mockRejectedValue(unreadable());
    await refreshBoard();
    const s = getState();
    expect(s.board).toEqual(BOARD);
    expect(s.boardUnreadable).not.toBeNull();
    expect(s.boardError).toBeNull();
  });

  it("读权限回来后成功 → boardUnreadable 复位、快照落地", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(unreadable());
    await refreshBoard();
    expect(getState().boardUnreadable).not.toBeNull();

    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    await refreshBoard();
    const s = getState();
    expect(s.boardUnreadable).toBeNull();
    expect(s.board).toEqual(BOARD);
  });

  it("四态互斥：读不了 → 缺文件 → 离线，每一步只留一句话", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(unreadable());
    await refreshBoard();
    expect(getState().boardUnreadable).not.toBeNull();

    vi.mocked(fetchBoard).mockRejectedValue(notFound());
    await refreshBoard();
    let s = getState();
    expect(s.boardMissing).toBe(true);
    expect(s.boardUnreadable).toBeNull();
    expect(s.boardError).toBeNull();
    expect(s.boardDecodeError).toBeNull();

    vi.mocked(fetchBoard).mockRejectedValue(readFailed());
    await refreshBoard();
    s = getState();
    expect(s.boardError).not.toBeNull();
    expect(s.boardUnreadable).toBeNull();
    expect(s.boardMissing).toBe(false);
  });

  it("2xx 解不出来 → 读不了：decode 那句话被换掉，不叠着说两句", async () => {
    vi.mocked(fetchBoard).mockResolvedValue({ nope: true } as unknown as Board);
    await refreshBoard();
    expect(getState().boardDecodeError).not.toBeNull();

    vi.mocked(fetchBoard).mockRejectedValue(unreadable());
    await refreshBoard();
    const s = getState();
    expect(s.boardDecodeError).toBeNull();
    expect(s.boardUnreadable).not.toBeNull();
  });

  it("别的 5xx 仍是离线分支（BOARD_UNREADABLE 没有把整个 5xx 段挪走）", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(
      new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "internal error" } }),
    );
    await refreshBoard();
    const s = getState();
    expect(s.boardError).toBe("internal error");
    expect(s.boardUnreadable).toBeNull();
  });
});
