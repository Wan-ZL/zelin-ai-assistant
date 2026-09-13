// 纠正备注预检的判例（CONTRACT §63.5 / issue #296）：五行契约做不到的诉求必须在排队前被认出来，
// 而普通的事实纠正（截止日期错了、owner 认错人了）必须一条都不误伤——误报会把预检变成噪音。
import { describe, expect, it } from "vitest";
import { NOTE_CONFLICT_ORDER, noteConflicts } from "./noteCheck";

describe("noteConflicts", () => {
  it("says nothing about an empty or whitespace-only note", () => {
    expect(noteConflicts("")).toEqual([]);
    expect(noteConflicts("   \n ")).toEqual([]);
    expect(noteConflicts(undefined as unknown as string)).toEqual([]);
  });

  it("catches the two shapes from the issue: drop a label line, and ask for more detail", () => {
    // issue #296 的原始两条：删一行 + 写详细。validate() 对这两条永远说不。
    expect(noteConflicts("If the Changed since last plan line has nothing, omit the line."))
      .toEqual(["drop_line"]);
    expect(noteConflicts("Write the remaining lines in more detail.")).toEqual(["more_detail"]);
    expect(noteConflicts("较上次变化没内容就把那行删掉，其余几行写详细一点。"))
      .toEqual(["drop_line", "more_detail"]);
  });

  it("catches an extra line, relabelling, a language count change and banned formatting", () => {
    expect(noteConflicts("Add a line for action items.")).toEqual(["add_line"]);
    expect(noteConflicts("再加一行记风险。")).toEqual(["add_line"]);
    expect(noteConflicts("Rename Split to Owners.")).toEqual(["relabel"]);
    expect(noteConflicts("把标签改成中文项目部的叫法。")).toEqual(["relabel"]);
    expect(noteConflicts("English only this time.")).toEqual(["language_count"]);
    expect(noteConflicts("只要中文就行。")).toEqual(["language_count"]);
    expect(noteConflicts("Use bullets and bold the owner names.")).toEqual(["formatting"]);
    expect(noteConflicts("给每行加个时间戳。")).toEqual(["formatting"]);
  });

  it("returns a deduped list in the fixed display order", () => {
    const many = noteConflicts(
      "Use bullets, write it in more detail, rename the labels, drop the Open line, "
      + "add a line for risks, and give me English only.");
    expect(many).toEqual(NOTE_CONFLICT_ORDER);
    expect(new Set(many).size).toBe(many.length);
  });

  it("leaves ordinary factual corrections alone", () => {
    const innocent = [
      "deadline is Friday, not Monday",
      "The owner is Wan, not me.",
      "截止日期写错了，是下周五。",
      "分工那行的名字反了，训练是我做。",
      "Decided is wrong: we did not agree to ship, we agreed to test.",
      "第三方的声音混进来了，忽略关于午饭的那段。",
      "No deadline was set in this meeting.",
      "Drop the mention of the budget — that was a different meeting.",
      "会议比排的时间长，内容以后半段为准。",
    ];
    for (const note of innocent) expect([note, noteConflicts(note)]).toEqual([note, []]);
  });

  it("is case-insensitive and stable for the same text", () => {
    expect(noteConflicts("OMIT THE OPEN LINE")).toEqual(["drop_line"]);
    expect(noteConflicts("omit the open line")).toEqual(["drop_line"]);
    expect(noteConflicts("More Detail Please")).toEqual(["more_detail"]);
  });
});
