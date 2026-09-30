// 报告（CONTRACT §79.5）：report.html 是人面，所以它必须自包含、必须转义、必须把复现步骤
// 端到人眼前。这一份钉的是「页面文本进 HTML 前一律转义」——巡检采的正是不可信的页面文本，
// 一条带尖括号的卡片标题不该把报告变成一个注入面。
import { describe, expect, it } from "vitest";
import { escapeHtml, renderReportHtml, summarize, type JourneyRecord, type ScoutReport } from "./report";
import { toFinding } from "./findings";

const CTX = { journey: "board_tour", step: 2, url: "http://127.0.0.1:1/", screenshot: "board_tour/step-02.png" };

function record(over: Partial<JourneyRecord> = {}): JourneyRecord {
  return {
    name: "board_tour", scene: "initial", lang: "zh", goal: "看一遍看板",
    viewport: { width: 1440, height: 900 }, outcome: "done", reached: true,
    steps: [
      { step: 1, action: "click e1", ok: true, error: null, ms: 120, pilotMs: 3, url: "u", page: "board", screenshot: null },
      { step: 2, action: "press Escape", ok: false, error: "炸了", ms: 90, pilotMs: 21_700, url: "u", page: "board", screenshot: "board_tour/step-02.png" },
    ],
    ...over,
  };
}

const FINDINGS = [
  toFinding({ oracle: "lane_count", severity: "error", summary: "对不上", detail: "d", signature: "s1" }, CTX),
  toFinding({ oracle: "overflow", severity: "warn", summary: "裁了", detail: "d", signature: "s2" }, CTX),
  toFinding({ oracle: "pilot", severity: "info", summary: "怪", detail: "d", signature: "s3" }, CTX),
];

describe("summarize", () => {
  it("趟数、步数与三档严重度各算一遍", () => {
    expect(summarize([record(), record({ name: "rail_walk" })], FINDINGS))
      .toEqual({ journeys: 2, steps: 4, error: 1, warn: 1, info: 1 });
  });

  it("空巡检也给得出一份 0", () => {
    expect(summarize([], [])).toEqual({ journeys: 0, steps: 0, error: 0, warn: 0, info: 0 });
  });
});

describe("escapeHtml", () => {
  it("五个危险字符全转", () => {
    expect(escapeHtml('<img src=x onerror="alert(1)">&'))
      .toBe("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;");
  });

  it("& 先转，不会把已转的实体再转一次", () => {
    expect(escapeHtml("a & <b>")).toBe("a &amp; &lt;b&gt;");
  });
});

describe("renderReportHtml", () => {
  function build(over: Partial<ScoutReport> = {}): ScoutReport {
    const journeys = [record()];
    return {
      protocol: 1, pilot: "scripted", startedAt: "2026-09-28T10:00:00.000Z", durationMs: 31_000,
      journeys, findings: FINDINGS, counts: summarize(journeys, FINDINGS), ...over,
    };
  }

  it("是一份自包含的 HTML（有 doctype、有内联样式、没有外链资源）", () => {
    const html = renderReportHtml(build());
    expect(html.startsWith("<!doctype html>")).toBe(true);
    expect(html).toContain("<style>");
    expect(html).not.toMatch(/<(script|link)\b/);
  });

  it("每一趟一节，带目标、结局与步骤表", () => {
    const html = renderReportHtml(build());
    expect(html).toContain("board_tour");
    expect(html).toContain("看一遍看板");
    expect(html).toContain("click e1");
    expect(html).toContain("press Escape");
  });

  it("发现按严重度上色，指纹写在旁边（去重时用）", () => {
    const html = renderReportHtml(build());
    expect(html).toContain('class="sev-error"');
    expect(html).toContain(FINDINGS[0].fingerprint);
  });

  it("截图是相对路径的 <img>，整个 run 目录可以原样拷走", () => {
    expect(renderReportHtml(build())).toContain('src="board_tour/step-02.png"');
  });

  it("界面耗时与驾驶员耗时分两栏——模型慢不等于界面慢（§79.3）", () => {
    const html = renderReportHtml(build());
    expect(html).toContain("界面耗时");
    expect(html).toContain("驾驶员想了多久");
    expect(html).toContain("21700ms");
  });

  it("页面文本进 HTML 前一律转义（采来的正是不可信文本）", () => {
    const nasty = toFinding({
      oracle: "console", severity: "error",
      summary: '<img src=x onerror="alert(1)">', detail: "<script>", signature: "s",
    }, CTX);
    const html = renderReportHtml(build({ findings: [nasty] }));
    expect(html).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
    expect(html).not.toContain("<img src=x");
    expect(html).not.toContain("<script>");
  });

  it("没有发现时也说人话，不是一张空表", () => {
    expect(renderReportHtml(build({ findings: [] }))).toContain("这一趟没有发现");
  });

  it("没走到目标时在报告里显眼地说出来", () => {
    const html = renderReportHtml(build({ journeys: [record({ reached: false })] }));
    expect(html).toContain("不成立");
  });
});
