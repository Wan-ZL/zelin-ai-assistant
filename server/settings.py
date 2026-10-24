"""server/settings.py — the web Settings page's model knobs + the shared overrides helpers (CONTRACT §59 / §15).

1. **The model knobs** ``models.pipeline`` (every headless ``claude -p`` the app
   runs — the meeting recap) and ``models.fallback`` (D53: the
   ``--fallback-model`` every launch carries; ``off`` = no flag; default
   ``claude-opus-5[1m]``). ``GET /api/settings/models`` reports the *effective*
   value of each knob (settings_overrides.json flat key ``models_<knob>`` →
   config.yaml ``models.<knob>`` → default) plus the canonical id list the UI
   renders as its dropdown (rule 10: the catalog is server-owned, the web
   mirrors wire keys verbatim). ``PUT /api/settings/models`` validates and
   **diff-writes** ``state/settings_overrides.json`` (§15 v0.14 保存语义): a
   value equal to the config.yaml/default effective value DELETES the override
   key, a different one writes it; every other key in the file is preserved.
   The pipeline (act/lib/config.py ``_OVERRIDE_FIELDS``) reads the same keys.

2. **Shared helpers** the per-section settings modules use (recaps, display,
   settings_catalog): :func:`read_overrides`, :func:`atomic_write_json`,
   :func:`config_yaml_doc`, :func:`coerce_clock_time`.

v2.0.0 (Act pipeline retired; full text in tag ``full``): the
``models.dispatch`` knob and the "follow the Claude Code global default" mode
are gone — under §78 the headless claude runs with its own config dir, so there
is no global default to follow; a stored ``"follow"`` reads as the product
default. Retired with them: the writer of the owner's Claude Code settings file
(``GET/POST /api/claude-code/default-model``), the §67 skill store
(``GET/POST /api/skills``) and the §70 daily-loop knobs
(``/api/settings/daily-loop``). This module reads and writes nothing outside
``<home>``.

server/ mirrors the knob vocabulary from act/lib/config.py (pinned by
tests/test_server_paths_mirror.py) — the two sides must agree on what a valid
knob value is or the web would write something the pipeline ignores.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML absent: config.yaml layer is skipped
    yaml = None  # type: ignore[assignment]

from server import paths
from server.errors import ConflictError, InvalidFieldError, UnknownFieldError

# ---- mirrors of act/lib/config.py (drift-pinned) --------------------------- #
MODEL_PIPELINE = "pipeline"
LEGACY_MODEL_FOLLOW = "follow"
CANONICAL_MODELS = (
    "claude-fable-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
)
DEFAULT_MODEL_PIPELINE = "claude-opus-5"
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}$")
# D53 (mirror of config.MODEL_FALLBACK_OFF / DEFAULT_MODEL_FALLBACK)
MODEL_FALLBACK = "fallback"
MODEL_FALLBACK_OFF = "off"
DEFAULT_MODEL_FALLBACK = "claude-opus-5[1m]"
# every knob PUT /api/settings/models accepts
MODEL_KNOBS = (MODEL_PIPELINE, MODEL_FALLBACK)

# settings_overrides.json flat keys the pipeline reads (config._OVERRIDE_FIELDS)
OVERRIDE_KEY = "models_%s"

CLOCK_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

_MODEL_SHAPE_MSG = ("模型 id 只能含字母数字和 . _ - [ ]，≤64 字符，不能有空格 / "
                    "a model id is letters, digits and . _ - [ ] only, ≤64 chars, no spaces")


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def settings_overrides_path(home: Path) -> Path:
    # mirrors act/lib/config.SETTINGS_OVERRIDES_PATH (STATE_DIR / settings_overrides.json)
    return home / "state" / "settings_overrides.json"


# --------------------------------------------------------------------------- #
# shared coercions (mirror of config.coerce_clock_time; the bool twin retired with
# the daily-loop knobs in v2.0.0 — server/recaps.py and settings_catalog keep their own)
# --------------------------------------------------------------------------- #
def coerce_clock_time(value) -> str:
    m = CLOCK_TIME_RE.match(value.strip()) if isinstance(value, str) else None
    if m is None:
        raise ValueError("时间必须是 HH:MM（本地时间，如 22:00）/ time must be HH:MM local, e.g. 22:00")
    return "%02d:%s" % (int(m.group(1)), m.group(2))


# --------------------------------------------------------------------------- #
# model id validation (mirror of config.coerce_model / coerce_fallback_model)
# --------------------------------------------------------------------------- #
def coerce_model(value) -> str:
    """None / blank / the legacy "follow" (any case) → the product default; a
    well-formed id stays as typed; anything else raises ValueError."""
    if value is None:
        return DEFAULT_MODEL_PIPELINE
    if not isinstance(value, str):
        raise ValueError("模型必须是字符串 / model must be a string")
    s = value.strip()
    if not s or s.lower() == LEGACY_MODEL_FOLLOW:
        return DEFAULT_MODEL_PIPELINE
    if not MODEL_ID_RE.match(s):
        raise ValueError(_MODEL_SHAPE_MSG)
    return s


def coerce_fallback_model(value) -> str:
    """None / blank → the default id; "off" (any case) or boolean False → "off"
    (YAML 1.1 reads a bare ``off`` as False); a well-formed id stays as typed."""
    if value is None:
        return DEFAULT_MODEL_FALLBACK
    if value is False:
        return MODEL_FALLBACK_OFF
    if not isinstance(value, str):
        raise ValueError("模型必须是字符串 / model must be a string")
    return _coerce_fallback_str(value.strip())


def _coerce_fallback_str(s: str) -> str:
    """String half of coerce_fallback_model: blank → default; "off" → off; id shape gate."""
    if not s:
        return DEFAULT_MODEL_FALLBACK
    if s.lower() == MODEL_FALLBACK_OFF:
        return MODEL_FALLBACK_OFF
    if not MODEL_ID_RE.match(s):
        raise ValueError(_MODEL_SHAPE_MSG + "；off = 关闭回退 / off = no fallback")
    return s


def coerce_knob(knob: str, value) -> str:
    return coerce_fallback_model(value) if knob == MODEL_FALLBACK else coerce_model(value)


def is_canonical(value: str) -> bool:
    return value in CANONICAL_MODELS


def noncanonical_warning(knob: str, value: str) -> Optional[str]:
    """The free-text warning (D22 (e)); None for canonical ids."""
    if is_canonical(value):
        return None
    return ("%s 用了非 canonical 的模型 id「%s」——别名/后缀（[1m]、-eap…）随时可能下线，"
            "下线那天会议纪要会静默全败 / %s uses the non-canonical model id \"%s\" - "
            "aliases/suffixes ([1m], -eap...) can disappear any day and every recap "
            "would then fail silently" % (knob, value, knob, value))


def fallback_warning(value: str) -> Optional[str]:
    """D53: None for off, the product default and canonical ids (same rule as
    ``act/llm.fallback_is_canonical``)."""
    if value in (MODEL_FALLBACK_OFF, DEFAULT_MODEL_FALLBACK) or value in CANONICAL_MODELS:
        return None
    return ("fallback 用了非 canonical 的模型 id「%s」——别名/后缀下线那天，主模型不可用时的"
            "回退也会失败 / fallback uses the non-canonical model id \"%s\" - the day the "
            "alias/suffix retires, the fallback itself fails when the primary is unavailable"
            % (value, value))


# --------------------------------------------------------------------------- #
# overrides / config.yaml IO (shared)
# --------------------------------------------------------------------------- #
def _parse_overrides(text: str, p: Path) -> dict:
    """Overrides text → object; empty file = {}; not JSON / not an object →
    ConflictError (never overwrite what the owner had in there)."""
    try:
        doc = json.loads(text) if text.strip() else {}
    except ValueError:
        raise ConflictError(
            "state/settings_overrides.json is not valid JSON - fix it by hand "
            "before saving settings from the web", {"path": str(p)})
    if not isinstance(doc, dict):
        raise ConflictError(
            "state/settings_overrides.json must be a JSON object", {"path": str(p)})
    return doc


def read_overrides(home: Path) -> dict:
    """The overrides document, {} when absent. An unparsable file (or a non-
    object) raises ConflictError — the pipeline ignores such a file, but
    overwriting it from here would destroy whatever the owner had in it."""
    p = settings_overrides_path(home)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise ConflictError("settings_overrides.json is unreadable",
                            {"path": str(p), "error": str(exc)})
    return _parse_overrides(text, p)


def atomic_write_json(p: Path, doc: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)


def config_yaml_doc(home: Path) -> dict:
    """config.yaml as a dict; {} when PyYAML / the file / the shape is absent."""
    if yaml is None:
        return {}
    try:
        doc = yaml.safe_load(paths.config_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return doc if isinstance(doc, dict) else {}


# --------------------------------------------------------------------------- #
# layered read: overrides → config.yaml → default
# --------------------------------------------------------------------------- #
def _knob_default(knob: str) -> str:
    return DEFAULT_MODEL_FALLBACK if knob == MODEL_FALLBACK else DEFAULT_MODEL_PIPELINE


def _lenient_knob(knob: str, value) -> str:
    """config-file leniency: a bad shape reads as the knob's default."""
    try:
        return coerce_knob(knob, value)
    except ValueError:
        return _knob_default(knob)


def _config_models(home: Path) -> "tuple[dict, dict]":
    """(values, present): config.yaml ``models:`` coerced per knob + which knobs
    the file actually spells (``source`` label)."""
    blk = config_yaml_doc(home).get("models")
    blk = blk if isinstance(blk, dict) else {}
    values = {knob: _knob_default(knob) for knob in MODEL_KNOBS}
    present = {knob: knob in blk for knob in MODEL_KNOBS}
    for knob in MODEL_KNOBS:
        if present[knob]:
            values[knob] = _lenient_knob(knob, blk.get(knob))
    return values, present


def _effective_knob(knob: str, base: dict, present: dict, overrides: dict) -> "tuple[str, str]":
    """(value, source): a well-formed override wins; a malformed one is skipped."""
    raw = overrides.get(OVERRIDE_KEY % knob)
    if raw is not None:
        try:
            return coerce_knob(knob, raw), "override"
        except ValueError:
            pass
    return base[knob], ("config" if present[knob] else "default")


def models_snapshot(home: Path) -> dict:
    """Wire shape (web/src/types.ts ``ModelsSettings`` mirrors verbatim)::

        {"pipeline": "<id>", "fallback": "<id>|off", "off": "off",
         "pipeline_default": "claude-opus-5", "fallback_default": "claude-opus-5[1m]",
         "canonical": [...],
         "source": {"pipeline": "override|config|default", "fallback": ...},
         "warnings": ["...plain sentence per non-canonical knob..."]}
    """
    overrides = read_overrides(home)
    base, present = _config_models(home)
    out: dict = {"off": MODEL_FALLBACK_OFF, "pipeline_default": DEFAULT_MODEL_PIPELINE,
                 "fallback_default": DEFAULT_MODEL_FALLBACK,
                 "canonical": list(CANONICAL_MODELS), "source": {}, "warnings": []}
    for knob in MODEL_KNOBS:
        value, source = _effective_knob(knob, base, present, overrides)
        out[knob] = value
        out["source"][knob] = source
        warning = (fallback_warning(value) if knob == MODEL_FALLBACK
                   else noncanonical_warning(knob, value))
        if warning:
            out["warnings"].append(warning)
    return out


# --------------------------------------------------------------------------- #
# write: PUT /api/settings/models
# --------------------------------------------------------------------------- #
def _validated(payload: dict) -> dict:
    """Field whitelist (400 UNKNOWN_FIELD) + per-knob coercion (400 INVALID_FIELD)."""
    unknown = set(payload) - set(MODEL_KNOBS)
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    if not payload:
        raise InvalidFieldError("nothing to save: give pipeline and/or fallback")
    wanted: dict = {}
    for knob in (k for k in MODEL_KNOBS if k in payload):
        try:
            wanted[knob] = coerce_knob(knob, payload[knob])
        except ValueError as exc:
            raise InvalidFieldError(str(exc), {"field": knob})
    return wanted


def update_models(home: Path, payload: dict) -> dict:
    """Validate ``{"pipeline"?: str, "fallback"?: str}`` and diff-write the
    override keys (equal to the config.yaml/default effective value → key
    deleted). Returns the fresh :func:`models_snapshot`."""
    wanted = _validated(payload)
    base, _present = _config_models(home)
    overrides = read_overrides(home)
    for knob, value in wanted.items():
        key = OVERRIDE_KEY % knob
        if value == base[knob]:
            overrides.pop(key, None)
        else:
            overrides[key] = value
    atomic_write_json(settings_overrides_path(home), overrides)
    return models_snapshot(home)
