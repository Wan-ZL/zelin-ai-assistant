"""act/lib/recap_timing.py — 会议纪要模型调用的超时随转写长度伸缩 + 「丢了」判线的重定（CONTRACT §63.15；§63.8 追记）。

issue #332 / #440 的实测：``act/recap.LLM_TIMEOUT_S`` 是定值 240 s，4,493 词的那场
首次生成超时、靠下一轮重试才在会后约 90 分钟落稿；2026-09-21 那场 6,160 词用了
约 213 s——离定值只差 27 s。转写越长，模型读得越久、写得越多，一个定值要么掐断长会、
要么让短会的一次崩溃等得太久。所以超时自此是**转写词数的函数**：

    llm_timeout_s(words) = clamp(BASE + PER_KWORD × words / 1000, BASE, MAX)

- ``BASE`` = §63.3 起的那个定值（**地板**：任何转写都不比以前更早被掐断）；
- ``PER_KWORD`` 按实测 ≈ 35 s / 千词取 60 s，留 ~1.7× 的余量；
- ``MAX`` 是天花板：再长的转写也不该让 ``state/recap/.lock`` 被一次调用占到下一个 cron 轮。

§63.8 的 ``LOST_AFTER_S``（「起了却迟迟没落笔 = 丢了」）当时**恰等于**「锁等待 + 模型
× 重试」的上界，所以它必须跟着一起变成函数：:func:`lost_after_s`（words）=
``LOCK_WAIT_S + MODEL_CALLS_PER_RUN × llm_timeout_s(words)``——``recap_requests`` 按
这一行记录上的 ``transcript_words`` 算判线，并把算出来的秒数 add-only 地写进回执
（``generate_request.lost_after_s``），页面据它说「超过 N 分钟」而不是写死 10。词数
未知（OPEN 行的阶段稿、老记录）= 地板那条线 = 原来的 10 分钟，一字不变。

纯函数、stdlib-only；``act/recap.py`` 与 ``act/lib/recap_requests.py`` 都从这里取数
（一份真源，防腐 #9）。
"""
from __future__ import annotations

# 按钮入口等锁的上限（§63.1）——act/recap.py 自此从这里取，它是 lost_after_s 的一项
LOCK_WAIT_S = 120.0
# 模型调用超时的地板 = §63.3 起的定值 240 s
LLM_TIMEOUT_BASE_S = 240.0
# 每 1,000 个转写词加多少秒（实测 6,160 词 ≈ 213 s ≈ 35 s / 千词；取 60 留余量）
LLM_TIMEOUT_PER_KWORD_S = 60.0
# 天花板：一次调用最多 15 分钟
LLM_TIMEOUT_MAX_S = 900.0
# 一次出稿最多几次模型调用（§63.3：一次 + 重试一次）
MODEL_CALLS_PER_RUN = 2
# 词数的天花板：四小时的会也不到十万词；再大的数只可能来自手改坏的记录，封住它免得算术溢出
MAX_WORDS = 10_000_000


def _words(value) -> int:
    """记录上的 ``transcript_words``：真数且 ≥ 0 才算（``bool`` 是 ``int`` 子类；None / 字符串 /
    nan / inf / 大到离谱的数——手改坏的记录里都见得到——一律 = 0 = 地板，永不抛：这个数算不出来
    不许把整个 ``recaps[]`` 投影拖死）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        words = int(value)
    except (ValueError, OverflowError):     # float("nan") / float("inf")
        return 0
    return max(0, min(words, MAX_WORDS))


def llm_timeout_s(words=None) -> float:
    """这份转写的一次模型调用最多等多久（秒）：地板 + 每千词的增量，封在天花板下。"""
    scaled = LLM_TIMEOUT_BASE_S + LLM_TIMEOUT_PER_KWORD_S * _words(words) / 1000.0
    return float(min(LLM_TIMEOUT_MAX_S, max(LLM_TIMEOUT_BASE_S, scaled)))


def lost_after_s(words=None) -> float:
    """§63.8 的「丢了」判线 = 一次成功生成的上界：锁等待 + 模型调用 × 重试次数，
    模型那一项按同一份转写的词数伸缩（词数未知 = 地板 = 原来的 10 分钟）。"""
    return LOCK_WAIT_S + MODEL_CALLS_PER_RUN * llm_timeout_s(words)
