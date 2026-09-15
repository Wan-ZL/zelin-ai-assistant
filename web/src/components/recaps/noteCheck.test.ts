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
    // 收紧词表时最容易连坐的就是这几个裸形——「那行 / 那条 / 整行」必须照旧可达。
    expect(noteConflicts("把那行删掉。")).toEqual(["drop_line"]);
    expect(noteConflicts("待定那条整个删掉。")).toEqual(["drop_line"]);
    expect(noteConflicts("不要第四行。")).toEqual(["drop_line"]);
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
    // 往行里塞东西 ≠ 要第六行：只该报格式那一条。
    expect(noteConflicts("Add timestamps to each line.")).toEqual(["formatting"]);
    expect(noteConflicts("把五行的顺序调一下，截止放最后。")).toEqual(["relabel"]);
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

  it("does not answer a removal of other content as a removal of the line", () => {
    // 删的是行里的内容 / 名字 / 那段，行还在——这些都是可满足的诉求。
    // 中文侧还有一层：裸「行」是执行 / 银行 / 进行 / 可行 的尾字，不是纪要的一行。
    const innocent = [
      "不要写执行细节，只写结论。",
      "去掉关于银行账户的内容。",
      "别写进行中的状态，会议已经结束了。",
      "不要提可行性研究那部分。",
      "省略掉那条关于薪资的内容。",
      "分工那行去掉张三，他没参加。",
      "待定那行里的预算条目去掉，那是另一场会。",
      "Remove Alice from the Split line.",
      "Drop me from the Split line, I only observed.",
      "把待定那行的占位删了，留着标签就行。",
    ];
    for (const note of innocent) expect([note, noteConflicts(note)]).toEqual([note, []]);
  });

  it("keeps a real drop request that shares a note with a content removal", () => {
    // 守卫是逐句的，不能连坐：同一条备注里「删那行」与「把张三从那行去掉」各算各的。
    expect(noteConflicts("Drop the Open line and remove me from the Split line."))
      .toEqual(["drop_line"]);
    expect(noteConflicts("把那行删掉，另外分工那行去掉张三。")).toEqual(["drop_line"]);
    expect(noteConflicts("省略掉那条关于薪资的内容，待定那行整个删掉。")).toEqual(["drop_line"]);
    // issue #296 的原话就带着「没内容」三个字——守卫的名词表刻意不含它。
    expect(noteConflicts("那行没内容就删掉。")).toEqual(["drop_line"]);
  });

  it("does not answer a removal request with the opposite direction's refusal", () => {
    // 极性：要求「少写细节 / 去掉格式 / 删掉某语言里的错字」全是校验本来就在做的方向，
    // 答成「写不了更详细 / 加不了格式 / 改不了语言」就是在赶人走。
    const innocent = [
      "Remove the extra context about hiring.",
      "去掉多余的背景说明。",
      "Remove the English typo in the owner name.",
      "去掉中文名字的拼音。",
      "去掉引号，那不是原话。",
      "把 emoji 删掉。",
      "Remove the quotes around the decision line.",
      "Take the emoji out of the decided line.",
      "The timestamp is wrong, it started at 12:56.",
      "他的原话是周五，不是下周一。",
      "五行的顺序没问题，但分工写错了。",
      "Write less detail on hiring.",
      "少写点细节。",
      "不要加粗。",
    ];
    for (const note of innocent) expect([note, noteConflicts(note)]).toEqual([note, []]);
    // 反方向照旧命中——守卫收的是极性，不是词。
    expect(noteConflicts("多写点细节。")).toEqual(["more_detail"]);
    expect(noteConflicts("加粗一下 owner 的名字。")).toEqual(["formatting"]);
    expect(noteConflicts("去掉英文版。")).toEqual(["language_count"]);
  });

  it("is case-insensitive and stable for the same text", () => {
    expect(noteConflicts("OMIT THE OPEN LINE")).toEqual(["drop_line"]);
    expect(noteConflicts("omit the open line")).toEqual(["drop_line"]);
    expect(noteConflicts("More Detail Please")).toEqual(["more_detail"]);
  });
});
