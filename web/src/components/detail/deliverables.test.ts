// 交付物发现启发式的行为测试。
import { describe, expect, it } from "vitest";
import { deliverableKind, extractDeliverables, looksLikeHtml } from "./deliverables";
import type { CardDetail } from "../../types";

describe("extractDeliverables", () => {
  it("finds /deliverables/ basenames across nested string fields, deduped in order", () => {
    const detail = {
      id: "R-9",
      delivered_summary: "成品见 /Users/z/wb/deliverables/report.html，数据在 /Users/z/wb/deliverables/data.csv。",
      execution: { note: "again /Users/z/wb/deliverables/report.html" },
    } as CardDetail;
    expect(extractDeliverables(detail)).toEqual([
      { name: "report.html", kind: "html" },
      { name: "data.csv", kind: "text" },
    ]);
  });

  it("strips trailing prose punctuation (Chinese and ASCII)", () => {
    const detail = { id: "R-9", summary: "见 ~/wb/deliverables/page.html)。" } as CardDetail;
    expect(extractDeliverables(detail)).toEqual([{ name: "page.html", kind: "html" }]);
  });

  it("rejects dotfiles, traversal-ish names and subdirectory refs (mirrors server _validate_name)", () => {
    const detail = {
      id: "R-9",
      a: "x /deliverables/.hidden y",
      b: "x /deliverables/sub/inner.html y",
      c: "x /deliverables/ok.md y",
    } as CardDetail;
    // sub/inner.html 只留 basename 规则拒收（含 /），.hidden 拒收
    expect(extractDeliverables(detail).map((r) => r.name)).toEqual(["ok.md"]);
  });

  it("classifies kinds by extension", () => {
    expect(deliverableKind("a.htm")).toBe("html");
    expect(deliverableKind("a.markdown")).toBe("markdown");
    expect(deliverableKind("a.png")).toBe("image");
    expect(deliverableKind("a.pdf")).toBe("other");
  });
});

describe("looksLikeHtml", () => {
  it("detects harvest-backfilled full-page html final drafts (§33)", () => {
    expect(looksLikeHtml("<!DOCTYPE html><html></html>")).toBe(true);
    expect(looksLikeHtml("  <html lang=\"zh\">")).toBe(true);
    expect(looksLikeHtml("# markdown heading")).toBe(false);
    expect(looksLikeHtml(null)).toBe(false);
  });
});
