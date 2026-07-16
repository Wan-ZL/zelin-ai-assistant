"""match_corpus — deterministic card-matching corpus + near-dupe scoring (§38).

Python SIBLING of ``shared/Sources/SearchMatch.swift`` (§37): :func:`normalize`
mirrors ``SearchMatch.normalize`` exactly — lowercase, strip ``-`` ``_`` ``.``
and all whitespace, CJK passes through — so "eb1" matches "EB-1A" on both
sides. If either side's normalization semantics ever change, mirror the other.

Three consumers, all LLM-free and pure (no IO, never raises):

- **alias derivation** (:func:`derive_aliases`): up to ~6 distinctive keyword
  tokens per card, drawn from its title/summary/sources/notes, shown next to
  the card in the triage/capture inventory so the matcher LLM can recognize a
  card whose frozen title is an unmatchable URL/path;
- **pre-pass ranking** (:func:`rank_candidates`): normalized-token overlap
  between an incoming candidate text and each card's corpus — the top hits get
  flagged 「最可能相关」 in the prompt before the LLM ever answers;
- **near-dupe detection** (:func:`score_pair` via act/lib/auto_merge.py):
  the deterministic signal behind auto merge suggestions (§38).

Tokenization: latin/digit runs are normalized as one token ("EB-1A" → "eb1a",
"v0.33.1" → "v0331"); CJK runs contribute character bigrams (plus the whole
run when short) — no segmenter, fully deterministic.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# Twin of SearchMatch.swift's separator set — keep in lockstep (§37/§38).
_SEPARATORS = frozenset("-_.")

# latin/digit run, possibly with inner separators ("EB-1A", "config.json")
_LATIN_RUN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# CJK unified ideographs (basic + ext A) + kana — the note languages here.
_CJK_RUN_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿]+")

# Generic tokens that identify nothing (english function words + pipeline
# vocabulary that appears on almost every card). Deliberately small: the
# doc-frequency penalty in derive_aliases handles corpus-specific noise.
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "your",
    "you", "are", "was", "will", "have", "has", "not", "but", "can",
    "http", "https", "www", "com", "org", "html", "htm", "php",
    "slack", "gmail", "email", "meeting", "note", "notes", "card",
    "todo", "task", "update", "updated", "new", "add", "fix",
    "radar", "quick",   # the §38 fold-note tags — on every folded card
})

# Pure-digit tokens shorter than this are dates/counters, not identifiers.
_MIN_DIGIT_LEN = 4
# A single shared token this long is a signal on its own (URL slug, video id).
STRONG_TOKEN_LEN = 6

MAX_ALIASES = 6


def normalize(text) -> str:
    """Python twin of ``SearchMatch.normalize`` (§37): lowercase + strip
    ``-``/``_``/``.``/whitespace so latin/digit runs compare separator-free;
    CJK and everything else passes through."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    return "".join(
        ch for ch in text.lower()
        if ch not in _SEPARATORS and not ch.isspace())


def _cjk_grams(run: str) -> list[str]:
    """Character bigrams of a CJK run, PLUS the whole run when it is short
    (2-4 chars — those runs ARE the word and read better as aliases). Bigrams
    are always emitted so a short mention ("推荐信") still intersects a longer
    phrasing ("推荐信初稿") of the same thing."""
    if len(run) < 2:
        return []
    grams = [run[i:i + 2] for i in range(len(run) - 1)]
    if len(run) <= 4:
        grams.append(run)
    return grams


def _keep(t: str) -> bool:
    if len(t) < 2 or t in _STOPWORDS:
        return False
    if t.isdigit() and len(t) < _MIN_DIGIT_LEN:
        return False
    return True


def tokens(text) -> set[str]:
    """Deterministic normalized token set of a text blob (see module doc)."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    out: set[str] = set()
    for m in _LATIN_RUN_RE.finditer(text):
        run = m.group(0)
        t = normalize(run)
        if _keep(t):
            out.add(t)
        # separator-split sub-tokens too ("www.youtube.com" must intersect a
        # plain "youtube" mention; both sides emit the same parts).
        for part in re.split(r"[._-]+", run):
            p = part.lower()
            if p != t and _keep(p):
                out.add(p)
    for m in _CJK_RUN_RE.finditer(text):
        out.update(_cjk_grams(m.group(0)))
    return out


def corpus_text(req) -> str:
    """One card's matching corpus: title + display_title + summary + source
    quotes/refs + notes. ``getattr`` throughout — works on plain objects and
    stays compatible before/after §37's display_title lands."""
    parts = [
        str(getattr(req, "title", "") or ""),
        str(getattr(req, "display_title", "") or ""),
        str(getattr(req, "summary", "") or ""),
        str(getattr(req, "notes", "") or ""),
    ]
    for s in (getattr(req, "sources", None) or []):
        if isinstance(s, dict):
            parts.append(str(s.get("quote") or ""))
            parts.append(str(s.get("ref") or ""))
    return "\n".join(p for p in parts if p)


def corpus_tokens(req) -> set[str]:
    return tokens(corpus_text(req))


def derive_aliases(req, doc_freq: Optional[dict] = None,
                   limit: int = MAX_ALIASES) -> list[str]:
    """Up to ``limit`` distinctive keyword aliases for one card.

    Ranking is deterministic: rarest across the registry first (``doc_freq``
    = token -> number of cards carrying it, from :func:`doc_frequencies`),
    then longest, then lexicographic. Tokens already inside the card's own
    (normalized) title are skipped — the title is already on the inventory
    line; aliases exist to add what it can't say.
    """
    title_norm = normalize(str(getattr(req, "title", "") or ""))
    cand = corpus_tokens(req)
    freq = doc_freq or {}

    def _key(t: str):
        return (int(freq.get(t, 1)), -len(t), t)

    out: list[str] = []
    for t in sorted(cand, key=_key):
        if t and t in title_norm:
            continue
        if len(t) > 32:   # a base64/hash-ish segment would bloat the line
            continue
        out.append(t)
        if len(out) >= limit:
            break
    return out


def doc_frequencies(token_sets: Iterable[set]) -> dict:
    """token -> number of cards whose corpus contains it."""
    freq: dict = {}
    for ts in token_sets:
        for t in ts:
            freq[t] = freq.get(t, 0) + 1
    return freq


def score_pair(a: set, b: set) -> tuple[float, list[str]]:
    """Normalized-token overlap between two token sets.

    Returns ``(score, matched_tokens)`` where score = |a∩b| / min(|a|,|b|)
    (overlap coefficient — robust when one side is a short one-liner). A
    single shared token is only a signal when it is long enough to be an
    identifier (``STRONG_TOKEN_LEN``); otherwise ≥2 shared tokens are
    required, else the score is 0. matched_tokens is longest-first (stable).
    """
    if not a or not b:
        return 0.0, []
    inter = a & b
    if not inter:
        return 0.0, []
    if len(inter) < 2 and not any(len(t) >= STRONG_TOKEN_LEN for t in inter):
        return 0.0, []
    score = len(inter) / min(len(a), len(b))
    return score, sorted(inter, key=lambda t: (-len(t), t))


def rank_candidates(text, reqs, top: int = 3,
                    min_score: float = 0.2) -> list[tuple[object, float, list[str]]]:
    """Deterministic pre-pass: rank ``reqs`` by overlap with ``text``.

    Returns up to ``top`` entries ``(req, score, matched_tokens)`` with
    score ≥ ``min_score``, best first (ties broken by id string so the
    prompt is stable across runs)."""
    incoming = tokens(text)
    if not incoming:
        return []
    scored = []
    for r in reqs:
        s, matched = score_pair(incoming, corpus_tokens(r))
        if s >= min_score:
            scored.append((r, s, matched))
    scored.sort(key=lambda e: (-e[1], str(getattr(e[0], "id", ""))))
    return scored[:top]
