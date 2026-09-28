// 发现与指纹（CONTRACT §79.4）。指纹是**唯一**一处计算——TS 这边算好写进 report.json，
// scripts/qa/ui_scout.py 只读不重算。这一份钉的是「同一个 bug 跨跑次同一个指纹」：数字、
// 时间戳、路径、hex id 都得先抹平，否则每跑一次都是一条「新」发现，去重等于没有。
import { describe, expect, it } from "vitest";
import {
  countBySeverity, dedupe, fingerprint, normalizeSignature, shortHash, toFinding, worstSeverity,
  type Finding, type OracleHit,
} from "./findings";

const CTX = { journey: "board_tour", step: 3, url: "http://127.0.0.1:1/", screenshot: "board_tour/step-03.png" };

function hit(over: Partial<OracleHit> = {}): OracleHit {
  return {
    oracle: "lane_count", severity: "error", summary: "对不上", detail: "细节",
    signature: "lane badge review", ...over,
  };
}

describe("normalizeSignature", () => {
  it("数字抹成 <n>", () => {
    expect(normalizeSignature("列头写着 4，数据里是 7")).toBe("列头写着 <n>，数据里是 <n>");
  });

  it("ISO 时间戳抹成 <ts>", () => {
    expect(normalizeSignature("generated 2026-09-28T10:41:58Z")).toBe("generated <ts>");
  });

  it("长路径抹成 <path>", () => {
    expect(normalizeSignature("cannot read /Users/zelin/tmp/zai-abc/state/x.json"))
      .toBe("cannot read <path>");
  });

  it("hex id 抹成 <hex>", () => {
    expect(normalizeSignature("session deadbeefcafe failed")).toBe("session <hex> failed");
  });

  it("空白归一、大小写归一", () => {
    expect(normalizeSignature("  Lane   BADGE\treview ")).toBe("lane badge review");
  });
});

describe("fingerprint", () => {
  it("同一个 bug 跨跑次同一个指纹（数字漂移不算新 bug）", () => {
    expect(fingerprint("board_tour", "lane_count", "列头写着 4，数据里是 7"))
      .toBe(fingerprint("board_tour", "lane_count", "列头写着 5，数据里是 9"));
  });

  it("换一趟行程就是另一条发现", () => {
    expect(fingerprint("board_tour", "lane_count", "x")).not.toBe(fingerprint("rail_walk", "lane_count", "x"));
  });

  it("换一只判官也是另一条发现", () => {
    expect(fingerprint("board_tour", "lane_count", "x")).not.toBe(fingerprint("board_tour", "console", "x"));
  });

  it("是 8 位十六进制，稳定且可以当文件名", () => {
    const value = fingerprint("a", "b", "c");
    expect(value).toMatch(/^[0-9a-f]{8}$/);
    expect(value).toBe(fingerprint("a", "b", "c"));
  });

  it("shortHash 对空串也给得出 8 位", () => {
    expect(shortHash("")).toMatch(/^[0-9a-f]{8}$/);
  });
});

describe("toFinding", () => {
  it("把上下文补齐，指纹一并算好", () => {
    const finding = toFinding(hit(), CTX);
    expect(finding.journey).toBe("board_tour");
    expect(finding.step).toBe(3);
    expect(finding.screenshot).toBe("board_tour/step-03.png");
    expect(finding.fingerprint).toBe(fingerprint("board_tour", "lane_count", "lane badge review"));
  });

  it("跨步判官自带的 step 覆盖当前步（预算判官走完一趟才回头看时间表）", () => {
    expect(toFinding(hit({ step: 7 }), CTX).step).toBe(7);
    expect(toFinding(hit(), CTX).step).toBe(3);
  });
});

describe("dedupe / 严重度", () => {
  const findings: Finding[] = [
    toFinding(hit({ summary: "第一次" }), CTX),
    toFinding(hit({ summary: "第二次" }), { ...CTX, step: 7 }),
    toFinding(hit({ oracle: "overflow", severity: "warn", signature: "overflow .rail-label" }), CTX),
    toFinding(hit({ oracle: "pilot", severity: "info", signature: "pilot 看着怪" }), CTX),
  ];

  it("同指纹只留第一条（第一条带着最早的复现步骤）", () => {
    const kept = dedupe(findings);
    expect(kept).toHaveLength(3);
    expect(kept[0].summary).toBe("第一次");
    expect(kept[0].step).toBe(3);
  });

  it("worstSeverity 认 error > warn > info", () => {
    expect(worstSeverity(findings)).toBe("error");
    expect(worstSeverity(findings.slice(2))).toBe("warn");
    expect(worstSeverity(findings.slice(3))).toBe("info");
    expect(worstSeverity([])).toBe(null);
  });

  it("countBySeverity 三档都在，没有的是 0", () => {
    expect(countBySeverity(dedupe(findings))).toEqual({ error: 1, warn: 1, info: 1 });
    expect(countBySeverity([])).toEqual({ error: 0, warn: 0, info: 0 });
  });
});
