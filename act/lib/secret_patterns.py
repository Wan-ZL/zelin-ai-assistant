"""Built-in secret shapes + the mask — the ONE place both redaction sides read.

CONTRACT §15（telemetry 内容字段无条件密钥掩码）/ §19（密钥不出 Mac）。

``sanitize.scrub`` masks these in every outbound prompt; ``analytics.clip_content``
masks them in every user-typed telemetry field. Before P3a the two modules
imported each other for this list (analytics → sanitize for the patterns,
sanitize → analytics for the redaction event) — an import cycle flagged by
the structure gate. The shared piece now lives one layer DOWN: this module
imports nothing from act/, so either side can depend on it without seeing the
other. The Swift writer mirrors the same patterns in ``Analytics.clip``
(tests/test_telemetry_level.py drift-guard reads ``SECRET_PATTERNS`` here).
"""
from __future__ import annotations

import re

MASK = "[脱敏]"

# Safe, high-precision (low false-positive) shapes.
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),                 # Anthropic keys
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                       # OpenAI-style
    re.compile(r"xox[bpasr]-[A-Za-z0-9\-]{8,}"),              # Slack tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),                          # AWS access key id
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),                # GitHub tokens
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]


def _match_spans(s: str, patterns) -> set:
    """Index set of every character covered by any pattern match in ``s``."""
    positions: set = set()
    for pat in patterns:
        for m in pat.finditer(s):
            positions.update(range(m.start(), m.end()))
    return positions


def secret_positions(s: str, patterns=None) -> set:
    """Index set of every character of ``s`` that is secret material.

    两遍扫描：先扫原串；再把空白拼掉扫一遍并映射回原串下标——邮件式换行/
    空格会把 key 劈成两段，只有拼合后才能看出整条是密钥素材（§15 承诺任何
    设置下都不收集 key，劈开的尾段也是）。拼合可能把紧邻 key 的词也圈进来
    （无法与折行区分），宁可多掩不可半漏（fail safe）。
    """
    pats = SECRET_PATTERNS if patterns is None else patterns
    positions = _match_spans(s, pats)
    idx_map = [i for i, ch in enumerate(s) if ch != " "]
    compact = "".join(ch for ch in s if ch != " ")
    positions.update(idx_map[j] for j in _match_spans(compact, pats))
    return positions


def _skip_masked_run(s: str, positions: set, i: int) -> int:
    """从 ``i`` 起跳过一段连续密钥区间——夹在两段掩码之间的折行空格一并吞掉
    （它只是被 split 归一出来的换行痕迹）→ 区间之后的下标。"""
    while i < len(s):
        if i in positions:
            i += 1
        elif s[i] == " " and (i + 1) in positions:
            i += 1
        else:
            break
    return i


def mask_positions(s: str, positions: set) -> str:
    """每段连续的密钥区间折叠成一个 MASK；其余字符原样。"""
    out: list = []
    i = 0
    while i < len(s):
        if i in positions:
            out.append(MASK)
            i = _skip_masked_run(s, positions, i)
        else:
            out.append(s[i])
            i += 1
    return "".join(out)
