"""server/display.py — the web Settings page's 「显示」 section (CONTRACT §54.1
item 12; routes §49). stdlib only (config.yaml is read through
server.settings.config_yaml_doc, which degrades to {} without PyYAML).

Three display preferences the board applies as CSS custom properties on
``:root`` (web/src/styles/tokens.css is the single source of the value
mapping; web/src/displayPrefs.ts only writes ``data-*`` attributes):

- ``text_size``   s | m | l | xl        (default m)      → ``--text-scale``
- ``text_weight`` regular | medium | bold (default regular) → ``--weight-shift``
- ``stroke``      thin | normal | thick  (default normal)  → ``--stroke-w``

Effective value = settings_overrides.json flat key (``ui_display_text_size`` /
``ui_display_text_weight`` / ``ui_display_stroke``) → config.yaml ``ui.display``
block → default; ``PUT /api/settings/display`` diff-writes the flat keys with
the §15 semantics server/settings.py already implements for the model knobs
(value equal to the config.yaml/default effective value DELETES the key).

These keys have **no daemon reader**: act/lib/config.py ignores unknown
overrides keys by design (§15), so nothing is mirrored into
``config._OVERRIDE_FIELDS`` — the server is their only reader and writer. The
vocabulary lists ride along in the GET so the web renders its segmented
controls from server data (anti-corruption rule 10), never from a client copy.
"""
from __future__ import annotations

from pathlib import Path

from server import settings
from server.errors import InvalidFieldError, UnknownFieldError

# wire key → allowed values (order = the segmented control's left-to-right order)
VOCABULARY = {
    "text_size": ("s", "m", "l", "xl"),
    "text_weight": ("regular", "medium", "bold"),
    "stroke": ("thin", "normal", "thick"),
}
DEFAULTS = {"text_size": "m", "text_weight": "regular", "stroke": "normal"}
# wire key → settings_overrides.json flat key (server-only; the daemon ignores them)
OVERRIDE_KEYS = {field: "ui_display_%s" % field for field in VOCABULARY}
# wire key of the vocabulary list carried in the GET snapshot
VOCABULARY_KEYS = {"text_size": "text_sizes", "text_weight": "text_weights",
                   "stroke": "strokes"}


def coerce(field: str, value) -> str:
    """Lower-cased, stripped member of the field's vocabulary; anything else
    raises ValueError with a plain-language reason (the web toasts it)."""
    allowed = VOCABULARY[field]
    v = str(value if value is not None else "").strip().lower()
    if v not in allowed:
        raise ValueError("%s must be one of %s" % (field, ", ".join(allowed)))
    return v


# --------------------------------------------------------------------------- #
# layered read: overrides → config.yaml → default
# --------------------------------------------------------------------------- #
def _config_block(home: Path) -> dict:
    """config.yaml ``ui: display:`` block as {wire key: raw value} for the keys
    it spells; {} when PyYAML / the file / the shape is absent."""
    ui = settings.config_yaml_doc(home).get("ui")
    blk = ui.get("display") if isinstance(ui, dict) else None
    blk = blk if isinstance(blk, dict) else {}
    return {k: blk[k] for k in VOCABULARY if k in blk}


def _base_values(home: Path) -> "tuple[dict, dict]":
    """(effective-before-overrides, source label per key); a bad config.yaml
    value degrades to the default the way the daemon treats bad yaml."""
    cfg = _config_block(home)
    values, source = dict(DEFAULTS), {}
    for field, default in DEFAULTS.items():
        source[field] = "config" if field in cfg else "default"
        if field in cfg:
            try:
                values[field] = coerce(field, cfg[field])
            except ValueError:
                values[field] = default
    return values, source


def snapshot(home: Path) -> dict:
    """Wire shape (web/src/types.ts ``DisplaySettings`` mirrors verbatim)::

        {"text_size": "m", "text_weight": "regular", "stroke": "normal",
         "text_sizes": ["s","m","l","xl"], "text_weights": ["regular","medium","bold"],
         "strokes": ["thin","normal","thick"],
         "source": {"text_size": "override|config|default", ...}}
    """
    overrides = settings.read_overrides(home)
    values, source = _base_values(home)
    for field, key in OVERRIDE_KEYS.items():
        raw = overrides.get(key)
        if raw is None:
            continue
        try:
            values[field], source[field] = coerce(field, raw), "override"
        except ValueError:
            pass  # a bad hand-edited entry is skipped, like the daemon does
    out = dict(values)
    for field, list_key in VOCABULARY_KEYS.items():
        out[list_key] = list(VOCABULARY[field])
    out["source"] = source
    return out


# --------------------------------------------------------------------------- #
# write: PUT /api/settings/display
# --------------------------------------------------------------------------- #
def _wanted(payload: dict) -> dict:
    """The validated subset of the three knobs the PUT carries."""
    unknown = set(payload) - set(VOCABULARY)
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    if not payload:
        raise InvalidFieldError("nothing to save")
    wanted = {}
    for field in VOCABULARY:
        if field not in payload:
            continue
        try:
            wanted[field] = coerce(field, payload[field])
        except ValueError as exc:
            raise InvalidFieldError(str(exc), {"field": field})
    return wanted


def update(home: Path, payload: dict) -> dict:
    """Validate ``{text_size?, text_weight?, stroke?}`` and diff-write the flat
    override keys (value == config/default → key deleted, other keys untouched).
    Unknown keys → 400 UNKNOWN_FIELD; a value outside the vocabulary → 400
    INVALID_FIELD. Returns the fresh :func:`snapshot`."""
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
