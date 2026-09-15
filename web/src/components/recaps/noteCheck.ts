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
//
// 这是固定词表上的尽力预检，不是判定器：漏报只退回旧行为（几分钟后的 needs_review），
// 误报才是真伤（预检一吵就没人看它）。判定因此分三层：
//   1) 逐句判定（CLAUSE_SPLIT）——一句里的「去掉张三」不该染上隔壁那句的「把那行删掉」；
//   2) 命中表 PATTERNS——动词与名词必须落在同一句、且离得足够近；
//   3) 守卫表 GUARDS——同一句说的是「删他物」（去掉那行里的内容 / remove Alice from the Split
//      line）或「反向极性」（去掉引号、少写细节、时间戳写错了——都是校验本来就在做的方向）时，
//      这一类当场不算命中。
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

/** 逐句切分：中英标点 + 换行 + 破折号。切分只让命中变少，不会凭空多出一条。 */
const CLAUSE_SPLIT = /[。；！？、，,;!?.\n—–]+/;

// 中文「删除」动词与「行 / 标签」名词离得足够近才算——「不要写错截止日期」不是删行。
const NEAR = "[^。；;.!?\\n]{0,16}";
const ZH_DROP = "(删掉|删去|删除|删了|去掉|去除|拿掉|不要|别写|不用写|省略掉?|不显示|不出现)";
const ZH_DROP_TAIL = "(删掉|删去|删除|删了|去掉|去除|拿掉|别写|不用写|省略|不显示|不出现)";
// 裸「行」是一堆别的词的尾字（执行 / 银行 / 进行 / 可行 / 例行 / 五行 / 几行），只有前面不是
// 这些字时才是纪要的一行；「那行 / 那条 / 整行 / 第三行」本身无歧义，照旧可达——issue #296
// 的原话就是「把那行删掉」。
const ZH_LINE = "(那一?行|这一?行|整行|某一?行|第[一二三四五六]行|标签|那条|这条|一条|栏"
  + "|(?<![执银进可例流旅同外内发运并平施举推履五几])行)";
// 英文侧「动词 → line」之间不许夹介词与连词（from / in / on / of / out of / and / keep）：
// 那些是「把 X 从某行里去掉」「删掉占位但留着那行」，不是「把某行删掉」——remove Alice from
// the Split line 是可满足的诉求。间距也从 24 收到 16，够 "get rid of the Deadline line"，
// 不够把半句话跨过去。
const EN_NEAR_LINE =
  "(?:(?!\\b(?:from|in|on|of|out of|within|off|and|but|keep|leave)\\b)[^.!?\\n]){0,16}";
// 同一道理的镜像：「add timestamps to each line」是往行里塞东西，不是要第六行。
const EN_NEAR_ADD = "(?:(?!\\b(?:to|into|onto|each|every|all)\\b)[^.!?\\n]){0,24}";

const PATTERNS: Record<NoteConflictId, RegExp[]> = {
  drop_line: [
    new RegExp("\\b(drop|omit|remove|delete|skip|cut|get rid of|leave out|no need for)\\b"
      + EN_NEAR_LINE
      + "\\b(line|lines|label|labels|row|rows|section|sections|field|fields|heading)\\b"),
    /\b(line|lines|label|labels|row|rows|section|sections)\b[^.!?\n]{0,24}\b(should not|shouldn't|must not|mustn't|does not|doesn't)\b[^.!?\n]{0,16}\b(appear|show|be there)\b/,
    new RegExp(`${ZH_DROP}${NEAR}${ZH_LINE}`),
    new RegExp(`${ZH_LINE}${NEAR}${ZH_DROP_TAIL}`),
  ],
  add_line: [
    new RegExp("\\b(add|append|insert)\\b" + EN_NEAR_ADD + "\\b(line|row|section|bullet|field)\\b"),
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
    // 顺序得由祈使动词管着才算改标签序——「五行的顺序没问题」是在夸，不是在要求。
    new RegExp(`(改|换|调整|调一下|调下|调换|重新排|重排)${NEAR}(顺序|次序)`
      + `|(顺序|次序)${NEAR}(改|换|调整|调一下|调下|调换|重新排|重排)|重新排序`),
  ],
  language_count: [
    /\b(only|just)\b[^.!?\n]{0,12}\b(in )?(english|chinese|mandarin)\b|\b(english|chinese|mandarin)\b[^.!?\n]{0,8}\bonly\b/,
    // 语言名必须是被删的那个宾语本身——「remove the english typo」删的是错字，不是英文版。
    /\b(drop|skip|remove|omit|no|without)\s+(the\s+)?(english|chinese|mandarin)(\s+(version|copy|recap|text|one|side))?(\s+(this time|next time|please|at all))?\s*$/,
    /\b(also|add|and)\b[^.!?\n]{0,12}\b(in )?(japanese|korean|spanish|french|german|portuguese)\b/,
    /(只要|只写|只用|只出|只留|就要)(中文|英文|英语)|(中文|英文|英语)(就行|就够|即可)/,
    // 同理，中文侧「去掉中文名字的拼音」删的是拼音——语言名后面不许再跟别的名词。
    /(不要|去掉|删掉|删去|省略|不用|不出|别出)(中文|英文|英语)(版|那版|那一版)?\s*$/,
    /日文版|日语版|韩文版|西班牙文|法文版/,
  ],
  formatting: [
    /\b(bullet|bullets|bulleted|markdown|mrkdwn|bold|boldface|italic|italics|emoji|emojis|hyperlink|hyperlinks|timestamps?|time stamps?)\b/,
    /\b(add|include|put|use|link)\b[^.!?\n]{0,20}\b(a link|links|the link|urls?)\b/,
    /\b(quote|quotes|quotation marks|verbatim|word for word|in quotes)\b/,
    // 裸「原话」是引述用语（「他的原话是周五」），只有被请求动词管着才是要格式。
    /项目符号|要点符号|加粗|粗体|黑体|斜体|表情符号|emoji|超链接|时间戳|引号|逐字引用|markdown|(用|按|加|照)原话/i,
    new RegExp(`(加|放|带|贴|附)${NEAR}链接`),
  ],
};

/** 守卫表：同一句里出现这些形状 = 这一类当场不算命中（删他物 / 反向极性）。 */
const GUARDS: Partial<Record<NoteConflictId, RegExp[]>> = {
  drop_line: [
    // 英文侧的「删他物」由 EN_NEAR_LINE 在命中表里就地挡掉（夹了介词就不是删行）——否决是
    // 整句级的，这里再来一遍会连坐同一句里真正的删行诉求，所以只留中文这几条。
    // 「那条关于薪资的内容」「那行里的预算条目」「那行的占位」：行只是量词，宾语是别的东西。
    // 名词表刻意不含「内容」——「那行没内容就删掉」正是 issue #296 的原话。
    new RegExp(`${ZH_LINE}[^。；;.!?\\n]{0,4}(关于|有关|提到|里的|中的|里|中)`),
    new RegExp(`${ZH_LINE}的?[^。；;.!?\\n]{0,4}(占位|条目|名字|人名|数字|金额|拼音|说法)`),
    new RegExp(`${ZH_DROP}${NEAR}(关于|有关|提到)`),
    // 「分工那行去掉张三」：删除动词后面还跟着一个别的宾语。
    new RegExp(`${ZH_LINE}[^。；;.!?\\n]{0,6}${ZH_DROP_TAIL}`
      + "(?![^。；;.!?\\n]{0,2}(不|别|无|没))[^。；;.!?\\n]{0,1}[\\u4e00-\\u9fff]{2,6}$"),
  ],
  more_detail: [
    // 极性：「少写 / 去掉细节」是可满足的方向，不该被答成「写不了更详细」。
    /\b(remove|removing|drop|dropping|cut|cutting|delete|omit|skip|less|fewer|no|without|don't|do not)\b[^.!?\n]{0,20}\b(detail|details|context|background|depth|specifics|verbose|longer)\b/,
    new RegExp(`(去掉|删掉|删去|拿掉|省略|不要|别|少写|简短|简洁)${NEAR}(细节|详细|背景|上下文)`),
  ],
  formatting: [
    // 极性：「去掉引号 / 把 emoji 删掉」正是校验本来就在做的事，不该被答成「加不了格式」。
    /\b(remove|removing|drop|dropping|delete|omit|strip|take|no|without|don't|do not|avoid|skip|lose)\b[^.!?\n]{0,24}\b(bullet|bullets|markdown|bold|italic|italics|emoji|emojis|link|links|url|urls|timestamp|timestamps|time stamp|quote|quotes|quotation marks)\b/,
    // 「时间戳写错了」「the timestamp is wrong」是事实纠正，不是格式请求。
    /\b(timestamp|timestamps|time stamp|link|url|quote|quotes|bullet|bullets)\b[^.!?\n]{0,16}\b(is|are|was|were)\b[^.!?\n]{0,16}\b(wrong|incorrect|off|mistaken|not right)\b/,
    new RegExp(`(去掉|删掉|删去|拿掉|去除|不要|别加|别用|不用|不加|无需)${NEAR}(引号|emoji|表情|时间戳|链接|加粗|粗体|斜体|项目符号|markdown)`, "i"),
    new RegExp(`(引号|emoji|表情符号|时间戳|链接|加粗|粗体|斜体|项目符号)${NEAR}(去掉|删掉|删去|拿掉|去除|不要|不用)`, "i"),
    new RegExp(`(时间戳|引号|链接)${NEAR}(写错|不对|错了|有误)`),
  ],
};

function hits(id: NoteConflictId, clause: string): boolean {
  if (!PATTERNS[id].some((rx) => rx.test(clause))) return false;
  return !(GUARDS[id] ?? []).some((rx) => rx.test(clause));
}

/**
 * 备注里结构上无法满足的诉求（固定顺序、去重）。空备注 = 空表。
 * 判定只看备注文本，不调模型、不发请求——同一段文本永远得同一个答案。
 */
export function noteConflicts(note: string): NoteConflictId[] {
  const text = String(note ?? "").toLowerCase();
  if (!text.trim()) return [];
  const clauses = text.split(CLAUSE_SPLIT).filter((clause) => clause.trim());
  return NOTE_CONFLICT_ORDER.filter((id) => clauses.some((clause) => hits(id, clause)));
}
