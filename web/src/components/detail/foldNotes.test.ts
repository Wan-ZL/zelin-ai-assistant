// fold-note 行格式解析——与 act/lib/registry.py parse_fold_notes 判例对齐。
import { describe, expect, it } from "vitest";
import { parseFoldNotes } from "./foldNotes";

describe("parseFoldNotes", () => {
  it("parses kind, text, ts and split marker", () => {
    const { folds, rest } = parseFoldNotes(
      "[radar] 用户又提了导出需求 [@2026-08-01T09:00:00Z]\n"
      + "[quick] 手动补充一句 [@2026-08-02T10:00:00Z#2] [已拆出 R-207]\n"
      + "自由备注一行\n",
    );
    expect(folds).toEqual([
      { kind: "radar", text: "用户又提了导出需求", ts: "2026-08-01T09:00:00Z", splitInto: null },
      { kind: "quick", text: "手动补充一句", ts: "2026-08-02T10:00:00Z#2", splitInto: "R-207" },
    ]);
    expect(rest).toEqual(["自由备注一行"]);
  });

  it("keeps legacy untimestamped lines with ts null", () => {
    const { folds } = parseFoldNotes("[radar] 旧格式一行");
    expect(folds).toEqual([{ kind: "radar", text: "旧格式一行", ts: null, splitInto: null }]);
  });

  it("tolerates non-string input", () => {
    expect(parseFoldNotes(null)).toEqual({ folds: [], rest: [] });
    expect(parseFoldNotes(undefined)).toEqual({ folds: [], rest: [] });
  });
});
