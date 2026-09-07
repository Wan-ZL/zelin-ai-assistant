// 设置页分区开合记忆的纯函数（CONTRACT §68.1 追记，D44；原生 SettingsCollapseStore 的 web 版）：
// 键 settings.expandedSections 缺席 → 默认四区；合法 JSON 数组原样收（含 `[]` = 全折，记忆比默认大）；坏形 → 默认；
// 写出排序后的 JSON 数组；toggle 纯函数翻一区。
import { beforeEach, describe, expect, it } from "vitest";
import {
  DEFAULT_EXPANDED_SECTIONS,
  EXPANDED_SECTIONS_STORAGE_KEY,
  readExpandedSections,
  toggledSections,
  writeExpandedSections,
} from "./settingsFolds";

beforeEach(() => {
  window.localStorage.clear();
});

describe("readExpandedSections — memory beats the default seed", () => {
  it("seeds the four everyday sections when nothing is stored", () => {
    expect(EXPANDED_SECTIONS_STORAGE_KEY).toBe("settings.expandedSections");
    expect([...readExpandedSections()].sort()).toEqual([...DEFAULT_EXPANDED_SECTIONS].sort());
    expect([...DEFAULT_EXPANDED_SECTIONS].sort()).toEqual(["deps", "general", "live_captions", "recording"]);
  });

  it("honours a stored array verbatim — including the empty array (everything collapsed)", () => {
    window.localStorage.setItem(EXPANDED_SECTIONS_STORAGE_KEY, JSON.stringify(["gmail", "flags"]));
    expect([...readExpandedSections()].sort()).toEqual(["flags", "gmail"]);
    window.localStorage.setItem(EXPANDED_SECTIONS_STORAGE_KEY, "[]");
    expect(readExpandedSections().size).toBe(0);
  });

  it("falls back to the default seed on corrupt / non-array values and drops non-string items", () => {
    window.localStorage.setItem(EXPANDED_SECTIONS_STORAGE_KEY, "{not json");
    expect([...readExpandedSections()].sort()).toEqual([...DEFAULT_EXPANDED_SECTIONS].sort());
    window.localStorage.setItem(EXPANDED_SECTIONS_STORAGE_KEY, JSON.stringify({ general: true }));
    expect([...readExpandedSections()].sort()).toEqual([...DEFAULT_EXPANDED_SECTIONS].sort());
    window.localStorage.setItem(EXPANDED_SECTIONS_STORAGE_KEY, JSON.stringify(["voice", 3, null, "", "sync"]));
    expect([...readExpandedSections()].sort()).toEqual(["sync", "voice"]);
  });
});

describe("writeExpandedSections / toggledSections", () => {
  it("writes a sorted JSON array that reads back to the same set", () => {
    writeExpandedSections(new Set(["voice", "deps", "general"]));
    expect(window.localStorage.getItem(EXPANDED_SECTIONS_STORAGE_KEY)).toBe('["deps","general","voice"]');
    expect([...readExpandedSections()].sort()).toEqual(["deps", "general", "voice"]);
  });

  it("toggle adds a missing id and removes a present one without mutating the input", () => {
    const base: ReadonlySet<string> = new Set(["general"]);
    const opened = toggledSections(base, "gmail");
    expect([...opened].sort()).toEqual(["general", "gmail"]);
    const closed = toggledSections(opened, "general");
    expect([...closed]).toEqual(["gmail"]);
    expect([...base]).toEqual(["general"]);
  });
});
