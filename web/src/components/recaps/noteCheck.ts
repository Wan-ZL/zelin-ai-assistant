// 纠正备注的确定性预检（CONTRACT §63.5 / issue #296）：五行契约结构上做不到的诉求，
// 在排队之前就说清楚，而不是让 recap_text.validate 在几分钟后默默退回一份 needs_review。
// 纯逻辑、无 React、无 fetch——vitest node 环境可直测；这里只出 id，文案在 RecapDetail 里
// 走页面同一套 text(zh, en)（防腐 #10：不另起第二套双语机制）。
//
// 六类诉求各对应 §63.3 模板里的一条硬约束：
//   drop_line / add_line  → 恰好五行（validate: "exactly 5 lines per language"）
//   more_detail           → 每行长度硬帽（EN ≤ 140 / 中文 ≤ 60）
//   relabel               → 标签文字与顺序固定
//   language_count        → en + zh 一次产出，两版都存
//   formatting            → 禁 markdown / emoji / 链接 / 时间戳 / 引号
export type NoteConflictId =
  | "drop_line"
  | "add_line"
  | "more_detail"
  | "relabel"
  | "language_count"
  | "formatting";

/** 展示顺序固定（备注命中多条时逐条按此序列出） */
export const NOTE_CONFLICT_ORDER: NoteConflictId[] = [
  "drop_line", "add_line", "more_detail", "relabel", "language_count", "formatting",
];

// 中文「删除」动词与「行 / 标签」名词离得足够近才算——「不要写错截止日期」不是删行。
const NEAR = "[^。；;.!?\\n]{0,16}";

const PATTERNS: Record<NoteConflictId, RegExp[]> = {
  drop_line: [
    /\b(drop|omit|remove|delete|skip|cut|get rid of|leave out|no need for)\b[^.!?\n]{0,24}\b(line|lines|label|labels|row|rows|section|sections|field|fields|heading)\b/,
    /\b(line|lines|label|labels|row|rows|section|sections)\b[^.!?\n]{0,24}\b(should not|shouldn't|must not|mustn't|does not|doesn't)\b[^.!?\n]{0,16}\b(appear|show|be there)\b/,
    new RegExp(`(删掉|删去|删除|去掉|去除|拿掉|不要|别写|不用写|省略|不显示|不出现)${NEAR}(行|标签|那条|这条|一条|栏)`),
    new RegExp(`(行|标签|那条|这条|一条|栏)${NEAR}(删掉|删去|删除|去掉|去除|拿掉|别写|不用写|省略|不显示|不出现)`),
  ],
  add_line: [
    /\b(add|append|insert)\b[^.!?\n]{0,24}\b(line|row|section|bullet|field)\b/,
    /\b(another|extra|one more|a sixth|sixth|additional)\b[^.!?\n]{0,12}\b(line|row|section)\b/,
    new RegExp(`(加|添|多|再加|新增)${NEAR}(一行|一条|一栏|个行|第六行)`),
    /第六行|多一行|多一条/,
  ],
  more_detail: [
    /\b(more|greater|extra|additional|further|fuller)\b[^.!?\n]{0,12}\b(detail|details|context|background|depth|specifics)\b/,
    /\b(in (more|much more|greater) detail|go into detail|go deeper|be more detailed|more thorough|elaborate|expand on|expand the|flesh (it|them) out|spell it out in full)\b/,
    /\b(make|write|keep)\b[^.!?\n]{0,24}\b(longer|more verbose)\b|\blonger lines?\b/,
    /更详细|详细一点|详细一些|详细些|再详细|写详细|多写点|多写些|多写一点|更多细节|细节再多|展开讲|展开写|写长一点|加长|更充分/,
  ],
  relabel: [
    /\b(rename|relabel|re-label)\b/,
    /\b(change|changing|different|new|reword|rewrite|swap)\b[^.!?\n]{0,20}\b(label|labels|heading|headings|prefix|prefixes)\b/,
    /\b(reorder|re-order)\b|\b(change|different|swap)\b[^.!?\n]{0,20}\b(order of the (lines|labels)|line order)\b/,
    new RegExp(`(改|换|改成|重命名|调整|重新)${NEAR}(标签|标题|前缀)|(标签|标题|前缀)${NEAR}(改|换|重命名)`),
    new RegExp(`(行|标签|五行)${NEAR}顺序|顺序${NEAR}(行|标签)|重新排序|调换${NEAR}(行|标签)`),
  ],
  language_count: [
    /\b(only|just)\b[^.!?\n]{0,12}\b(in )?(english|chinese|mandarin)\b|\b(english|chinese|mandarin)\b[^.!?\n]{0,8}\bonly\b/,
    /\b(drop|skip|remove|omit|no)\b[^.!?\n]{0,16}\b(english|chinese|mandarin)\b/,
    /\b(also|add|and)\b[^.!?\n]{0,12}\b(in )?(japanese|korean|spanish|french|german|portuguese)\b/,
    /只要(中文|英文|英语)|只写(中文|英文|英语)|只用(中文|英文|英语)|只出(中文|英文|英语)|不要(中文|英文|英语)|去掉(中文|英文|英语)|(中文|英文|英语)就(行|够)/,
    /日文版|日语版|韩文版|西班牙文|法文版/,
  ],
  formatting: [
    /\b(bullet|bullets|bulleted|markdown|mrkdwn|bold|boldface|italic|italics|emoji|emojis|hyperlink|hyperlinks|timestamps?|time stamps?)\b/,
    /\b(add|include|put|use|link)\b[^.!?\n]{0,20}\b(a link|links|the link|urls?)\b/,
    /\b(quote|quotes|quotation marks|verbatim|word for word|in quotes)\b/,
    /项目符号|要点符号|加粗|粗体|黑体|斜体|表情符号|emoji|超链接|时间戳|引号|原话|逐字引用|markdown/i,
    new RegExp(`(加|放|带|贴|附)${NEAR}链接`),
  ],
};

/**
 * 备注里结构上无法满足的诉求（固定顺序、去重）。空备注 = 空表。
 * 判定只看备注文本，不调模型、不发请求——同一段文本永远得同一个答案。
 */
export function noteConflicts(note: string): NoteConflictId[] {
  const text = String(note ?? "").toLowerCase();
  if (!text.trim()) return [];
  return NOTE_CONFLICT_ORDER.filter((id) => PATTERNS[id].some((rx) => rx.test(text)));
}
