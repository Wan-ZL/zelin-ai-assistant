// 卡片排序判例（镜像 Store.swift sortCards）：三种模式、P-/R- 混排按数字后缀、
// 不可解析 id 沉底保序、同后缀稳定、deadline 模式无 deadline 字段退化为 newest、
// 偏好名 cardSortOrder 读写 + 未知值回落。
import { beforeEach, describe, expect, it } from "vitest";
import {
  DEFAULT_SORT_ORDER,
  idSuffix,
  normalizeSortOrder,
  readSortOrder,
  SORT_STORAGE_KEY,
  sortCards,
  writeSortOrder,
} from "./cardSort";

const ids = (rows: { id: string }[]) => rows.map((r) => r.id);

describe("idSuffix", () => {
  it("取尾部数字，不看前缀", () => {
    expect(idSuffix("R-013")).toBe(13);
    expect(idSuffix("P-201")).toBe(201);
    expect(idSuffix("MS-7")).toBe(7);
    expect(idSuffix("capture-abc")).toBeNull();
    expect(idSuffix("")).toBeNull();
  });
});

describe("sortCards", () => {
  const rows = [
    { id: "R-005" },
    { id: "capture-x" },
    { id: "P-201" },
    { id: "R-180" },
    { id: "capture-y" },
    { id: "P-190" },
  ];

  it("newest（默认）：数字后缀降序，P-/R- 同一把尺；不可解析沉底保序", () => {
    expect(ids(sortCards(rows, "newest"))).toEqual(["P-201", "P-190", "R-180", "R-005", "capture-x", "capture-y"]);
  });

  it("oldest：数字后缀升序；不可解析仍沉底保序", () => {
    expect(ids(sortCards(rows, "oldest"))).toEqual(["R-005", "R-180", "P-190", "P-201", "capture-x", "capture-y"]);
  });

  it("同后缀按原序（稳定）", () => {
    const dup = [{ id: "MS-7", tag: "a" }, { id: "R-7", tag: "b" }, { id: "P-7", tag: "c" }];
    expect(sortCards(dup, "newest").map((r) => r.tag)).toEqual(["a", "b", "c"]);
    expect(sortCards(dup, "oldest").map((r) => r.tag)).toEqual(["a", "b", "c"]);
  });

  it("deadline：有期限的先按日期升序，无期限的按 newest 跟在后面", () => {
    const dated = [
      { id: "R-1", deadline: null },
      { id: "R-2", deadline: "2026-09-20" },
      { id: "R-3", deadline: "" },
      { id: "R-4", deadline: "2026-09-02" },
      { id: "R-9" },
    ];
    expect(ids(sortCards(dated, "deadline", (r) => r.deadline))).toEqual(["R-4", "R-2", "R-9", "R-3", "R-1"]);
  });

  it("deadline 模式但行模型没有 deadline 取值函数 → 整列退化为 newest", () => {
    expect(ids(sortCards(rows, "deadline"))).toEqual(ids(sortCards(rows, "newest")));
  });

  it("不改输入数组", () => {
    const copy = [...rows];
    sortCards(rows, "newest");
    expect(rows).toEqual(copy);
  });
});

describe("sort preference persistence", () => {
  beforeEach(() => window.localStorage.clear());

  it("未知值回落 newest；键名逐字镜像原生 cardSortOrder", () => {
    expect(normalizeSortOrder("bogus")).toBe(DEFAULT_SORT_ORDER);
    expect(normalizeSortOrder(undefined)).toBe("newest");
    expect(SORT_STORAGE_KEY).toBe("cardSortOrder");
  });

  it("写后可读回；未写时读到默认", () => {
    expect(readSortOrder()).toBe("newest");
    writeSortOrder("deadline");
    expect(window.localStorage.getItem("cardSortOrder")).toBe("deadline");
    expect(readSortOrder()).toBe("deadline");
    window.localStorage.setItem("cardSortOrder", "garbage");
    expect(readSortOrder()).toBe("newest");
  });
});
