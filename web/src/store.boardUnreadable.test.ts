// store.refreshBoard 的第四态「文件在、server 读不动」（CONTRACT §49 追记 2026-09-18，issue #423）：
// `GET /api/board` 答 503 BOARD_UNREADABLE → **旧快照留着**（与 404 相反：文件在，上一版看板仍是管线写过什么的真话）、
// boardUnreadable = 「看板文件读不出来: <server 原句，含 errno>」。绝不落 boardError——它的读者全把它读成「离线」，
// 落进去等于对着一台刚答过话的 server 说连不上，还会把 PipelineBanner / DiagnosticsStrip / MaintenanceBanner /
// SelfImproveBanner 四条横幅一并关掉。四态互斥：离线 / 缺文件 / 解不出来 / 读不动，各清另三个。
// 经 vi.mock 替换 fetchBoard，零真实网络。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard } from "./api";
import { getState, isBoardUnreadableError, refreshBoard, resetStoreForTests, setLanguage } from "./store";
import type { Board } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchCard: vi.fn() };
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

/** server/board_source.board_bytes 的 503 原形（errno 在 details 里） */
const unreadable = () => new ApiError(503, {
  error: {
    code: "BOARD_UNREADABLE",
    message: "cannot read dashboard.json — see details.errno",
    details: { path: "/Volumes/Storage/.../state/dashboard.json", errno: 1, strerror: "Operation not permitted" },
  },
});
/** 非 2xx 的非 JSON 体：api.request 用通用码兜（envelope 自己都解不出来时的退化） */
const unreadableWithBrokenEnvelope = () => new ApiError(503, {});
/** §68.7 的另一枚 503——语义完全不同，绝不许被说成「看板读不动」 */
const shellUnavailable = () => new ApiError(503, {
  error: { code: "SHELL_UNAVAILABLE", message: "the board app is not running" },
});
const notFound = () => new ApiError(404, { error: { code: "NOT_FOUND", message: "dashboard.json not found" } });
const readFailed = () => new ApiError(0, { error: { code: "READ_FAILED", message: "Board data is temporarily unavailable." } });
const invalidJson = () => new ApiError(200, { error: { code: "READ_FAILED", message: "not valid JSON (200)" } });

beforeEach(() => {
  resetStoreForTests();
  setLanguage("en");
  vi.mocked(fetchBoard).mockReset();
});

describe("isBoardUnreadableError", () => {
  it("is a 503 BOARD_UNREADABLE — and nothing else", () => {
    expect(isBoardUnreadableError(unreadable())).toBe(true);
    expect(isBoardUnreadableError(notFound())).toBe(false);
    expect(isBoardUnreadableError(readFailed())).toBe(false);
    expect(isBoardUnreadableError(invalidJson())).toBe(false);
    expect(isBoardUnreadableError(new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "boom" } }))).toBe(false);
    expect(isBoardUnreadableError(new Error("x"))).toBe(false);
  });

  it("does NOT claim the board is unreadable for the surface's other 503 (SHELL_UNAVAILABLE, §68.7)", () => {
    // 裸 `status === 503` 会把「壳没在跑」说成「看板读不动」——正是本追记要修的那类谎话
    expect(isBoardUnreadableError(shellUnavailable())).toBe(false);
  });

  it("does NOT claim it for a 503 with no envelope at all (proxy / half-written response)", async () => {
    // 那种 503 连 errno 都没有，说「看板文件读不出来」同样是没查就断言；
    // 它继续落 boardError——对一个网关 503，「连不上」才是更近的真话
    expect(isBoardUnreadableError(unreadableWithBrokenEnvelope())).toBe(false);
    vi.mocked(fetchBoard).mockRejectedValueOnce(unreadableWithBrokenEnvelope());
    await refreshBoard();
    const s = getState();
    expect(s.boardUnreadable).toBeNull();
    expect(s.boardError).not.toBeNull();
  });
});

describe("refreshBoard on a 503 BOARD_UNREADABLE", () => {
  it("keeps the last good snapshot and says the file can't be read — not 'offline', not 'missing'", async () => {
    vi.mocked(fetchBoard).mockResolvedValueOnce(BOARD);
    await refreshBoard();
    expect(getState().board).not.toBeNull();

    vi.mocked(fetchBoard).mockRejectedValueOnce(unreadable());
    await refreshBoard();
    const s = getState();
    expect(s.board).toEqual(BOARD);                       // 快照不清（文件在）
    expect(s.boardUnreadable).toContain("The board file can't be read");
    expect(s.boardUnreadable).toContain("errno");         // server 原句带着 errno
    expect(s.boardError).toBeNull();                      // 四条横幅不许因此闭嘴
    expect(s.boardMissing).toBe(false);
    expect(s.boardDecodeError).toBeNull();
    expect(s.boardLoading).toBe(false);
  });

  it("reports it on a cold start too (no snapshot yet)", async () => {
    vi.mocked(fetchBoard).mockRejectedValueOnce(unreadable());
    await refreshBoard();
    const s = getState();
    expect(s.board).toBeNull();
    expect(s.boardUnreadable).not.toBeNull();
    expect(s.boardError).toBeNull();
    expect(s.boardLoading).toBe(false);
  });

  it("uses the zh copy under the zh language", async () => {
    setLanguage("zh");
    vi.mocked(fetchBoard).mockRejectedValueOnce(unreadable());
    await refreshBoard();
    expect(getState().boardUnreadable).toContain("看板文件读不出来");
  });
});

describe("the four board states are mutually exclusive — one sentence at a time", () => {
  // 每一次转移都**从 boardUnreadable 已置位的状态出发**：走一条 unreadable→A→unreadable→B… 的链，
  // 顺着走一遍（unreadable→missing→offline→decode→success）会让后两步的 `toBeNull()` 在入口就已经是
  // null，那两条断言永远不可能红——删掉对应的清理代码照样全绿（本轮 review 实测）。
  async function enterUnreadable() {
    vi.mocked(fetchBoard).mockRejectedValueOnce(unreadable());
    await refreshBoard();
    expect(getState().boardUnreadable).not.toBeNull();
  }

  it("404 entered from unreadable clears boardUnreadable (and blanks the snapshot)", async () => {
    await enterUnreadable();
    vi.mocked(fetchBoard).mockRejectedValueOnce(notFound());
    await refreshBoard();
    const s = getState();
    expect(s.boardMissing).toBe(true);
    expect(s.boardUnreadable).toBeNull();
    expect(s.boardError).toBeNull();
    expect(s.boardDecodeError).toBeNull();
  });

  it("going offline from unreadable clears boardUnreadable", async () => {
    await enterUnreadable();
    vi.mocked(fetchBoard).mockRejectedValueOnce(readFailed());
    await refreshBoard();
    const s = getState();
    expect(s.boardError).not.toBeNull();
    expect(s.boardUnreadable).toBeNull();
    expect(s.boardMissing).toBe(false);
    expect(s.boardDecodeError).toBeNull();
  });

  it("a decode failure entered from unreadable clears boardUnreadable", async () => {
    await enterUnreadable();
    vi.mocked(fetchBoard).mockRejectedValueOnce(invalidJson());
    await refreshBoard();
    const s = getState();
    expect(s.boardDecodeError).not.toBeNull();
    expect(s.boardUnreadable).toBeNull();
    expect(s.boardError).toBeNull();
    expect(s.boardMissing).toBe(false);
  });

  it("a successful read entered from unreadable clears boardUnreadable", async () => {
    await enterUnreadable();
    vi.mocked(fetchBoard).mockResolvedValueOnce(BOARD);
    await refreshBoard();
    const s = getState();
    expect(s.board).toEqual(BOARD);
    expect([s.boardUnreadable, s.boardError, s.boardDecodeError]).toEqual([null, null, null]);
    expect(s.boardMissing).toBe(false);
  });

  it("a 503 after a 404 replaces the sentence and does not resurrect the cleared snapshot", async () => {
    vi.mocked(fetchBoard).mockResolvedValueOnce(BOARD);
    await refreshBoard();
    vi.mocked(fetchBoard).mockRejectedValueOnce(notFound());
    await refreshBoard();
    expect(getState().board).toBeNull();                  // 404 清快照（既有判例）
    vi.mocked(fetchBoard).mockRejectedValueOnce(unreadable());
    await refreshBoard();
    const s = getState();
    expect(s.board).toBeNull();                           // 没有快照可留就是没有
    expect(s.boardMissing).toBe(false);
    expect(s.boardUnreadable).not.toBeNull();
  });
});
