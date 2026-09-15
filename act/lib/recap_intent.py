"""act/lib/recap_intent.py — 出稿之前先问一句「这份纪要是干什么用的」（CONTRACT §63.11；§63.3 / §63.6 / §63.10 追记）。

第一版照旧按转写出（§63.3 / §63.10 一个字符没动），然后**从那一版自己身上**
推导出一组问题摆在它旁边；owner 答完，第二版按答案重出，而第一版作为
``baseline`` 永久留在记录上（issue #302：「Generate the first version as today,
then present a short set of questions next to it」）。

**纯模块**：不调模型、不读盘、不发请求——同一份记录 + 同一组答案永远得到同一组
问题与同一段指令（§63.5 预检的同一条纪律）。问题由规则从存着的正文推导，不是
一张写死的问卷（issue #302：「The questions are the point, so they should be
derived from the recap rather than fixed」）。

问题的**组成**（哪几条、什么 id、什么选项）是 daemon 数据、经 dashboard 投影上
wire（``recaps[].questions``，projection-only）；**问法**是 web 的 `text(zh, en)`
按 ``kind`` / 选项值查表（防腐 #10：不起第二套双语机制，client 也不自己造问题）。

问题表（``kind`` → 选项，两张都是 **add-only 闭表**，顺序即展示顺序）：

===========  ==============================  ==========================================
kind         选项                            从哪儿来
===========  ==============================  ==========================================
split        keep / drop / propose           `Split:` 行（或长版 split 节）逐条，帽 6 条
deadline     keep / drop                     `Deadline:` 行非填充值时才问
others       keep / drop                     #332 实测第二高频的删除类：对方的要求 / 归属 / 进度
detail       keep / drop                     #332 第三类：研究级细节与保留说法
audience     send / self                     发给别人 vs 自己看（语气与对冲不同）
own          own / decline                   issue #302 的第三条候选：这个工作流你认领吗
prior        compare / drop                  只有真存在上一份纪要时才问
===========  ==============================  ==========================================

答案上 wire 的形状是**字符串列表** ``["split1=drop", "aud=send"]``（不是 dict）：
inbox 的字节序列化器（`server/inbox_writer._dump_value`）只认 null / bool / 数字 /
字符串 / 列表，而 37 份 golden 里没有一处嵌套对象——把 dict 塞上 wire 会当场
TypeError，而扩写序列化器要连带重钉全部 golden 的字节形（本轮不做）。

答案里的 ``split<n>`` 指的是**上一版的第 n 条分工**，条目原文只在**围栏里**出现
（`prompt_block` 只写编号与动作，`baseline_block` 把编号表与上一版正文一起交给
`sanitize.fence_untrusted`）——模型自己写的正文来自不可信转写，宪法第 5 条照旧
适用，指令块里一个字都不许夹带它。

``prior=drop`` 是唯一一条**确定性落地**的答案（`recap_text.drop_prior`）：五行形
把第 4 行钉成模板自己规定的填充串（渲染因此整行略掉，§63.10），长版把 `changed`
那一节整节去掉——不靠 prompt 求模型别写（#332 实测两份样本那行都是填充值）。
"""
from __future__ import annotations

import re
from typing import Optional

from act.lib import recap_text

# --------------------------------------------------------------------------- #
# 词表（两张 add-only 闭表）与上限
# --------------------------------------------------------------------------- #
KIND_SPLIT = "split"
KIND_DEADLINE = "deadline"
KIND_OTHERS = "others"
KIND_DETAIL = "detail"
KIND_AUDIENCE = "audience"
KIND_OWN = "own"
KIND_PRIOR = "prior"

# kind → 选项（闭表，顺序即展示顺序；第一项不是「默认」——没答过的问题不发答案）
OPTIONS: dict = {
    KIND_SPLIT: ("keep", "drop", "propose"),
    KIND_DEADLINE: ("keep", "drop"),
    KIND_OTHERS: ("keep", "drop"),
    KIND_DETAIL: ("keep", "drop"),
    KIND_AUDIENCE: ("send", "self"),
    KIND_OWN: ("own", "decline"),
    KIND_PRIOR: ("compare", "drop"),
}
# 全局问题的 id → kind（顺序 = #332 实测的删除频次排序：对方的要求 / 研究级细节
# 排在 owner 自己的两条之前，因为它们是第二、第三高频的手删类别）
GLOBAL_IDS: tuple = ((KIND_OTHERS, KIND_OTHERS), (KIND_DETAIL, KIND_DETAIL),
                     ("aud", KIND_AUDIENCE), ("own", KIND_OWN), ("prior", KIND_PRIOR))
DEADLINE_ID = "dl"
SPLIT_PREFIX = KIND_SPLIT

# 逐条分工的帽（#332 实测：发出去那份里「分派给本人」的条目是 5 条）；
# 问题总数 = 6 + 截止 + 5 个全局 = 12，与答案上限**恰好相等**——问出来的每一条
# 都必须答得上去，否则面板会给一个送不出去的答案格
MAX_SPLIT_QUESTIONS = 6
MAX_ANSWERS = 12
MAX_QUESTIONS = MAX_ANSWERS
# 一条 subject 上 wire 的长度帽（逐条帽是 EN 240，投影 60 行 × 6 条要有界——防腐 #4）
MAX_SUBJECT_CHARS = 160

# id 与 wire 上一条答案的形状（`server/inbox_writer._RECAP_ANSWER_RE` 逐字镜像，
# tests/test_server_paths_mirror.py 钉漂移）
ID_RE = re.compile(r"^[a-z]{1,8}\d{0,2}$")
ANSWER_RE = re.compile(r"^[a-z]{1,8}\d{0,2}=[a-z_]{1,12}$")

# 五行形里这两行的位置（§63.3 的标签顺序是硬闸，位置即身份——§63.9 的同一条依据）
SPLIT_LINE = 1
DEADLINE_LINE = 2

# 「一条真信息，但不是一条可留可删的承诺」：模板自己规定的未分派串（查表，不是
# 对模型散文做正则——与 §63.10 的填充值判据同一条纪律）
UNASSIGNED: frozenset = frozenset({"not assigned", "未分配"})
# 分工行里一条与一条之间的分隔（模板要求 `owner: item` 对，多条用分号隔开）
_PAIR_SPLIT = re.compile(r"[;；\n]+")


def _fixed_kinds() -> dict:
    out = {DEADLINE_ID: KIND_DEADLINE}
    out.update({aid: kind for aid, kind in GLOBAL_IDS})
    return out


FIXED_IDS: dict = _fixed_kinds()


def kind_of(answer_id) -> Optional[str]:
    """一个答案 id 属于哪一类；认不出 = None（= 整条请求畸形，actd 诚实 noop）。"""
    if not (isinstance(answer_id, str) and ID_RE.match(answer_id)):
        return None
    if answer_id.startswith(SPLIT_PREFIX):
        tail = answer_id[len(SPLIT_PREFIX):]
        if tail.isdigit() and 1 <= int(tail) <= MAX_SPLIT_QUESTIONS:
            return KIND_SPLIT
        return None
    return FIXED_IDS.get(answer_id)


# --------------------------------------------------------------------------- #
# 从存着的那一版推导问题
# --------------------------------------------------------------------------- #
def _label_body(line: str, index: int) -> str:
    """一行去掉它的标签（英文记录、手改成中文的记录都认；标签不对 = 整行当正文）。"""
    text_ = str(line)
    for labels in (recap_text.LABELS_EN, recap_text.LABELS_ZH):
        if text_.startswith(labels[index]):
            return text_[len(labels[index]):].strip()
    return text_.strip()


def _real_item(item: str) -> bool:
    """这条是不是一条真承诺：模板的填充值与「未分配」都不是（查表）。"""
    body = str(item).strip()
    if not body:
        return False
    norm = body.strip().lower()
    return not (recap_text.is_filler_item(body) or norm in UNASSIGNED)


def _lines_of(rec) -> list:
    lines = rec.get("en") if isinstance(rec, dict) else None
    return [str(line) for line in lines] if len(lines or []) == recap_text.LINE_COUNT else []


def _clean_sections(rec) -> list:
    """长版的节数组里像样的那几节（手改坏的文件里什么都可能有）。"""
    sections = rec.get("sections_en") if isinstance(rec, dict) else None
    return [sec for sec in (sections or []) if isinstance(sec, dict)]


def _clean_items(sec: dict) -> list:
    """一节里像样的条目（非字符串的丢掉——`str()` 一个 dict 出来的是垃圾正文）。"""
    return [item for item in (sec.get("items") or []) if isinstance(item, str)]


def _section_items(rec, key: str) -> list:
    """长版里某一节的条目（只认像样的节与像样的条目）。"""
    out = []
    for sec in _clean_sections(rec):
        if sec.get("key") == key:
            out += _clean_items(sec)
    return out


def _is_sections(rec) -> bool:
    return recap_text.normalize_shape(rec.get("shape")) == recap_text.SHAPE_SECTIONS


def split_subjects(rec) -> list:
    """上一版里逐条分工的原文（≤ :data:`MAX_SPLIT_QUESTIONS` 条，逐条截到帽内）。

    五行形取 `Split:` 那一行按分号切开（模板逐字要求 `owner: item` 对，多条分号
    隔开）；长版取 `split` 那一节的条目。填充值与「未分配」不算一条——那是「没有
    分派」这条真信息，不是一份可留可删的责任。"""
    if not isinstance(rec, dict):
        return []
    if _is_sections(rec):
        raw = _section_items(rec, KIND_SPLIT)
    else:
        lines = _lines_of(rec)
        raw = _PAIR_SPLIT.split(_label_body(lines[SPLIT_LINE], SPLIT_LINE)) if lines else []
    kept = [" ".join(str(item).split()) for item in raw]
    return [item[:MAX_SUBJECT_CHARS] for item in kept if _real_item(item)][:MAX_SPLIT_QUESTIONS]


def _has_deadline(rec) -> bool:
    """上一版真记了一个截止日期吗（填充值 `none set` / `未定` 不算）。"""
    if _is_sections(rec):
        return any(_real_item(item) for item in _section_items(rec, KIND_DEADLINE))
    lines = _lines_of(rec)
    return bool(lines) and not recap_text.is_filler_line(lines[DEADLINE_LINE], DEADLINE_LINE)


def _question(answer_id: str, kind: str, subject: Optional[str] = None) -> dict:
    """一条问题的 wire 形（键恒在；``subject`` 只有逐条的那几个有正文）。"""
    return {"id": answer_id, "kind": kind, "options": list(OPTIONS[kind]), "subject": subject}


def derive(rec, has_priors: bool = False) -> list:
    """一条记录（或一条投影行）→ 这一份纪要该被问的问题，≤ :data:`MAX_QUESTIONS` 条。

    没正文 = 一条也不问（还没出过稿的会议没有可steer的东西）；`prior` 只在真有
    上一份纪要时出现（没有的话「较上次变化」那行本来就是填充值，§63.10 渲染时
    已经略掉了——#332 的「default it off」在那一层兑现，不必多问一句）。"""
    if not recap_text.has_body(rec):
        return []
    out = [_question("%s%d" % (SPLIT_PREFIX, i + 1), KIND_SPLIT, subject)
           for i, subject in enumerate(split_subjects(rec))]
    if _has_deadline(rec):
        out.append(_question(DEADLINE_ID, KIND_DEADLINE))
    for answer_id, kind in GLOBAL_IDS:
        if kind != KIND_PRIOR or has_priors:
            out.append(_question(answer_id, kind))
    return out[:MAX_QUESTIONS]


# --------------------------------------------------------------------------- #
# 答案：闸 → 指令块
# --------------------------------------------------------------------------- #
def _one_answer(value) -> Optional[tuple]:
    """``"split1=drop"`` → ``("split1", "drop")``；形状 / 词表外 = None。"""
    if not (isinstance(value, str) and ANSWER_RE.match(value)):
        return None
    answer_id, _, picked = value.partition("=")
    kind = kind_of(answer_id)
    if kind is None or picked not in OPTIONS[kind]:
        return None
    return answer_id, picked


def answers_ok(value) -> bool:
    """wire 上的 ``answers`` 合不合法：1.. :data:`MAX_ANSWERS` 条字符串、每条
    ``id=value`` 都在闭表里、id 不重复。**全有或全无**——一条认不出就是整条请求
    畸形（actd 诚实 noop，永不「猜掉」一个答案偷偷改写这份纪要）。"""
    if not isinstance(value, list) or not (1 <= len(value) <= MAX_ANSWERS):
        return False
    pairs = [_one_answer(item) for item in value]
    if None in pairs:
        return False
    ids = [pair[0] for pair in pairs]
    return len(set(ids)) == len(ids)


def clean_answers(value) -> list:
    """合法就原样（自己的副本），否则 ``[]``（= 这次生成没有答案）。"""
    return list(value) if answers_ok(value) else []


def answer_map(answers) -> dict:
    """``["split1=drop"]`` → ``{"split1": "drop"}``（畸形整批丢，与 :func:`answers_ok` 同口径）。"""
    return {pair[0]: pair[1] for pair in (_one_answer(item) for item in clean_answers(answers))
            if pair is not None}


def drops_prior(answers) -> bool:
    """owner 说了「这次不比上一份」吗——唯一一条确定性落地的答案（§63.11）。"""
    return answer_map(answers).get("prior") == "drop"


# 一条答案 → 喂给模型的那句英文指令。**永不夹带正文**：逐条的那几个只说编号，
# 条目原文只在 :func:`baseline_block` 的围栏里出现（宪法第 5 条）。
_INSTRUCTIONS: dict = {
    (KIND_SPLIT, "keep"): "Item %s: keep it, with its owner.",
    (KIND_SPLIT, "drop"): "Item %s: drop it entirely — it must not appear in any line or section.",
    (KIND_SPLIT, "propose"): ("Item %s: it is not agreed — record it as something the owner puts "
                              "forward, not as a decision (the Open line, or the proposed section)."),
    (KIND_DEADLINE, "keep"): "Deadline: keep the date as it was spoken.",
    (KIND_DEADLINE, "drop"): "Deadline: the owner does not want one on the record — write the empty form.",
    (KIND_OTHERS, "keep"): "Record the other party's requirements, ownership and status.",
    (KIND_OTHERS, "drop"): ("Do not record the other party's requirements, ownership or status — "
                            "only what this side decided or will do."),
    (KIND_DETAIL, "keep"): "Include the research-level detail and the hedges as they were spoken.",
    (KIND_DETAIL, "drop"): ("Stop at the decision and action level: no research-level detail, "
                            "no hedge tails such as not closed or untested."),
    (KIND_AUDIENCE, "send"): ("This recap will be sent to the other party: hedge anything that was "
                              "not actually agreed, and never state a proposal as a decision."),
    (KIND_AUDIENCE, "self"): "This recap is for the owner's own memory: be terse, no hedging for a reader.",
    (KIND_OWN, "own"): "The owner does own that project or workstream — write them as its owner.",
    (KIND_OWN, "decline"): ("The owner does not want to own that project or workstream — never write "
                            "them as its owner and do not assign its work to them."),
    (KIND_PRIOR, "compare"): "Compare against the prior recap quoted above.",
    (KIND_PRIOR, "drop"): ("Ignore any prior recap: the changed-since-last-plan part is written as "
                           "the empty form (the pipeline enforces this deterministically)."),
}


def _instruction(answer_id: str, picked: str) -> Optional[str]:
    kind = kind_of(answer_id)
    line = _INSTRUCTIONS.get((kind, picked)) if kind else None
    if line is None:
        return None
    return line % answer_id[len(SPLIT_PREFIX):] if kind == KIND_SPLIT else line


def prompt_block(answers) -> Optional[str]:
    """答案 → **trusted 侧**的指令块（``None`` = 没有答案 = 这一段根本不进 prompt）。

    只有编号与动作，一个字的正文都没有：`Item 3` 指的是
    :func:`baseline_block` 围栏里第 3 条。"""
    lines = [_instruction(answer_id, picked) for answer_id, picked in answer_map(answers).items()]
    kept = [line for line in lines if line]
    return "\n- ".join(["The owner answered these questions about what this recap is for — apply them:"]
                       + kept) + "\n" if kept else None


def baseline_block(subjects: list, body: str = "") -> Optional[str]:
    """答案指的那一版：编号表 + 上一版正文。**调用方必须把它交给
    `sanitize.fence_untrusted`**（`recap_text.build_prompt` 就是这么做的）——这段
    文字是模型自己从不可信转写里写出来的，不是指令。"""
    rows = ["%d. %s" % (i + 1, subject) for i, subject in enumerate(subjects or [])]
    parts = (["The items the numbered answers refer to:"] + rows) if rows else []
    if str(body or "").strip():
        parts += ["", "The previous version as it was written:", str(body)]
    return "\n".join(parts) if parts else None
