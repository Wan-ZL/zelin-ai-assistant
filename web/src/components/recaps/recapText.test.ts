// 会议纪要页纯逻辑（§63）：行标签、按日分组、badge 词表、语言选择、正文只含 5 行；
// §63.5 追记（issue #299）抬头一行与剪贴板那一份；§63.8 生成态判定（server 回执
// generate_request × 本地乐观 pending）与它的 badge。
import { describe, expect, it } from "vitest";
import type { RecapRow } from "../../types";
import {
  PENDING_TIMEOUT_MS, PICKUP_TIMEOUT_MS, appLabel, badgesFor, dayLabel, generationPhase, groupByDay, isGenerating,
  pickLanguage, recapBody, recapClipboardText, recapHeader, rowLabel, slackDraftLabel,
} from "./recapText";

function row(over: Partial<RecapRow> = {}): RecapRow {
  return {
    key: "meeting:2026-08-31T1256-zoom",
    app: "zoom",
    start: "2026-08-31T19:56:00Z",
    end: "2026-08-31T20:16:00Z",
    duration_min: 20,
    status: "closed",
    version: 1,
    quality: "ok",
    en: ["Decided: x", "Split: y", "Deadline: z", "Changed since last plan: none recorded", "Open: none"],
    zh: ["定了：x", "分工：y", "截止：z", "较上次变化：无记录", "待定：无"],
    ...over,
  };
}

describe("recapText", () => {
  it("labels rows with local times, app name and duration", () => {
    const label = rowLabel(row());
    expect(label).toMatch(/^\d{2}:\d{2}–\d{2}:\d{2} · Zoom · 20 min$/);
    expect(appLabel("slack-huddle")).toBe("Slack Huddle");
    expect(appLabel("unknown-app")).toBe("unknown-app");
    expect(rowLabel(row({ start: "garbage", end: "garbage" }))).toContain("--:--");
  });

  it("groups consecutive rows by local day", () => {
    const a = row({ key: "meeting:2026-08-31T1256-zoom" });
    const b = row({ key: "meeting:2026-08-31T1000-zoom", start: "2026-08-31T17:00:00Z", end: "2026-08-31T17:20:00Z" });
    const c = row({ key: "meeting:2026-08-20T1000-zoom", start: "2026-08-20T17:00:00Z", end: "2026-08-20T17:20:00Z" });
    const groups = groupByDay([a, b, c]);
    expect(groups.length).toBe(2);
    expect(groups[0].rows.map((r) => r.key)).toEqual([a.key, b.key]);
    expect(groups[1].rows).toEqual([c]);
  });

  it("badges follow the #129 vocabulary", () => {
    const ids = (r: RecapRow) => badgesFor(r).map((b) => b.id);
    expect(ids(row())).toEqual(["new"]);
    expect(ids(row({ copied_at: "2026-09-01T00:00:00Z" }))).toEqual(["copied"]);
    expect(ids(row({ copied_at: "x", sent_at: "y" }))).toEqual(["sent"]);
    expect(ids(row({ version: 2 }))).toEqual(["new", "updated"]);
    expect(ids(row({ status: "open", en: null, zh: null, quality: null }))).toEqual(["open"]);
    expect(ids(row({ status: "open", partial: true }))).toEqual(["open", "partial"]);
    expect(ids(row({ quality: "needs_review" }))).toEqual(["new", "review"]);
    expect(ids(row({ en: null, zh: null, quality: "thin_transcript" }))).toEqual(["thin"]);
    expect(ids(row({ en: null, zh: null, quality: "no_audio" }))).toEqual(["silent"]);
    expect(ids(row({ en: null, zh: null, quality: "generation_failed" }))).toEqual(["failed"]);
  });

  it("generation phase: a fresh server receipt wins, local pending only bridges the gap (§63.8)", () => {
    const T = 1_000_000;
    const pending = { version: 1, requested_at: null, at: T };
    // 没按过、没回执 = idle；按下后 actd 还没接手 = queued
    expect(generationPhase(row(), undefined, T)).toBe("idle");
    expect(generationPhase(row(), pending, T + 1000)).toBe("queued");
    // 回执落地：running → 以它为准；done 且版本涨了 → idle
    const running = { requested_at: "2026-09-14T12:00:05Z", state: "running", note: null };
    expect(generationPhase(row({ generate_request: running }), pending, T + 5000)).toBe("running");
    expect(generationPhase(row({ generate_request: running }), undefined, T)).toBe("running");   // 刷新页面后仍知道
    const done = { ...running, state: "done" };
    expect(generationPhase(row({ version: 2, generate_request: done }), pending, T + 60_000)).toBe("idle");
    expect(generationPhase(row({ version: 2 }), pending, T + 60_000)).toBe("idle");             // 版本涨了就够
    // 按下时看到的是上一轮的旧回执：它不算新，本地 pending 继续说 queued，直到新 requested_at 出现
    const stale = { requested_at: "2026-09-14T11:00:00Z", state: "lost", note: null };
    const pressedOnStale = { version: 1, requested_at: stale.requested_at, at: T };
    expect(generationPhase(row({ generate_request: stale }), pressedOnStale, T + 1000)).toBe("queued");
    expect(generationPhase(row({ generate_request: stale }), undefined, T)).toBe("lost");
    expect(generationPhase(row({ generate_request: { ...stale, state: "noop", note: "launch_failed" } }), undefined, T)).toBe("noop");
    expect(generationPhase(row({ generate_request: { ...stale, state: "done" } }), undefined, T)).toBe("idle");
    // 90 s 没人接手 → unclaimed（按钮解锁、一句人话）；十分钟退场
    expect(generationPhase(row(), pending, T + PICKUP_TIMEOUT_MS)).toBe("queued");
    expect(generationPhase(row(), pending, T + PICKUP_TIMEOUT_MS + 1)).toBe("unclaimed");
    expect(generationPhase(row(), pending, T + PENDING_TIMEOUT_MS)).toBe("unclaimed");
    expect(generationPhase(row(), pending, T + PENDING_TIMEOUT_MS + 1)).toBe("idle");
    // unclaimed 期间新回执 / 新版本照样接管
    expect(generationPhase(row({ generate_request: running }), pending, T + PICKUP_TIMEOUT_MS + 1)).toBe("running");
    expect(generationPhase(row({ version: 2 }), pending, T + PICKUP_TIMEOUT_MS + 1)).toBe("idle");
    expect(isGenerating("queued") && isGenerating("running")).toBe(true);
    expect(isGenerating("unclaimed") || isGenerating("lost") || isGenerating("noop") || isGenerating("idle")).toBe(false);
  });

  it("generation badges lead the row: 生成中 / 后台未接手 / 生成未落地 / 生成未启动", () => {
    const ids = (r: RecapRow, phase: Parameters<typeof badgesFor>[1]) => badgesFor(r, phase).map((b) => b.id);
    expect(ids(row(), "queued")).toEqual(["generating", "new"]);
    expect(ids(row({ version: 2 }), "running")).toEqual(["generating", "new", "updated"]);
    expect(ids(row(), "unclaimed")).toEqual(["unclaimed", "new"]);
    expect(ids(row(), "lost")).toEqual(["lost", "new"]);
    expect(ids(row(), "noop")).toEqual(["noop", "new"]);
    expect(ids(row(), "idle")).toEqual(["new"]);
    expect(badgesFor(row(), "running")[0]).toMatchObject({ zh: "生成中", en: "Generating", tone: "info" });
    expect(badgesFor(row(), "unclaimed")[0]).toMatchObject({ zh: "后台未接手", en: "Not picked up", tone: "warning" });
    expect(badgesFor(row(), "lost")[0]).toMatchObject({ tone: "warning" });
  });

  it("picks the language: explicit setting wins, auto follows the UI", () => {
    expect(pickLanguage("zh", "en")).toBe("zh");
    expect(pickLanguage("en", "zh")).toBe("en");
    expect(pickLanguage("auto", "zh")).toBe("zh");
    expect(pickLanguage(undefined, "en")).toBe("en");
  });

  it("copy body is exactly the five lines joined by newlines", () => {
    expect(recapBody(row(), "en")).toBe("Decided: x\nSplit: y\nDeadline: z\nChanged since last plan: none recorded\nOpen: none");
    expect(recapBody(row(), "zh").split("\n").length).toBe(5);
    expect(recapBody(row({ en: null }), "en")).toBe("");
  });

  // 判例与时区无关：用本地时间构造 ISO，再按本地时间格式化——两头同一个时区就抵消掉了。
  // 2026-03-04 是周三（也是 issue #299 举的那天），且离任何 DST 跳变都远。
  const localIso = (y: number, m: number, d: number, hh: number, mm: number) =>
    new Date(y, m - 1, d, hh, mm).toISOString();

  it("day label carries the weekday, localized, and stays silent on garbage (§63.5)", () => {
    const wed = localIso(2026, 3, 4, 14, 7);
    expect(dayLabel(wed, "en")).toBe("2026-03-04 (Wed)");
    expect(dayLabel(wed, "zh")).toBe("2026-03-04（周三）");
    // 七天连着走一遍，两张表都走：星期表与 Date.getDay() 同序（周日起），不靠 Intl。
    // 2026-03-01 是周日——中英各钉七个名字，少钉一边就漏掉那张表的错序（判例本身的旧漏洞）
    const week = (lang: "zh" | "en") =>
      [...Array(7)].map((_x, i) => dayLabel(localIso(2026, 3, 1 + i, 12, 0), lang).replace(/^[\d-]+ ?[（(](.+)[）)]$/, "$1"));
    expect(week("en")).toEqual(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]);
    expect(week("zh")).toEqual(["周日", "周一", "周二", "周三", "周四", "周五", "周六"]);
    expect(dayLabel("garbage", "en")).toBe("");
    expect(dayLabel("garbage", "zh")).toBe("");
  });

  it("header = date + weekday + the row label, times as captured (§63.5 / issue #299)", () => {
    const r = row({ start: localIso(2026, 3, 4, 14, 7), end: localIso(2026, 3, 4, 14, 48), duration_min: 41 });
    expect(recapHeader(r, "en")).toBe("2026-03-04 (Wed) 14:07–14:48 · Zoom · 41 min");
    expect(recapHeader(r, "zh")).toBe("2026-03-04（周三）14:07–14:48 · Zoom · 41 min");
    // 采集到的 14:07 原样出现——不向下取整到 14:00
    expect(recapHeader(r, "en")).toContain("14:07");
    // 同一天两场会的抬头必须不同（issue #299：两份粘出去分不出是哪场）
    const later = row({ start: localIso(2026, 3, 4, 16, 30), end: localIso(2026, 3, 4, 17, 0), duration_min: 30 });
    expect(recapHeader(later, "en")).not.toBe(recapHeader(r, "en"));
    // start 解析不出 → 退回行标签，不多出一个 "?"
    const broken = row({ start: "garbage", end: "garbage" });
    expect(recapHeader(broken, "en")).toBe(rowLabel(broken));
    expect(recapHeader(broken, "en")).not.toContain("?");
  });

  it("clipboard text = header + the five lines; the knob off gives the five alone (§63.5)", () => {
    const r = row({ start: localIso(2026, 3, 4, 14, 7), end: localIso(2026, 3, 4, 14, 48), duration_min: 41 });
    const withHeader = recapClipboardText(r, "en", true);
    expect(withHeader.split("\n").length).toBe(6);
    expect(withHeader.split("\n")[0]).toBe("2026-03-04 (Wed) 14:07–14:48 · Zoom · 41 min");
    expect(withHeader.slice(withHeader.indexOf("\n") + 1)).toBe(recapBody(r, "en"));
    expect(recapClipboardText(r, "en", false)).toBe(recapBody(r, "en"));
    expect(recapClipboardText(r, "zh", true).split("\n")[0]).toBe("2026-03-04（周三）14:07–14:48 · Zoom · 41 min");
    // 没正文 = 空串（光一行抬头不是纪要），两种开关值都一样
    expect(recapClipboardText(row({ en: null }), "en", true)).toBe("");
    expect(recapClipboardText(row({ en: null }), "en", false)).toBe("");
    // start 坏掉但正文在：抬头退回行标签，剪贴板照旧是 6 行，不空
    const broken = recapClipboardText(row({ start: "garbage", end: "garbage" }), "en", true);
    expect(broken.split("\n").length).toBe(6);
    expect(broken.split("\n")[0]).toContain("--:--");
  });

  it("slack draft receipt copy", () => {
    const text = (_zh: string, en: string) => en;
    expect(slackDraftLabel("posted", text)).toBe("Draft placed");
    expect(slackDraftLabel("draft_already_exists", text)).toBe("Slack already has a draft");
    expect(slackDraftLabel("no_target", text)).toBe("No draft: no target conversation");
    expect(slackDraftLabel("weird", text)).toBe("weird");
    expect(slackDraftLabel(undefined, text)).toBe("");
  });
});
