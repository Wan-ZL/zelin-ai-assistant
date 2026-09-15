// 纠正备注预检的判例（CONTRACT §63.5 / §63.10 / issue #296 / #303）：这一份纪要的格式做不到的
// 诉求必须在排队前被认出来，而普通的事实纠正（截止日期错了、owner 认错人了）必须一条都不误伤
// ——误报会把预检变成噪音。判定按**形状**收口：可发送长版删得掉一节、写得长一点，
// 对它说「做不到」就是那句错的拒绝。
import { describe, expect, it } from "vitest";
import { fixableByLongShape, NOTE_CONFLICT_ORDER, noteConflicts, SHAPE_IMPOSSIBLE } from "./noteCheck";

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
    // 句尾的语气词与客套话不是「另一个宾语」：这些照旧是在删那一行。
    const stillDrops = [
      "那行删掉就好。",
      "那行删掉就行。",
      "那条删掉就行。",
      "那行删掉算了。",
      "那行整个删掉算了。",
      "把那一行删掉谢谢。",
      "把那行去掉吧谢谢。",
      "第五行删掉就可以。",
      "那行去掉比较好。",
      "那行删掉，谢谢。",
      // 英文侧：连词串起来的两行一起删，以及最长的那个标签（Changed since last plan，
      // 动词与 line 之间 29 个字符）——把间距收太紧会让 issue #296 的原话够不着。
      "Remove the Deadline and Split lines.",
      "Drop the Open and Changed lines.",
      "Drop the Changed since last plan line.",
      "Get rid of the Changed since last plan line.",
      "Omit the Changed since last plan line.",
    ];
    for (const note of stillDrops) expect([note, noteConflicts(note)]).toEqual([note, ["drop_line"]]);
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
    // 中文侧还有一层：「行」得由指示词 / 量词 / 序数带着才是纪要的一行，别的都不是。
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
      "Remove the quotes around the decision line.",
      "把待定那行的占位删了，留着标签就行。",
      // 宾语提到动词前面（「把 X 从某行去掉」）——英文镜像早就挡住了，中文得一样。
      "把张三从分工那行去掉。",
      "把 Alice 从分工那行去掉。",
      "张三从那行去掉。",
      // 「那行的 <校验本来就在禁的东西>」：删的是那个东西，行还在。
      "去掉那行的时间戳。",
      "把那行的引号去掉。",
      "去掉那行的 emoji。",
      "那行的链接删掉。",
      "把待定那行的日期去掉。",
      "去掉那行的标点。",
    ];
    for (const note of innocent) expect([note, noteConflicts(note)]).toEqual([note, []]);
  });

  it("does not read a bare 行 inside an ordinary word as a recap line", () => {
    // 「行」当头字的词（行动项 / 行程 / 行业 / 行为 / 行政 / 行文）用黑名单前字根本挡不住,
    // 而「不用写行动项」是会议纪要备注里最可能出现的一句——词表因此收成白名单。
    const innocent = [
      "不用写行动项。",
      "去掉行动项。",
      "行动项那部分不用写。",
      "删掉行程安排。",
      "去掉行业背景那段。",
      "不要写行为描述。",
      "去掉行政方面的内容。",
      "别写行文风格的意见。",
      "去掉排行的部分。",
      "删掉修行的比喻。",
      "去掉通行做法。",
      "别写现行流程。",
      "去掉言行不一那段。",
      "不用写品行评价。",
      "别写德行的部分。",
    ];
    for (const note of innocent) expect([note, noteConflicts(note)]).toEqual([note, []]);
  });

  it("keeps a real drop request that shares a note with a content removal", () => {
    // 守卫是逐句的，不能连坐：同一条备注里「删那行」与「把张三从那行去掉」各算各的。
    expect(noteConflicts("Drop the Open line and remove me from the Split line."))
      .toEqual(["drop_line"]);
    expect(noteConflicts("把那行删掉，另外分工那行去掉张三。")).toEqual(["drop_line"]);
    expect(noteConflicts("省略掉那条关于薪资的内容，待定那行整个删掉。")).toEqual(["drop_line"]);
    expect(noteConflicts("把张三从分工那行去掉，待定那行整个删掉。")).toEqual(["drop_line"]);
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
  // ------------------------------------------------------------------ §63.10
  it("stops calling the sendable shape's satisfiable asks impossible", () => {
    const note = "较上次变化没内容就把那行删掉，其余几行写详细一点。";
    expect(noteConflicts(note)).toEqual(["drop_line", "more_detail"]);      // 五行形：照旧两条
    expect(noteConflicts(note, "sections")).toEqual([]);                    // 长版：这两件事做得到
    expect(noteConflicts("Omit the Open line and add one more section.", "sections")).toEqual([]);
    // 长版**仍然**做不到的三类：分节名是闭表、中英两版一次产出、格式禁项照旧
    expect(noteConflicts("Rename the Decided heading.", "sections")).toEqual(["relabel"]);
    expect(noteConflicts("去掉英文版。", "sections")).toEqual(["language_count"]);
    expect(noteConflicts("加粗一下 owner 的名字。", "sections")).toEqual(["formatting"]);
    expect(SHAPE_IMPOSSIBLE.sections).toEqual(["relabel", "language_count", "formatting"]);
    expect(SHAPE_IMPOSSIBLE.lines).toEqual(NOTE_CONFLICT_ORDER);
    // 形状缺省 / 认不出 = 五行形（老调用方的行为一字不变）
    expect(noteConflicts(note, "garbage")).toEqual(["drop_line", "more_detail"]);
  });

  it("knows which of the hits the long shape would fix (the panel points there)", () => {
    expect(fixableByLongShape(["drop_line", "formatting"])).toBe(true);
    expect(fixableByLongShape(["more_detail"])).toBe(true);
    expect(fixableByLongShape(["add_line"])).toBe(true);
    expect(fixableByLongShape(["relabel", "language_count", "formatting"])).toBe(false);
    expect(fixableByLongShape([])).toBe(false);
  });
});
