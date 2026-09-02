// steer.ts 纯函数行为测试（M6）：防御性解析 + 开放枚举降级 + 诚实计数。
import { describe, expect, it } from "vitest";
import {
  parseSteers,
  queuedReasonLabel,
  steerAcknowledged,
  steerStatusLabel,
  summarizeSteers,
} from "./steer";

const en = (_zh: string, english: string) => english;
const zh = (chinese: string) => chinese;

describe("parseSteers", () => {
  it("非数组 / 缺 ts 的项一律静默丢弃", () => {
    expect(parseSteers(undefined)).toEqual([]);
    expect(parseSteers("nope")).toEqual([]);
    expect(parseSteers([null, 42, { text: "no ts" }, ["ts"]])).toEqual([]);
  });

  it("合法项原样保留（含未知附加字段）", () => {
    const note = { ts: "2026-08-30T09:00:00Z", text: "走兼容层", status: "queued", extra: 1 };
    expect(parseSteers([note])).toEqual([note]);
  });
});

describe("summarizeSteers", () => {
  it("诚实三态计数；未知 status 按 queued 兜底（不谎报送达）", () => {
    const summary = summarizeSteers([
      { ts: "t1", status: "delivered" },
      { ts: "t2", status: "queued" },
      { ts: "t3", status: "dropped" },
      { ts: "t4", status: "future_state" },
      { ts: "t5" },
    ]);
    expect(summary).toEqual({ queued: 3, delivered: 1, dropped: 1 });
  });
});

describe("steerStatusLabel", () => {
  it("三态双语；未知 status 原样展示（wire add-only）", () => {
    expect(steerStatusLabel("delivered", en)).toBe("Delivered");
    expect(steerStatusLabel("dropped", zh)).toBe("未送达");
    expect(steerStatusLabel("queued", en)).toBe("Queued");
    expect(steerStatusLabel("future_state", en)).toBe("future_state");
    expect(steerStatusLabel(undefined, en)).toBe("Queued");
  });
});

describe("queuedReasonLabel", () => {
  it("waiting_card 带 blocking_id →「等 R-xx」", () => {
    expect(queuedReasonLabel({ kind: "waiting_card", blocking_id: "R-105" }, zh)).toBe("等 R-105");
    expect(queuedReasonLabel({ kind: "waiting_card", blocking_id: "R-105" }, en)).toBe("waiting on R-105");
    // §60：blocking_display_id（前置卡工作编号）优先于主键
    expect(queuedReasonLabel({ kind: "waiting_card", blocking_id: "P-105", blocking_display_id: "R-105" }, en)).toBe("waiting on R-105");
    expect(queuedReasonLabel({ kind: "waiting_card" }, en)).toBe("waiting on another card");
  });

  it("waiting_budget / budget 已退役（CONTRACT §51，D9）：无专属文案，按原文降级", () => {
    // 原判例钉「等预算 / waiting on budget」；预算天花板 retired v0.48.7 后这两个
    // 值只可能来自旧快照，走开放枚举的原文路径，绝不再翻译成「等预算」。
    expect(queuedReasonLabel({ kind: "waiting_budget" }, zh)).toBe("waiting_budget");
    expect(queuedReasonLabel("budget", en)).toBe("budget");
  });

  it("act/lib/policy.py 扁平 token 形（dependency/concurrency）同表翻译", () => {
    expect(queuedReasonLabel("dependency", en)).toBe("waiting on another card");
    expect(queuedReasonLabel("concurrency", en)).toBe("waiting on a run slot");
    expect(queuedReasonLabel({ kind: "concurrency" }, zh)).toBe("等并发位");
  });

  it("未知 kind：detail 优先，否则 kind 原样（开放枚举不崩渲染）", () => {
    expect(queuedReasonLabel({ kind: "cosmic_rays", detail: "宇宙射线" }, en)).toBe("宇宙射线");
    expect(queuedReasonLabel({ kind: "cosmic_rays" }, en)).toBe("cosmic_rays");
  });

  it("未知字符串原样透传；空/非法形状 → null（不渲染 chip）", () => {
    expect(queuedReasonLabel("等预算窗口", en)).toBe("等预算窗口");
    expect(queuedReasonLabel("   ", en)).toBeNull();
    expect(queuedReasonLabel(undefined, en)).toBeNull();
    expect(queuedReasonLabel([], en)).toBeNull();
    expect(queuedReasonLabel({ kind: "" }, en)).toBeNull();
  });
});

describe("steerAcknowledged", () => {
  it("只认 steer === true 的对象响应", () => {
    expect(steerAcknowledged({ ok: true, steer: true, steer_status: "queued" })).toBe(true);
    expect(steerAcknowledged({ ok: true })).toBe(false);
    expect(steerAcknowledged({ steer: "true" })).toBe(false);
    expect(steerAcknowledged(null)).toBe(false);
    expect(steerAcknowledged(undefined)).toBe(false);
  });
});
