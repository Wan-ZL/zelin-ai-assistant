// 会议纪要页纯逻辑（§63）：行标签、按日分组、badge 词表、语言选择、复制正文只含 5 行。
import { describe, expect, it } from "vitest";
import type { RecapRow } from "../../types";
import { appLabel, badgesFor, groupByDay, pickLanguage, recapBody, rowLabel, slackDraftLabel } from "./recapText";

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

  it("slack draft receipt copy", () => {
    const text = (_zh: string, en: string) => en;
    expect(slackDraftLabel("posted", text)).toBe("Draft placed");
    expect(slackDraftLabel("draft_already_exists", text)).toBe("Slack already has a draft");
    expect(slackDraftLabel("no_target", text)).toBe("No draft: no target conversation");
    expect(slackDraftLabel("weird", text)).toBe("weird");
    expect(slackDraftLabel(undefined, text)).toBe("");
  });
});
