"""server/recaps.py — the web 会议纪要 page's server side (CONTRACT §63).

Two small things, both stdlib (+ optional PyYAML to read config.yaml):

1. **Recap settings** ``GET/PUT /api/settings/recap`` — the three knobs the
   pipeline reads (act/lib/config.py): ``enabled`` (default true),
   ``default_language`` (auto | zh | en), ``slack_draft_enabled`` (**default
   false**: a CLOSED recap is placed as a Slack *draft*, the send button stays
   the owner's). Effective value = settings_overrides.json flat key
   (``recap_enabled`` / ``recap_default_language`` / ``recap_slack_draft_enabled``)
   → config.yaml ``recap:`` block → default; PUT diff-writes the flat keys with
   the §15 semantics server/settings.py already implements for the model knobs.

2. **Local marks** ``POST /api/recaps/mark`` — 「复制」/「标记已发送」write
   ``state/recap/marks.json`` ``{key: {copied_at, sent_at}}``. This file is
   server-owned (act/recap.py never writes it; act/lib/recap_store.py only
   reads it into the ``recaps[]`` projection) and **no control flow reads a
   mark** — it is a badge, not a state transition.

server/ does not import act (§49): the key shape, the language vocabulary and
the override key names are mirrored from act/lib/recap_store.py /
act/lib/config.py and pinned by tests/test_server_paths_mirror.py.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML absent: config.yaml layer is skipped
    yaml = None  # type: ignore[assignment]

from server import paths, settings
from server.errors import InvalidFieldError, UnknownFieldError

# ---- mirrors (drift-pinned) ------------------------------------------------ #
# act/lib/recap_store.KEY_RE
KEY_RE = re.compile(r"^meeting:\d{4}-\d{2}-\d{2}T\d{4}-[a-z0-9-]{1,32}$")
# act/lib/config.RECAP_LANGUAGES
LANGUAGES: tuple = ("auto", "zh", "en")
# wire key → settings_overrides.json flat key (config._OVERRIDE_FIELDS)
OVERRIDE_KEYS = {"enabled": "recap_enabled",
                 "default_language": "recap_default_language",
                 "slack_draft_enabled": "recap_slack_draft_enabled"}
DEFAULTS = {"enabled": True, "default_language": "auto", "slack_draft_enabled": False}
MARKS: tuple = ("copied", "sent")

_BOOL_TRUE = ("true", "yes", "on", "1")
_BOOL_FALSE = ("false", "no", "off", "0")


def marks_path(home: Path) -> Path:
    # mirrors act/lib/recap_store.marks_path (STATE_DIR / recap / marks.json)
    return home / "state" / "recap" / "marks.json"


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# value coercion (mirror of config._coerce_bool / _coerce_recap_language)
# --------------------------------------------------------------------------- #
def coerce_bool(value) -> bool:
    """Strict bool: real bools, 0/1, the usual words; anything else raises
    (mirror of config._coerce_bool — "false" must never read as True)."""
    if isinstance(value, bool):
        return value
    word = str(value).strip().lower()   # 0 / 1 stringify into the word lists
    if word in _BOOL_TRUE:
        return True
    if word in _BOOL_FALSE:
        return False
    raise ValueError("not a boolean: %r" % (value,))


def coerce_language(value) -> str:
    v = str(value or "").strip().lower()
    if v not in LANGUAGES:
        raise ValueError("default_language must be one of %s" % ", ".join(LANGUAGES))
    return v


_COERCE = {"enabled": coerce_bool, "default_language": coerce_language,
           "slack_draft_enabled": coerce_bool}


def _coerce_or(field: str, value, default):
    try:
        return _COERCE[field](value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# layered read: overrides → config.yaml → default
# --------------------------------------------------------------------------- #
def _yaml_doc(home: Path) -> dict:
    """config.yaml as a dict; {} when PyYAML / the file / the shape is absent."""
    if yaml is None:
        return {}
    try:
        doc = yaml.safe_load(paths.config_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _config_block(home: Path) -> dict:
    """config.yaml ``recap:`` block as {wire key: raw value} for the keys it
    spells (slack_draft.enabled flattened); {} when absent / unreadable."""
    blk = _yaml_doc(home).get("recap")
    blk = blk if isinstance(blk, dict) else {}
    out = {k: blk[k] for k in ("enabled", "default_language") if k in blk}
    draft = blk.get("slack_draft")
    if isinstance(draft, dict) and "enabled" in draft:
        out["slack_draft_enabled"] = draft["enabled"]
    return out


def _base_values(home: Path) -> "tuple[dict, dict]":
    """(effective-before-overrides, source label per key)."""
    cfg = _config_block(home)
    values, source = dict(DEFAULTS), {}
    for field, default in DEFAULTS.items():
        if field in cfg:
            values[field] = _coerce_or(field, cfg[field], default)
        source[field] = "config" if field in cfg else "default"
    return values, source


def snapshot(home: Path) -> dict:
    """Wire shape (web/src/types.ts ``RecapSettings`` mirrors verbatim)::

        {"enabled": bool, "default_language": "auto|zh|en",
         "slack_draft_enabled": bool, "languages": [...],
         "source": {"enabled": "override|config|default", ...}}
    """
    overrides = settings.read_overrides(home)
    values, source = _base_values(home)
    for field, key in OVERRIDE_KEYS.items():
        raw = overrides.get(key)
        if raw is None:
            continue
        try:
            values[field], source[field] = _COERCE[field](raw), "override"
        except (TypeError, ValueError):
            pass  # the pipeline skips the bad entry too
    out = dict(values)
    out["languages"] = list(LANGUAGES)
    out["source"] = source
    return out


# --------------------------------------------------------------------------- #
# write: PUT /api/settings/recap
# --------------------------------------------------------------------------- #
def _reject_unknown(payload: dict, allowed) -> None:
    unknown = set(payload) - set(allowed)
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})


def _wanted(payload: dict) -> dict:
    """The validated subset of the three knobs the PUT carries."""
    _reject_unknown(payload, OVERRIDE_KEYS)
    if not payload:
        raise InvalidFieldError("nothing to save")
    wanted = {}
    for field in OVERRIDE_KEYS:
        if field not in payload:
            continue
        try:
            wanted[field] = _COERCE[field](payload[field])
        except (TypeError, ValueError) as exc:
            raise InvalidFieldError(str(exc), {"field": field})
    return wanted


def update(home: Path, payload: dict) -> dict:
    """Validate ``{enabled?, default_language?, slack_draft_enabled?}`` and
    diff-write the flat override keys (value == config/default → key deleted).
    Unknown keys → 400 UNKNOWN_FIELD; a bad value → 400 INVALID_FIELD."""
    wanted = _wanted(payload)
    overrides = settings.read_overrides(home)
    base, _source = _base_values(home)
    for field, value in wanted.items():
        key = OVERRIDE_KEYS[field]
        if value == base[field]:
            overrides.pop(key, None)      # diff-write: same as effective → no key
        else:
            overrides[key] = value
    settings.atomic_write_json(settings.settings_overrides_path(home), overrides)
    return snapshot(home)


# --------------------------------------------------------------------------- #
# marks: POST /api/recaps/mark
# --------------------------------------------------------------------------- #
def _read_marks(home: Path) -> dict:
    try:
        doc = json.loads(marks_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _require_key(payload: dict) -> str:
    key = payload.get("key")
    if not (isinstance(key, str) and KEY_RE.match(key)):
        raise InvalidFieldError("key must be a recap key", {"field": "key"})
    return key


def _require_mark(payload: dict) -> "tuple[str, bool]":
    which = payload.get("mark")
    if which not in MARKS:
        raise InvalidFieldError("mark must be copied or sent", {"field": "mark"})
    on = payload.get("on", True)
    if not isinstance(on, bool):
        raise InvalidFieldError("on must be a boolean", {"field": "on"})
    return which, on


def mark(home: Path, payload: dict) -> dict:
    """``{"key": "meeting:…", "mark": "copied"|"sent", "on": bool?}`` →
    ``{"ok": true, "key", "copied_at", "sent_at"}``. ``on`` defaults to true;
    false clears the stamp (「标记已发送」is a toggle)."""
    _reject_unknown(payload, ("key", "mark", "on"))
    key = _require_key(payload)
    which, on = _require_mark(payload)
    marks = _read_marks(home)
    entry = marks.get(key) if isinstance(marks.get(key), dict) else {}
    entry["%s_at" % which] = _iso_now() if on else None
    marks[key] = entry
    settings.atomic_write_json(marks_path(home), marks)
    return {"ok": True, "key": key, "copied_at": entry.get("copied_at"),
            "sent_at": entry.get("sent_at")}
