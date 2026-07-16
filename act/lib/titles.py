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


def _url_title(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    domain = (parsed.netloc or "").split("@")[-1].split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    # video-id style query params beat the path (youtube watch?v=…);
    # urlparse strips the leading "?" so v= may open the query string.
    m = re.search(r"(?:^|&)v=([^&]+)", parsed.query or "")
    if m:
        return f"{domain} ▸ {m.group(1)}"
    segments = [s for s in (parsed.path or "").split("/") if s]
    while segments and segments[-1].lower() in _NOISE_SEGMENTS:
        segments.pop()
    if not segments:
        return domain or url
    return f"{domain} ▸ {segments[-1]}"


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


def sanitize_title(title) -> str:
    """Deterministic readable fallback for a frozen registry title (§37).

    Pure and total: any input comes back as a non-empty display string when
    the title itself is non-empty (empty/None passes through as "")."""
    if title is None:
        return ""
    t = " ".join(str(title).split()).strip()
    if not t:
        return ""
    if _URL_RE.match(t):
        out = _url_title(t)
    elif _PATH_RE.match(t):
        out = t.rstrip("/").rsplit("/", 1)[-1] or t
    elif len(t) > _LONG_TEXT:
        out = _clip_clause(t)
    else:
        out = t
    return clip_title(out) or t[:MAX_DISPLAY_TITLE]
