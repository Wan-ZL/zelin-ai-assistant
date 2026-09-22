// 会议纪要页纯逻辑——issue #440 的四件（CONTRACT §63.13 / §63.14 / §63.15 / §63.16）：
//   §63.16 手改的结束时间：rowLabel / recapHeader / 剪贴板表头按 end_override 显示、时长重算、坏值回落到录制的 end；
//            编辑框的 HH:MM → ISO-Z（合到会议那一天；不晚于开始 = null）；
//   §63.13 转写依据：从 sections_en 的 anchors 列读（标签或位置号 + 戳 + 原话），手改坏的滤掉，正文与剪贴板一字不带；
//   §63.13 三种新 code 的双语文案；§63.15 「丢了」的分钟数来自回执的 lost_after_s（老 daemon = 10）。
import { describe, expect, it } from "vitest";
import type { RecapAnchor, RecapRow } from "../../types";
import {
  endOverrideIso, localHHMM, lostAfterMinutes, problemLabel, recapAnchors, recapBody, recapClipboardText,
  recapHeader, rowLabel, shownDuration, shownEnd,
} from "./recapText";

const en = (zh: string, e: string) => e;
const zh = (z: string, _e: string) => z;

function row(over: Partial<RecapRow> = {}): RecapRow {
  return {
    key: "meeting:2026-08-31T1256-zoom", app: "zoom",
    start: "2026-08-31T19:56:00Z", end: "2026-08-31T20:36:00Z", duration_min: 40,
    status: "closed", version: 1, quality: "ok",
    en: ["Decided: x", "Split: y", "Deadline: z", "Changed since last plan: none recorded", "Open: none"],
    zh: ["定了：x", "分工：y", "截止：z", "较上次变化：无记录", "待定：无"],
    ...over,
  };
}

function sectionsRow(over: Partial<RecapRow> = {}): RecapRow {
  return row({
    shape: "sections", en: null, zh: null,
    sections_en: [
      { key: "decided", modality: "decided", items: ["Ann owns the data mix", "Legacy item without anchor"],
        tags: ["D1", "D2"], modalities: ["decided", ""],
        anchors: [{ at: "12:57", quote: "Ann will take the data mix" }, null] },
      { key: "split", modality: "decided", items: ["Bo may take the handover"], tags: [""],
        modalities: ["floated"], anchors: [{ at: "13:02", quote: "maybe Bo could take the handover" }] },
    ],
    sections_zh: [
      { key: "decided", modality: "decided", items: ["数据配比归 Ann", "老条目"], tags: ["D1", "D2"] },
      { key: "split", modality: "decided", items: ["交接也许归 Bo"], tags: [""] },
    ],
    copy_en: "Decided:\nD1. Ann owns the data mix\nD2. Legacy item without anchor\n\nSplit (decided):\n3. Bo may take the handover (floated)",
    copy_zh: "定了：\nD1. 数据配比归 Ann\nD2. 老条目\n\n分工（已定）：\n3. 交接也许归 Bo（有人提过）",
    ...over,
  });
}

describe("§63.16 end_override", () => {
  it("prefers a valid override for the shown end and recomputes the duration", () => {
    const captured = row();
    expect(shownEnd(captured)).toEqual({ end: "2026-08-31T20:36:00Z", overridden: false });
    expect(shownDuration(captured)).toBe(40);
    const edited = row({ end_override: "2026-08-31T20:30:00Z" });
    expect(shownEnd(edited)).toEqual({ end: "2026-08-31T20:30:00Z", overridden: true });
    expect(shownDuration(edited)).toBe(34);
    // 行标签、表头、剪贴板表头三处同一口径（所见即所复制）
    const label = rowLabel(edited);
    expect(label).toMatch(/^\d{2}:\d{2}–\d{2}:\d{2} · Zoom · 34 min$/);
    expect(label.split("–")[1].split(" ")[0]).toBe(localHHMM("2026-08-31T20:30:00Z"));
    expect(recapHeader(edited, "en").endsWith(label)).toBe(true);
    expect(recapClipboardText(edited, "en").split("\n")[0]).toBe(recapHeader(edited, "en"));
    // 正文一字不变
    expect(recapClipboardText(edited, "en").split("\n").slice(1).join("\n")).toBe(recapBody(edited, "en"));
  });

  it("falls back to the captured end on a missing, null or unparsable override", () => {
    for (const bad of [undefined, null, "", "yesterday", "2026-13-45T00:00:00Z"]) {
      const r = row({ end_override: bad as string | null | undefined });
      expect(shownEnd(r).overridden).toBe(false);
      expect(shownDuration(r)).toBe(40);
      expect(rowLabel(r)).toBe(rowLabel(row()));
    }
    // 手改到开始之前（手改坏的 marks）：时长不许是负数
    expect(shownDuration(row({ end_override: "2026-08-31T19:00:00Z" }))).toBe(0);
  });

  it("turns the editor's HH:MM into an ISO-Z on the meeting's day, and refuses an end before the start", () => {
    const r = row();
    const startLocal = new Date(r.start);
    const later = new Date(startLocal.getTime() + 34 * 60000);
    const hhmm = `${String(later.getHours()).padStart(2, "0")}:${String(later.getMinutes()).padStart(2, "0")}`;
    const iso = endOverrideIso(r, hhmm);
    expect(iso).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
    expect(new Date(iso as string).getTime()).toBe(later.getTime());
    // 与开始同一分钟 / 更早 / 坏输入 = null（面板据此说「结束要晚于开始」，不发请求）
    expect(endOverrideIso(r, localHHMM(r.start))).toBeNull();
    const earlier = new Date(startLocal.getTime() - 60000);
    expect(endOverrideIso(r, `${String(earlier.getHours()).padStart(2, "0")}:${String(earlier.getMinutes()).padStart(2, "0")}`)).toBeNull();
    expect(endOverrideIso(r, "")).toBeNull();
    expect(endOverrideIso(r, "25:99")).toBeNull();
    expect(endOverrideIso(row({ start: "garbage" }), "13:00")).toBeNull();
    expect(localHHMM("garbage")).toBe("");
  });
});

describe("§63.13 transcript anchors on the page", () => {
  it("lists one evidence row per anchored item, tag first, position when the item has no tag", () => {
    expect(recapAnchors(sectionsRow())).toEqual([
      { tag: "D1", at: "12:57", quote: "Ann will take the data mix" },
      { tag: "3", at: "13:02", quote: "maybe Bo could take the handover" },
    ]);
  });

  it("drops hand-mangled anchors and never touches the body or the clipboard", () => {
    const junk = ["nope", { at: 7, quote: "x" }, { at: "12:57", quote: "   " },
                  { at: "12:58", quote: "real words" }] as unknown as RecapAnchor[];
    const r = sectionsRow({
      sections_en: [{ key: "decided", modality: "decided", items: ["a", "b", "c", "d"], tags: ["D1", "D2", "D3", "D4"],
                      anchors: junk }],
    });
    expect(recapAnchors(r)).toEqual([{ tag: "D4", at: "12:58", quote: "real words" }]);
    expect(recapAnchors(sectionsRow({ sections_en: null }))).toEqual([]);
    expect(recapAnchors(row())).toEqual([]);                       // 五行形没有条目
    const body = recapClipboardText(sectionsRow(), "en");
    expect(body).not.toContain("12:57");
    expect(body).not.toContain("Ann will take the data mix");
    // 逐条语气的尾巴是 daemon 渲染进 copy_* 的，页面照原样显示
    expect(recapBody(sectionsRow(), "en")).toContain("3. Bo may take the handover (floated)");
    expect(recapBody(sectionsRow(), "zh")).toContain("3. 交接也许归 Bo（有人提过）");
  });

  it("explains the three new validator codes in both languages", () => {
    const unanchored = { code: "item_unanchored", lang: "en", line: 2 };
    expect(problemLabel(unanchored, en)).toBe("English: item 2 has no transcript anchor (the line's timestamp plus a verbatim fragment)");
    expect(problemLabel(unanchored, zh)).toBe("英文：第 2 条没有转写锚（转写那一行的时间戳 + 一段原话）");
    expect(problemLabel({ code: "anchor_unverified", lang: "en", line: 5 }, en)).toContain("item 5's anchor does not match the transcript");
    expect(problemLabel({ code: "anchor_unverified", lang: "en", line: 5 }, zh)).toContain("第 5 条的转写锚对不上转写");
    expect(problemLabel({ code: "item_modality", lang: "zh", line: 1 }, en)).toContain("item 1 has a modality outside decided / proposed / floated / open");
    expect(problemLabel({ code: "item_modality", lang: "zh", line: 1 }, zh)).toContain("第 1 条的语气不在固定表里");
  });
});

describe("§63.15 lost line from the receipt", () => {
  it("reads lost_after_s from the receipt and falls back to the old ten minutes", () => {
    expect(lostAfterMinutes(row())).toBe(10);
    expect(lostAfterMinutes(row({ generate_request: { requested_at: "x", state: "lost", note: null } }))).toBe(10);
    expect(lostAfterMinutes(row({ generate_request: { requested_at: "x", state: "lost", note: null, lost_after_s: 1339 } }))).toBe(22);
    expect(lostAfterMinutes(row({ generate_request: { requested_at: "x", state: "lost", note: null, lost_after_s: 600 } }))).toBe(10);
    expect(lostAfterMinutes(row({ generate_request: { requested_at: "x", state: "lost", note: null, lost_after_s: 5 } }))).toBe(1);
    expect(lostAfterMinutes(row({ generate_request: { requested_at: "x", state: "lost", note: null, lost_after_s: -3 } }))).toBe(10);
  });
});
