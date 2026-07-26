"""provenance — 来源角色决策表（CONTRACT §0 宪法第 4 条 + §45）.

回声环的一刀（Zelin 2026-07-25 拍板）：**屏幕 OCR 不发起卡片**。看板自照、
AI 会话的建议、Slack/Gmail 画面的二手拷贝——回声全部经由屏幕通道进入
radar；关掉屏幕的「出生权」，回声环断在源头。屏幕内容保留两个职责：进
档案（ingest 笔记/wiki 价值不变）和佐证已有卡（relates_to fold、提及数、
完成证据）——这些不产生新卡也不发通知。

这张表管的是 obsidian radar 候选项的「出生资格」，特意做成显式、有限、
可枚举的纯数据：tests/test_provenance.py 用穷举证明它完备且无矛盾（有限
域上的穷举 == Z3 的可满足性检查，零依赖），并用 Hypothesis 性质测试钉死
「screen 永远不得 FULL」这类宪法条款。改这张表 = 修法：同步改 §45 的
誊本，并让测试告诉你有没有漏行。

三种裁决（对 new_proposal 的上限；relates_to fold 永远放行）：

- ``FULL``        — new_proposal 可依 high-confidence 直达提案列（card_sent）
- ``LIMITED``     — new_proposal 最高落备选（detected）；不通知、自然过期
- ``CORROBORATE`` — 不得发起新卡：triage 判 relates_to 就 fold，其余一律丢
                    （radar 记 echo_blocked 计数 + analytics 事件留痕）

维度取值由 :func:`normalize` 收敛——LLM 提取器给的字段不可信（None/大写/
臆造值都真实可能），一切不认识的输入落 ``unknown``，表对 unknown 行有
显式裁决，所以 :func:`verdict` 是全函数、永不 raise。
"""
from __future__ import annotations

# -- 域 ----------------------------------------------------------------------
PROVENANCES = ("screen", "audio", "unknown")
SPEAKERS = ("human", "zelin", "assistant", "system", "unknown")

# -- 裁决 --------------------------------------------------------------------
FULL = "full"
LIMITED = "limited"
CORROBORATE = "corroborate"
VERDICTS = (FULL, LIMITED, CORROBORATE)

# -- 法条本体（§45 有一份人话誊本；两边必须一致） -----------------------------
TABLE: dict[tuple[str, str], str] = {
    # 屏幕 OCR：一刀砍。Zoom 聊天/合规横幅两个白名单例外 Zelin 明确不要
    # （「zoom chat 大多不会在语音不说的情况下发重要请求」）。
    ("screen", "human"): CORROBORATE,
    ("screen", "zelin"): CORROBORATE,
    ("screen", "assistant"): CORROBORATE,
    ("screen", "system"): CORROBORATE,
    ("screen", "unknown"): CORROBORATE,
    # 会议音频：真人说话是合法的发起渠道——这是 screenpipe 链的正差所在。
    # assistant 语音（TTS/演示）按回声处理；system 提示音几乎不构成请求。
    ("audio", "human"): FULL,
    ("audio", "zelin"): FULL,
    ("audio", "assistant"): CORROBORATE,
    ("audio", "system"): LIMITED,
    # 会议转写常缺说话者标注；因标注缺失杀掉主价值渠道比放行更贵。
    ("audio", "unknown"): FULL,
    # 来源判不出：保守放行到备选——安静的安全网，不打扰。
    ("unknown", "human"): LIMITED,
    ("unknown", "zelin"): LIMITED,
    ("unknown", "assistant"): CORROBORATE,
    ("unknown", "system"): LIMITED,
    ("unknown", "unknown"): LIMITED,
}


def normalize(value: object, domain: tuple[str, ...]) -> str:
    """LLM 字段 -> 域内取值；一切不认识的输入（None/数字/大写/臆造词）落
    ``unknown``。normalize 是 verdict 全函数性质的地基。"""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in domain:
            return v
    return "unknown"


def verdict(provenance: object, speaker: object) -> str:
    """出生资格裁决。全函数：任意输入（含垃圾）都返回 VERDICTS 之一。"""
    key = (normalize(provenance, PROVENANCES), normalize(speaker, SPEAKERS))
    # TABLE 覆盖全部域组合（tests 穷举钉死）；.get 兜底只是防御性习惯。
    return TABLE.get(key, LIMITED)
