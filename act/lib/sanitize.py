"""Local pre-send redaction — mask terms BEFORE anything goes to the Claude API.

A deterministic, offline scrub applied at every prompt boundary (executor,
analyze, radar_slack, radar_gmail, quick_capture). It rewrites only the OUTBOUND
prompt copy; the registry / notes / vault keep the original text untouched.

Opt-in: config.redaction_enabled (default False — masking changes what the model
sees, so Zelin turns it on deliberately in Settings). When on:
  - user terms from config.redaction_terms_file (one per line; `#` comment;
    `re:<pattern>` = regex; everything else = case-insensitive literal)
  - built-in secret patterns (if config.redaction_mask_secrets) — the
    "密钥不出 Mac" belt-and-suspenders: API keys / tokens / private keys.

The matched content is NEVER logged; only the mask COUNT is surfaced.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

MASK = "[脱敏]"

# Built-in secret patterns — safe, high-precision (low false-positive) shapes.
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),                 # Anthropic keys
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                       # OpenAI-style
    re.compile(r"xox[bpasr]-[A-Za-z0-9\-]{8,}"),              # Slack tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),                          # AWS access key id
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),                # GitHub tokens
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]

_terms_cache: dict = {}


def _load_terms(path: Path) -> list:
    """Return [(kind, pattern_or_str)]; cached by (path, mtime)."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    key = str(path)
    if _terms_cache.get(key, (None,))[0] == mtime:
        return _terms_cache[key][1]
    rules = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("re:"):
                try:
                    rules.append(("re", re.compile(line[3:], re.IGNORECASE)))
                except re.error:
                    pass
            else:
                rules.append(("lit", line))
    except OSError:
        return []
    _terms_cache[key] = (mtime, rules)
    return rules


def scrub(text: str, cfg=None) -> tuple[str, int]:
    """Return (possibly-masked text, number of masks applied). Never raises."""
    if not text:
        return text, 0
    if cfg is None:
        try:
            from act.lib import config
            cfg = config.load_config()
        except Exception:  # noqa: BLE001
            return text, 0
    if not getattr(cfg, "redaction_enabled", False):
        return text, 0

    count = 0
    out = text

    # 1) user literal + regex terms
    terms_file = getattr(cfg, "redaction_terms_file", None)
    if terms_file:
        for kind, pat in _load_terms(Path(terms_file).expanduser()):
            if kind == "lit":
                if pat.lower() in out.lower():
                    out, n = re.subn(re.escape(pat), MASK, out, flags=re.IGNORECASE)
                    count += n
            else:
                out, n = pat.subn(MASK, out)
                count += n

    # 2) built-in secrets
    if getattr(cfg, "redaction_mask_secrets", True):
        for pat in _SECRET_PATTERNS:
            out, n = pat.subn(MASK, out)
            count += n

    if count:
        try:
            from act.lib import analytics
            analytics.log_event("redaction", masks=count)   # count only, never content
        except Exception:  # noqa: BLE001
            pass
    return out, count


def scrub_text(text: str, cfg=None) -> str:
    """Convenience: return just the scrubbed text."""
    return scrub(text, cfg)[0]
