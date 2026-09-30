// 判官的口径（CONTRACT §79.3）。这一份钉的是**不许假红**：
// 「运行中」一列装两个分区、`completed` 的 counts 是截断前的总数、省略号本身是设计、
// 中文只有在英文界面上才算漏翻译——每一条都来自看板真实的装配规则，抄错就是满屏噪音。
import { describe, expect, it } from "vitest";
import {
  BOARD_LANES, CAPPED_PARTITIONS, alertHits, budgetHits, consoleHits, crashHits, i18nHits,
  laneCountHits, laneIsCapped, overflowHits, parseBadgeTotal, projectionHits, type BoardData,
} from "./oracles";

const BOARD: BoardData = {
  counts: { needs_approval: 4, running: 3, needs_input: 1, review: 2, completed: 9, debt: 3, archived: 7 },
  lengths: { needs_approval: 4, running: 3, needs_input: 1, review: 2, completed: 2, debt: 3, archived: 2 },
};

describe("parseBadgeTotal", () => {
  it("纯数字就是总数", () => {
    expect(parseBadgeTotal("7")).toBe(7);
    expect(parseBadgeTotal(" 12 ")).toBe(12);
  });

  it("过滤生效时是「命中/总数」，取总数", () => {
    expect(parseBadgeTotal("3/7")).toBe(7);
  });

  it("认不出来就不判（宁可漏，不可假）", () => {
    expect(parseBadgeTotal("")).toBe(null);
    expect(parseBadgeTotal("无匹配卡片")).toBe(null);
    expect(parseBadgeTotal("7 张")).toBe(null);
  });
});

describe("laneCountHits", () => {
  it("四列都对上时一条不报", () => {
    const lanes = [
      { slug: "needs_approval", badge: "4", domCards: 4 },
      { slug: "running", badge: "4", domCards: 4 },
      { slug: "review", badge: "2", domCards: 2 },
      { slug: "completed", badge: "9", domCards: 2 },
    ];
    expect(laneCountHits(lanes, BOARD)).toEqual([]);
  });

  it("「运行中」一列的徽章是 running + needs_input 之和", () => {
    const hits = laneCountHits([{ slug: "running", badge: "3", domCards: 3 }], BOARD);
    expect(hits).toHaveLength(1);
    expect(hits[0].severity).toBe("error");
    expect(hits[0].summary).toContain("数据里是 4 张");
  });

  it("未截断的列差一个就报（#446 那一类）", () => {
    const hits = laneCountHits([{ slug: "review", badge: "1", domCards: 2 }], BOARD);
    expect(hits.map((hit) => hit.oracle)).toEqual(["lane_count"]);
  });

  it("截断的列「徽章比数组多」是正常的，不报", () => {
    expect(laneCountHits([{ slug: "completed", badge: "9", domCards: 2 }], BOARD)).toEqual([]);
  });

  it("截断的列「徽章比数组还少」才是真错", () => {
    const hits = laneCountHits([{ slug: "completed", badge: "1", domCards: 2 }], BOARD);
    expect(hits).toHaveLength(1);
    expect(hits[0].detail).toContain("只允许多不允许少");
  });

  it("书立条（debt / archived）与不认识的 slug 都不在判官范围内", () => {
    expect(laneCountHits([
      { slug: "debt", badge: "99", domCards: 3 },
      { slug: "archived", badge: "0", domCards: 2 },
      { slug: "unknown_lane", badge: "0", domCards: 0 },
    ], BOARD)).toEqual([]);
  });

  it("徽章读不出数字，或数据缺这个分区，就不判", () => {
    expect(laneCountHits([{ slug: "review", badge: "无匹配卡片", domCards: 0 }], BOARD)).toEqual([]);
    expect(laneCountHits([{ slug: "review", badge: "1", domCards: 0 }],
      { counts: {}, lengths: {} })).toEqual([]);
  });

  it("同一条发现跨跑次同一个签名（指纹稳得住）", () => {
    const first = laneCountHits([{ slug: "review", badge: "1", domCards: 2 }], BOARD);
    const second = laneCountHits([{ slug: "review", badge: "5", domCards: 2 }], BOARD);
    expect(first[0].signature).toBe(second[0].signature);
  });
});

describe("projectionHits", () => {
  it("counts 与数组长度一致时不报", () => {
    expect(projectionHits({ counts: { review: 2 }, lengths: { review: 2 } })).toEqual([]);
  });

  it("未截断的分区自相矛盾就报", () => {
    const hits = projectionHits({ counts: { review: 5 }, lengths: { review: 2 } });
    expect(hits).toHaveLength(1);
    expect(hits[0].summary).toContain("counts 说 5");
  });

  it("completed / archived 的 counts 是截断前总数，多出来不报", () => {
    expect(projectionHits({
      counts: { completed: 50, archived: 30 },
      lengths: { completed: 2, archived: 2 },
    })).toEqual([]);
  });

  it("completed 自报比实际还少 —— 这是真错", () => {
    expect(projectionHits({ counts: { completed: 1 }, lengths: { completed: 2 } })).toHaveLength(1);
  });

  it("counts 里没有的分区不判", () => {
    expect(projectionHits({ counts: {}, lengths: { review: 2 } })).toEqual([]);
  });

  it("真 demo 数据（initial 场景）一条不报", () => {
    expect(projectionHits(BOARD)).toEqual([]);
  });
});

describe("i18n", () => {
  const strings = [{ where: ".rail span", text: "设置" }, { where: ".rail span", text: "Settings" }];

  it("中文界面下什么都不报", () => {
    expect(i18nHits(strings, "zh")).toEqual([]);
  });

  it("英文界面下的中文 chrome 文案才报", () => {
    const hits = i18nHits(strings, "en");
    expect(hits).toHaveLength(1);
    expect(hits[0].severity).toBe("warn");
    expect(hits[0].summary).toContain("设置");
  });

  it("空串跳过", () => {
    expect(i18nHits([{ where: ".rail span", text: "   " }], "en")).toEqual([]);
  });
});

describe("overflow", () => {
  it("刚好放得下不报；1px 容差吃掉亚像素", () => {
    expect(overflowHits([{ where: ".rail-label", text: "Settings", scrollWidth: 120, clientWidth: 120 }])).toEqual([]);
    expect(overflowHits([{ where: ".rail-label", text: "Settings", scrollWidth: 121, clientWidth: 120 }])).toEqual([]);
  });

  it("真的被裁了才报", () => {
    const hits = overflowHits([
      { where: ".rail-label", text: "Recording & Data Sources", scrollWidth: 190, clientWidth: 120 },
    ]);
    expect(hits).toHaveLength(1);
    expect(hits[0].oracle).toBe("overflow");
  });

  it("量不到宽度（clientWidth 0 = 没在布局里）就不判", () => {
    expect(overflowHits([{ where: ".x", text: "y", scrollWidth: 300, clientWidth: 0 }])).toEqual([]);
  });
});

describe("crash / console / alert / budget", () => {
  it("没崩就没这条；崩了是 error", () => {
    expect(crashHits(false)).toEqual([]);
    expect(crashHits(true)[0].severity).toBe("error");
  });

  it("每条 console 报错一条发现，原文进 detail", () => {
    const hits = consoleHits(["console: boom", "pageerror: x is not a function"]);
    expect(hits.map((hit) => hit.severity)).toEqual(["error", "error"]);
    expect(hits[1].detail).toBe("pageerror: x is not a function");
  });

  it("空白的报警面不算「在朝用户喊」", () => {
    expect(alertHits([{ where: ".card-error", text: "  " }])).toEqual([]);
    expect(alertHits([{ where: ".card-error", text: "动作失败" }])[0].severity).toBe("warn");
  });

  it("超预算的步才报，刚好等于预算不报", () => {
    const timings = [{ step: 1, action: "click e1", ms: 900 }, { step: 2, action: "click e2", ms: 4000 }];
    const hits = budgetHits(timings, 900);
    expect(hits).toHaveLength(1);
    expect(hits[0].summary).toContain("第 2 步");
    // 跨步判官必须自带步号，否则整趟的超预算全记到同一步上
    expect(hits[0].step).toBe(2);
  });
});

it("四列的装配规则与看板一致（running 装两个分区、completed 截断）", () => {
  expect(BOARD_LANES.map((lane) => lane.slug)).toEqual(["needs_approval", "running", "review", "completed"]);
  expect(BOARD_LANES.find((lane) => lane.slug === "running")?.dataKeys).toEqual(["running", "needs_input"]);
  expect(BOARD_LANES.filter(laneIsCapped).map((lane) => lane.slug)).toEqual(["completed"]);
  // 「哪些分区会截断」只有一处；两只判官都从它派生，不各写一份
  expect([...CAPPED_PARTITIONS].sort()).toEqual(["archived", "completed"]);
});
