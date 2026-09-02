// 相对时间 / 时长文案判例——档位与原生 RelativeTime（Utils.swift / Cards.swift）逐字对齐。
import { describe, expect, it } from "vitest";
import { absoluteLabel, duration, relativeAge, sinceEpoch, sinceIso } from "./relativeTime";

const zh = (c: string) => c;
const en = (_c: string, e: string) => e;
const NOW = Date.UTC(2026, 8, 1, 12, 0, 0); // 2026-09-01T12:00:00Z

describe("relativeAge", () => {
  it("四档：刚刚 / 分钟 / 小时 / 天（zh + en）", () => {
    expect(relativeAge(30, zh)).toBe("刚刚");
    expect(relativeAge(59 * 60, zh)).toBe("59分钟前");
    expect(relativeAge(3 * 3600 + 59 * 60, zh)).toBe("3小时前");
    expect(relativeAge(19 * 86400 + 3600, zh)).toBe("19天前");
    expect(relativeAge(30, en)).toBe("just now");
    expect(relativeAge(5 * 60, en)).toBe("5m ago");
    expect(relativeAge(2 * 3600, en)).toBe("2h ago");
    expect(relativeAge(19 * 86400, en)).toBe("19d ago");
  });
});

describe("sinceEpoch / sinceIso", () => {
  it("epoch 秒 → 相对；非正数、非数字 → null", () => {
    expect(sinceEpoch(NOW / 1000 - 19 * 86400, NOW, zh)).toBe("19天前");
    expect(sinceEpoch(0, NOW, zh)).toBeNull();
    expect(sinceEpoch(undefined, NOW, zh)).toBeNull();
    expect(sinceEpoch("1756500000", NOW, zh)).toBeNull();
  });

  it("未来时间戳夹到 刚刚（不出负数）", () => {
    expect(sinceEpoch(NOW / 1000 + 600, NOW, zh)).toBe("刚刚");
  });

  it("ISO → 相对；空/坏字串 → null", () => {
    expect(sinceIso("2026-08-28T12:00:00Z", NOW, zh)).toBe("4天前");
    expect(sinceIso("2026-09-01T11:20:00Z", NOW, en)).toBe("40m ago");
    expect(sinceIso("", NOW, zh)).toBeNull();
    expect(sinceIso("not-a-date", NOW, zh)).toBeNull();
    expect(sinceIso(42, NOW, zh)).toBeNull();
  });
});

describe("duration", () => {
  it("秒 / 分钟 / 小时[分] / 天[小时]（原生 2小时59分 那一档）", () => {
    expect(duration(1000, 1045, zh)).toBe("45秒");
    expect(duration(1000, 1000 + 5 * 60, zh)).toBe("5分钟");
    expect(duration(1000, 1000 + 2 * 3600 + 59 * 60, zh)).toBe("2小时59分");
    expect(duration(1000, 1000 + 2 * 3600, zh)).toBe("2小时");
    expect(duration(1000, 1000 + 3 * 86400 + 2 * 3600, zh)).toBe("3天2小时");
    expect(duration(1000, 1000 + 3 * 86400, zh)).toBe("3天");
    expect(duration(1000, 1000 + 2 * 3600 + 10 * 60, en)).toBe("2h 10m");
    expect(duration(1000, 1000 + 3 * 86400 + 2 * 3600, en)).toBe("3d 2h");
  });

  it("缺失 / 倒序 / 非正起点 → null", () => {
    expect(duration(undefined, 5, zh)).toBeNull();
    expect(duration(5, undefined, zh)).toBeNull();
    expect(duration(100, 50, zh)).toBeNull();
    expect(duration(0, 50, zh)).toBeNull();
  });
});

describe("absoluteLabel", () => {
  it("epoch 秒与 ISO 都给本地化绝对时间；解析不了 → undefined", () => {
    expect(absoluteLabel(1_756_500_000, "en")).toBe(new Date(1_756_500_000 * 1000).toLocaleString("en"));
    expect(absoluteLabel("2026-09-01T12:00:00Z", "en")).toBe(new Date(NOW).toLocaleString("en"));
    expect(absoluteLabel("nope", "en")).toBeUndefined();
    expect(absoluteLabel(null, "en")).toBeUndefined();
  });
});
