// store.normalizeBoardShape 的列级宽容（CONTRACT §49 追记 `store-resilience-drawer`；原生 `Dashboard.init(from:)` /
// `decodeLossyRows`，shared/Sources/Contract.swift：缺列或整列不是数组 → `[]`，`counts` 不是对象 → `Counts.empty`）：
// 过了顶层门（对象 + 字符串 generated_at）的合法 JSON 若少一列，此前 refreshBoard 原样落成 board，`BoardLanes` 取
// `board.needs_approval.filter` 即 TypeError——旧快照那时已换掉、错误边界的「重试」拉回同一份体只会再炸。自此落地前补齐：
// 七个必有列缺席 / 坏类型 → `[]`；可选列在场却不是数组 → `[]`（缺席不补——wire 镜像里不塞 server 没说的键）；坏 counts → `{}`。
// 一切正常时原样返回（同一引用）。经 vi.mock 替换 fetchBoard，零真实网络。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "./api";
import { getState, normalizeBoardShape, refreshBoard, resetStoreForTests, setLanguage } from "./store";
import type { Board } from "./types";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchBoard: vi.fn(), fetchCard: vi.fn() };
});

const LANES = ["needs_approval", "running", "needs_input", "review", "completed", "debt", "trash"] as const;

const BOARD: Board = {
  generated_at: "2026-09-05T12:00:00Z",
  counts: { needs_approval: 1 },
  needs_approval: [{ id: "R-001", title: "one", tier: "T1", show_cost: false, processing: false, sources: [], plan: [], dod: [] }],
  running: [],
  needs_input: [],
  review: [],
  completed: [],
  debt: [],
  trash: [],
};

const asBoard = (value: unknown) => value as Board;

beforeEach(() => {
  resetStoreForTests();
  setLanguage("en");
  vi.mocked(fetchBoard).mockReset();
});

describe("normalizeBoardShape", () => {
  it("a well-formed board comes back untouched — the very same reference", () => {
    expect(normalizeBoardShape(BOARD)).toBe(BOARD);
    const withOptional = { ...BOARD, archived: [], merge_suggestions: [], fold_receipts: [], recaps: [] };
    expect(normalizeBoardShape(withOptional)).toBe(withOptional);
  });

  it("a lane-less object gets all seven lanes as [] (native decodeLossyRows on a missing key); generated_at / counts / extra keys survive", () => {
    const out = normalizeBoardShape(asBoard({ generated_at: "x", counts: { running: 2 }, brand_new_key: 1 }));
    for (const lane of LANES) expect(out[lane]).toEqual([]);
    expect(out.generated_at).toBe("x");
    expect(out.counts).toEqual({ running: 2 });
    expect(out.brand_new_key).toBe(1);
  });

  it("a lane that is present but not an array (string / object / null) becomes [] — the good lanes keep their rows", () => {
    const out = normalizeBoardShape(asBoard({ ...BOARD, running: "broken", review: { a: 1 }, completed: null }));
    expect(out.running).toEqual([]);
    expect(out.review).toEqual([]);
    expect(out.completed).toEqual([]);
    expect(out.needs_approval).toBe(BOARD.needs_approval);
  });

  it("counts missing / null / array → {} (native Counts.empty); a proper counts object is kept", () => {
    expect(normalizeBoardShape(asBoard({ ...BOARD, counts: undefined })).counts).toEqual({});
    expect(normalizeBoardShape(asBoard({ ...BOARD, counts: null })).counts).toEqual({});
    expect(normalizeBoardShape(asBoard({ ...BOARD, counts: [1, 2] })).counts).toEqual({});
    expect(normalizeBoardShape(BOARD).counts).toBe(BOARD.counts);
  });

  it("optional lists (archived / merge_suggestions / fold_receipts / recaps): absent stays absent, present-but-not-array → []", () => {
    const absent = normalizeBoardShape(BOARD);
    expect("archived" in absent).toBe(false);
    expect("merge_suggestions" in absent).toBe(false);
    const broken = normalizeBoardShape(asBoard({ ...BOARD, archived: "x", merge_suggestions: 3, fold_receipts: {}, recaps: null }));
    expect(broken.archived).toEqual([]);
    expect(broken.merge_suggestions).toEqual([]);
    expect(broken.fold_receipts).toEqual([]);
    expect(broken.recaps).toEqual([]);
  });

  it("never mutates its input", () => {
    const input = asBoard({ generated_at: "x", counts: {} });
    normalizeBoardShape(input);
    expect(input).toEqual({ generated_at: "x", counts: {} });
  });
});

describe("refreshBoard · lane coercion", () => {
  it("a lane-less object after a good board lands as a renderable snapshot (lanes [], no boardDecodeError) instead of the raw body", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    await refreshBoard();
    vi.mocked(fetchBoard).mockResolvedValue(asBoard({ generated_at: "2026-09-05T12:01:00Z", counts: {} }));
    await refreshBoard();
    const s = getState();
    expect(s.boardDecodeError).toBeNull();
    expect(s.boardError).toBeNull();
    expect(s.board?.generated_at).toBe("2026-09-05T12:01:00Z");
    for (const lane of LANES) expect(s.board?.[lane]).toEqual([]);
    expect(s.board?.counts).toEqual({});
  });

  it("a well-formed board lands as-is (same reference the api handed over)", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(BOARD);
    await refreshBoard();
    expect(getState().board).toBe(BOARD);
  });
});
