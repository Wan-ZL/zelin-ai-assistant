"""titles — deterministic display-title sanitizer (CONTRACT §37).

The registry ``title`` field is FROZEN (it is the dedupe / re-raise identity
anchor — ``registry._same_source_and_title`` matches on it), so making board
titles readable must happen at PROJECTION time. ``sanitize_title`` is the last
deterministic rung of the §37 fallback chain (user display_title → LLM
display_title → sanitize(title) → title): pure, no IO, never raises — it
turns the three classic unreadable title shapes into one readable line:

- http(s) URL      -> "domain ▸ last-meaningful-path-segment" (video id, slug)
- filesystem path  -> the last path component
- overlong text    -> first sentence/clause, clipped to ~48 chars with an
                      ellipsis

Anything already short and plain passes through with whitespace collapsed.
Legacy cards need zero migration: the chain runs on every dashboard pass.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Hard ceiling for any display title on the wire (user titles are validated to
# <=64 at the boundaries; LLM/harvest titles are clipped to it).
MAX_DISPLAY_TITLE = 64

# Plain-text titles longer than this get clause-clipped by sanitize_title.
_LONG_TEXT = 60
_CLIP_AT = 48

_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
# an absolute (or ~) filesystem path with at least one separator and no spaces
_PATH_RE = re.compile(r"^(?:~|/)[^ ]*/[^ ]+$")

# path segments that carry no meaning on their own — skip backwards past them
# when picking a URL's "last meaningful segment" (watch?v=… query wins first).
_NOISE_SEGMENTS = frozenset(
    {"index.html", "index.htm", "index.php", "view", "watch", "p", "s"})


def clip_title(text, limit: int = MAX_DISPLAY_TITLE):
    """Whitespace-collapse + hard-clip a candidate display title.

    Returns the cleaned string, or None when the input is not a usable title
    (non-str / empty after collapsing) — the fail-closed shape every consumer
    (LLM keys, CARD TITLE harvest line) branches on.
    """
    if not isinstance(text, str):
        return None
    t = " ".join(text.split()).strip()
    if not t:
        return None
    if len(t) > limit:
        t = t[: limit - 1].rstrip() + "…"
    return t


def _url_domain(netloc: str) -> str:
    """Bare host: userinfo + port dropped, leading ``www.`` stripped."""
    domain = (netloc or "").split("@")[-1].split(":")[0]
    return domain[4:] if domain.startswith("www.") else domain


def _video_id(query: str):
    """``v=<id>`` query param (youtube watch?v=…) or None. urlparse strips the
    leading "?" so ``v=`` may open the query string."""
    m = re.search(r"(?:^|&)v=([^&]+)", query)
    return m.group(1) if m else None


def _last_meaningful_segment(path: str):
    """Last path segment that is not a noise word (index.html / view / …)."""
    segments = list(filter(None, path.split("/")))
    while segments and segments[-1].lower() in _NOISE_SEGMENTS:
        segments.pop()
    return segments[-1] if segments else None


def _url_title(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    domain = _url_domain(parsed.netloc)
    # video-id style query params beat the path
    tail = _video_id(parsed.query) or _last_meaningful_segment(parsed.path)
    if tail is None:
        return domain or url
    return f"{domain} ▸ {tail}"


# Sentence/clause boundary for _clip_clause (review fix). The old
# ``[。！？!?；;.]\s*`` matched a BARE mid-word ASCII dot (\s* matches empty),
# so "config.json" / "v0.33.1" / "domain.com" inside the first 48 chars became
# a "sentence end" and legacy long titles projected as garbage ("把 config").
#  - CJK enders 。！？； are unconditional boundaries;
#  - ASCII . needs whitespace/EOL after AND ≥3 word chars before (skips
#    abbreviations like "Dr." / "Mr." — a real sentence rarely ends in a
#    1-2 letter word);
#  - ASCII ! ? ; need whitespace/EOL after.
_CLAUSE_BOUNDARY_RE = re.compile(
    r"[。！？；]|(?<=\w\w\w)\.(?=\s|$)|[!?;](?=\s|$)")


def _clip_clause(text: str) -> str:
    """First sentence/clause of an overlong title, clipped to ~_CLIP_AT chars.

    Both branches append "…" — the result is always a truncation of a longer
    title, and the ellipsis is the honest signal for it (review fix: the
    boundary branch used to return without one)."""
    m = _CLAUSE_BOUNDARY_RE.search(text)
    if m and 0 < m.start() <= _CLIP_AT:
        return text[: m.start()] + "…"
    clipped = text[:_CLIP_AT].rstrip()
    # prefer breaking at the last comma/space inside the window
    m2 = re.search(r"^(.{12,}?)[，,、\s][^，,、\s]*$", clipped)
    if m2:
        clipped = m2.group(1)
    return clipped + "…"


def is_unreadable_title(title) -> bool:
    """§37.1 条件强制判定 — 冻结 title 是否属于三种不可读形态之一。

    True = URL / 文件系统路径 / 超长截断文本（>_LONG_TEXT，direct-run 的
    「用户原话截 80」即落在这档）——executor.build_prompt 据此把 CARD TITLE
    从自愿改为本轮强制。与 sanitize_title 共用同一组判定正则，另对含空格
    路径（"/Users/z/My Files/a.pdf"）单向放宽：首字符是 / 或 ~、首个空格前
    的段除首字符外还含 /（首段自身呈现路径结构，排除「~3 天完成 A/B 测试」
    这类约数开头的 prose）、全串 ≥2 个 / 即视为路径——_PATH_RE 本身不动
    （它兼任显示 fallback 的截段依据，放宽会牵连 sanitize_title 的输出）。
    非 str / 空白 title 返回 False（fail 向自愿制，绝不因坏输入硬性打扰
    agent）。纯函数，不抛异常。"""
    if not isinstance(title, str):
        return False
    t = " ".join(title.split()).strip()
    if not t:
        return False
    return _is_spaced_path(t) or _is_unreadable_shape(t)


def _is_spaced_path(t: str) -> bool:
    """Filesystem path that carries spaces (one-way relaxation of _PATH_RE):
    first char / or ~, the first token itself structured by a slash, and at
    least two slashes overall."""
    return t[0] in "/~" and "/" in t.split()[0][1:] and t.count("/") >= 2


def _is_unreadable_shape(t: str) -> bool:
    """URL / plain path / overlong text — the three §37 shapes."""
    return bool(_URL_RE.match(t) or _PATH_RE.match(t)) or len(t) > _LONG_TEXT


def _readable_form(t: str) -> str:
    """The readable rung for a collapsed, non-empty title (§37 chain)."""
    if _URL_RE.match(t):
        return _url_title(t)
    if _PATH_RE.match(t):
        return t.rstrip("/").rsplit("/", 1)[-1] or t
    if len(t) > _LONG_TEXT:
        return _clip_clause(t)
    return t


def sanitize_title(title) -> str:
    """Deterministic readable fallback for a frozen registry title (§37).

    Pure and total: any input comes back as a non-empty display string when
    the title itself is non-empty (empty/None passes through as "")."""
    if title is None:
        return ""
    t = " ".join(str(title).split()).strip()
    if not t:
        return ""
    return clip_title(_readable_form(t)) or t[:MAX_DISPLAY_TITLE]
