"""server/settings.py — the web Settings page's server side (CONTRACT §59, D22).

Two things, both stdlib (+ optional PyYAML for reading config.yaml):

1. **The two model knobs** ``models.dispatch`` / ``models.pipeline``.
   ``GET /api/settings/models`` reports the *effective* value of each knob
   (settings_overrides.json flat key ``models_<mode>`` → config.yaml
   ``models.<mode>`` → default ``follow``) plus the canonical id list the UI
   renders as its dropdown (rule 10: the catalog is server-owned, the web
   mirrors wire keys verbatim). ``PUT /api/settings/models`` validates and
   **diff-writes** ``state/settings_overrides.json`` exactly the way the Mac
   app did (§15 v0.14 保存语义): a value equal to the config.yaml/default
   effective value DELETES the override key, a different one writes it; every
   other key in the file is preserved byte-for-byte as JSON. The pipeline
   (act/lib/config.py ``_OVERRIDE_FIELDS``) reads the same two keys.

3. **The daily self-improvement loop's knobs** (CONTRACT §62, D10) —
   ``GET/PUT /api/settings/daily-loop``: ``enabled`` / ``time`` (local HH:MM)
   / ``max_proposals_per_day`` / ``stale_days`` / ``trash_retention_days``,
   same layered read (override ``daily_loop_<field>`` → config.yaml
   ``daily_loop.<field>`` → default) and the same diff-write; the pipeline's
   ``config._OVERRIDE_FIELDS`` reads the identical flat keys and actd picks a
   change up on its next pass (no restart).

2. **The Claude Code global default** — ``~/.claude/settings.json`` ``model``,
   what every ``follow`` call inherits. ``GET /api/claude-code/default-model``
   reads it; ``POST`` edits **only the ``model`` key** after copying the file
   to ``settings.json.bak-<UTC ts>`` in the same directory, preserving every
   other key and the file mode; an unparsable file is refused (409
   ``CONFLICT``) — never overwritten. Nothing rewrites that file on launch
   (D22 (d)): the owner clicks 「设为 …」 explicitly.

server/ does not import act (§49): the follow sentinel, mode names, canonical
list and id shape are **mirrored** from act/lib/config.py and pinned by
tests/test_server_paths_mirror.py — the two sides must agree on what a valid
knob value is or the web would write something the daemon ignores.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML absent: config.yaml layer is skipped
    yaml = None  # type: ignore[assignment]

from server import paths
from server.errors import ConflictError, InvalidFieldError, UnknownFieldError

# ---- mirrors of act/lib/config.py (drift-pinned) --------------------------- #
MODEL_FOLLOW = "follow"
MODEL_MODES = ("dispatch", "pipeline")
CANONICAL_MODELS = (
    "claude-fable-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
)
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}$")

# settings_overrides.json flat keys the pipeline reads (config._OVERRIDE_FIELDS)
OVERRIDE_KEY = "models_%s"

# ---- §62 daily loop knobs — mirrors of act/lib/config.py (drift-pinned) ---- #
DAILY_LOOP_FIELDS = ("enabled", "time", "max_proposals_per_day", "stale_days",
                     "trash_retention_days")
DAILY_LOOP_DEFAULTS = {"enabled": True, "time": "03:30", "max_proposals_per_day": 5,
                       "stale_days": 45, "trash_retention_days": 90}
DAILY_LOOP_KEY = "daily_loop_%s"
CLOCK_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_BOOL_WORDS = {"true": True, "yes": True, "on": True, "1": True,
               "false": False, "no": False, "off": False, "0": False}

BACKUP_SUFFIX = ".bak-%s"   # settings.json.bak-20260901T120000Z


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def settings_overrides_path(home: Path) -> Path:
    # mirrors act/lib/config.SETTINGS_OVERRIDES_PATH (STATE_DIR / settings_overrides.json)
    return home / "state" / "settings_overrides.json"


def claude_code_settings_path() -> Path:
    # mirrors act/llm.claude_code_settings_path (Path.home() follows $HOME)
    return Path.home() / ".claude" / "settings.json"


# --------------------------------------------------------------------------- #
# model id validation (mirror of config.coerce_model)
# --------------------------------------------------------------------------- #
def coerce_model(value) -> str:
    """None / blank / "follow" (any case) → "follow"; a well-formed id stays as
    typed; anything else raises ValueError with a plain-language reason."""
    if value is None:
        return MODEL_FOLLOW
    if not isinstance(value, str):
        raise ValueError("模型必须是字符串 / model must be a string")
    s = value.strip()
    if not s or s.lower() == MODEL_FOLLOW:
        return MODEL_FOLLOW
    if not MODEL_ID_RE.match(s):
        raise ValueError(
            "模型 id 只能含字母数字和 . _ - [ ]，≤64 字符，不能有空格 / "
            "a model id is letters, digits and . _ - [ ] only, ≤64 chars, no spaces")
    return s


def is_canonical(value: str) -> bool:
    return value == MODEL_FOLLOW or value in CANONICAL_MODELS


def noncanonical_warning(mode: str, value: str) -> Optional[str]:
    """The free-text warning (D22 (e)); None for follow / canonical ids."""
    if is_canonical(value):
        return None
    return ("%s 用了非 canonical 的模型 id「%s」——别名/后缀（[1m]、-eap…）随时可能下线，"
            "下线那天这些调用会静默全败 / %s uses the non-canonical model id \"%s\" - "
            "aliases/suffixes ([1m], -eap...) can disappear any day and every call "
            "would then fail silently" % (mode, value, mode, value))


# --------------------------------------------------------------------------- #
# §62 daily loop knob validation (mirror of config._coerce_bool / coerce_clock_time
# / _nonneg_int — the strict, overrides-path shapes)
# --------------------------------------------------------------------------- #
def coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    key = str(value).strip().lower() if isinstance(value, (int, str)) else ""
    if key in _BOOL_WORDS:
        return _BOOL_WORDS[key]
    raise ValueError("enabled 必须是 true/false / enabled must be true or false")


def coerce_clock_time(value) -> str:
    m = CLOCK_TIME_RE.match(value.strip()) if isinstance(value, str) else None
    if m is None:
        raise ValueError("time 必须是 HH:MM（本地时间，如 03:30）/ time must be HH:MM local, e.g. 03:30")
    return "%02d:%s" % (int(m.group(1)), m.group(2))


def _int_or_none(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_count(value) -> int:
    n = _int_or_none(value)
    if n is None or n < 0:
        raise ValueError("必须是非负整数 / must be a non-negative integer")
    return n


_DAILY_LOOP_COERCE = {"enabled": coerce_bool, "time": coerce_clock_time,
                      "max_proposals_per_day": coerce_count, "stale_days": coerce_count,
                      "trash_retention_days": coerce_count}


def coerce_daily_loop(field: str, value):
    """Strict (overrides-path) coercion for one knob; ValueError with a plain reason."""
    return _DAILY_LOOP_COERCE[field](value)


def _lenient_daily_loop(field: str, value, default):
    """config.yaml-path coercion (mirror of config._apply_daily_loop_block):
    bad value → default; counts clamp at 0 (a negative day count means off)."""
    if field in ("enabled", "time"):
        try:
            return coerce_daily_loop(field, value)
        except (TypeError, ValueError):
            return default
    n = _int_or_none(value)
    return max(0, default if n is None else n)


def _config_daily_loop(home: Path) -> "tuple[dict, dict]":
    """(values, present) from config.yaml ``daily_loop:``; absent/bad = defaults."""
    values, present = dict(DAILY_LOOP_DEFAULTS), {f: False for f in DAILY_LOOP_FIELDS}
    blk = _config_block(home, "daily_loop")
    for field in DAILY_LOOP_FIELDS:
        if field in blk:
            present[field] = True
            values[field] = _lenient_daily_loop(field, blk.get(field), values[field])
    return values, present


def _config_block(home: Path, name: str) -> dict:
    if yaml is None:
        return {}
    try:
        doc = yaml.safe_load(paths.config_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    blk = doc.get(name) if isinstance(doc, dict) else None
    return blk if isinstance(blk, dict) else {}


def daily_loop_snapshot(home: Path) -> dict:
    """Wire shape (web/src/types.ts ``DailyLoopSettings`` mirrors verbatim)::

        {"enabled": bool, "time": "HH:MM", "max_proposals_per_day": int,
         "stale_days": int, "trash_retention_days": int,
         "source": {"<field>": "override|config|default", ...}}
    """
    overrides = read_overrides(home)
    values, present = _config_daily_loop(home)
    out: dict = {"source": {}}
    for field in DAILY_LOOP_FIELDS:
        value, source = values[field], ("config" if present[field] else "default")
        raw = overrides.get(DAILY_LOOP_KEY % field)
        if raw is not None:
            try:
                value, source = coerce_daily_loop(field, raw), "override"
            except ValueError:
                pass  # the pipeline skips the bad entry too
        out[field] = value
        out["source"][field] = source
    return out


def _validated(payload: dict, fields: tuple, coerce, empty_msg: str) -> dict:
    """Field whitelist (400 UNKNOWN_FIELD) + per-field coercion (400
    INVALID_FIELD with the plain reason) → {field: value} for the given ones."""
    _check_shape(payload, fields, empty_msg)
    wanted: dict = {}
    for field in (f for f in fields if f in payload):
        try:
            wanted[field] = coerce(field, payload[field])
        except ValueError as exc:
            raise InvalidFieldError(str(exc), {"field": field})
    return wanted


def _check_shape(payload: dict, fields: tuple, empty_msg: str) -> None:
    unknown = set(payload) - set(fields)
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    if not payload:
        raise InvalidFieldError(empty_msg)


def _diff_write(home: Path, wanted: dict, base: dict, key_fmt: str) -> None:
    """§15 v0.14 保存语义：equal to the config.yaml/default effective value →
    the override key is deleted; different → written; other keys untouched."""
    overrides = read_overrides(home)
    for field, value in wanted.items():
        key = key_fmt % field
        if value == base[field]:
            overrides.pop(key, None)
        else:
            overrides[key] = value
    atomic_write_json(settings_overrides_path(home), overrides)


def update_daily_loop(home: Path, payload: dict) -> dict:
    """Validate a partial ``{field: value}`` and diff-write the flat override
    keys (equal to the config.yaml/default effective value → key deleted)."""
    wanted = _validated(payload, DAILY_LOOP_FIELDS, coerce_daily_loop,
                        "nothing to save: give at least one daily_loop field")
    base, _present = _config_daily_loop(home)
    _diff_write(home, wanted, base, DAILY_LOOP_KEY)
    return daily_loop_snapshot(home)


# --------------------------------------------------------------------------- #
# layered read: overrides → config.yaml → default
# --------------------------------------------------------------------------- #
def read_overrides(home: Path) -> dict:
    """The overrides document, {} when absent. An unparsable file (or a non-
    object) raises ConflictError — the pipeline ignores such a file, but
    overwriting it from here would destroy whatever the owner had in it."""
    p = settings_overrides_path(home)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConflictError("settings_overrides.json is unreadable",
                            {"path": str(p), "error": str(exc)})
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


def _config_models(home: Path) -> "tuple[dict, dict]":
    """(values, present): config.yaml ``models:`` block coerced per mode (bad
    shape → follow) + which modes the file actually spells (``source`` label).
    PyYAML absent / file absent / bad yaml → all follow, none present (the
    pipeline degrades the same way)."""
    values = {mode: MODEL_FOLLOW for mode in MODEL_MODES}
    present = {mode: False for mode in MODEL_MODES}
    if yaml is None:
        return values, present
    try:
        doc = yaml.safe_load(paths.config_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return values, present
    blk = doc.get("models") if isinstance(doc, dict) else None
    if not isinstance(blk, dict):
        return values, present
    for mode in MODEL_MODES:
        if mode in blk:
            present[mode] = True
            try:
                values[mode] = coerce_model(blk.get(mode))
            except ValueError:
                values[mode] = MODEL_FOLLOW
    return values, present


def models_snapshot(home: Path) -> dict:
    """Wire shape (web/src/types.ts ``ModelsSettings`` mirrors verbatim)::

        {"dispatch": "<id>|follow", "pipeline": "<id>|follow",
         "follow": "follow", "canonical": [...],
         "source": {"dispatch": "override|config|default", ...},
         "warnings": ["...plain sentence per non-canonical knob..."]}
    """
    overrides = read_overrides(home)
    base, present = _config_models(home)
    out: dict = {"follow": MODEL_FOLLOW, "canonical": list(CANONICAL_MODELS),
                 "source": {}, "warnings": []}
    for mode in MODEL_MODES:
        value, source = base[mode], ("config" if present[mode] else "default")
        raw = overrides.get(OVERRIDE_KEY % mode)
        if raw is not None:
            try:
                value, source = coerce_model(raw), "override"
            except ValueError:
                pass  # the pipeline skips the bad entry too
        out[mode] = value
        out["source"][mode] = source
        warning = noncanonical_warning(mode, value)
        if warning:
            out["warnings"].append(warning)
    return out


# --------------------------------------------------------------------------- #
# write: PUT /api/settings/models
# --------------------------------------------------------------------------- #
def atomic_write_json(p: Path, doc: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)


def update_models(home: Path, payload: dict) -> dict:
    """Validate ``{"dispatch"?: str, "pipeline"?: str}`` and diff-write the
    override keys. Unknown keys → 400 UNKNOWN_FIELD; a malformed id → 400
    INVALID_FIELD with the plain-language reason (the web toasts it). Returns
    the fresh :func:`models_snapshot`."""
    wanted = _validated(payload, MODEL_MODES, lambda _mode, v: coerce_model(v),
                        "nothing to save: give dispatch and/or pipeline")
    base, _present = _config_models(home)
    _diff_write(home, wanted, base, OVERRIDE_KEY)
    return models_snapshot(home)


# --------------------------------------------------------------------------- #
# Claude Code global default — ~/.claude/settings.json `model`
# --------------------------------------------------------------------------- #
def _load_claude_settings(p: Path):
    """(doc|None, text|None): None doc = missing; raises ConflictError when the
    file exists but is not a JSON object (never touched)."""
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        raise ConflictError("~/.claude/settings.json is unreadable",
                            {"path": str(p), "error": str(exc)})
    try:
        doc = json.loads(text)
    except ValueError:
        raise ConflictError(
            "~/.claude/settings.json is not valid JSON - refusing to touch it; "
            "fix it by hand (Claude Code reads it too)", {"path": str(p)})
    if not isinstance(doc, dict):
        raise ConflictError("~/.claude/settings.json must be a JSON object",
                            {"path": str(p)})
    return doc, text


def claude_code_default(path: Optional[Path] = None) -> dict:
    """Wire shape (``ClaudeCodeDefault`` in web/src/types.ts)::

        {"model": "<id>"|null, "path": "~/.claude/settings.json",
         "exists": bool, "parseable": bool, "canonical": bool}
    Never raises — an unparsable file reads as parseable:false, model:null."""
    p = path or claude_code_settings_path()
    out = {"model": None, "path": str(p), "exists": p.exists(),
           "parseable": False, "canonical": True}
    try:
        doc, _ = _load_claude_settings(p)
    except ConflictError:
        return out
    if doc is None:
        return out
    out["parseable"] = True
    model = doc.get("model")
    if isinstance(model, str) and model.strip():
        out["model"] = model.strip()
        out["canonical"] = model.strip() in CANONICAL_MODELS
    return out


def set_claude_code_default(model, path: Optional[Path] = None) -> dict:
    """Edit only ``model``; back the file up first. ``follow`` / blank is not a
    model here (400). Returns ``{"model", "previous", "backup", "path"}``."""
    try:
        value = coerce_model(model)
    except ValueError as exc:
        raise InvalidFieldError(str(exc), {"field": "model"})
    if value == MODEL_FOLLOW:
        raise InvalidFieldError("give a model id - the Claude Code default cannot follow itself",
                                {"field": "model"})
    p = path or claude_code_settings_path()
    doc, _text = _load_claude_settings(p)
    backup: Optional[str] = None
    previous = None
    if doc is None:
        doc = {}
        p.parent.mkdir(parents=True, exist_ok=True)
    else:
        prev = doc.get("model")
        previous = prev.strip() if isinstance(prev, str) and prev.strip() else None
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        bak = p.with_name(p.name + BACKUP_SUFFIX % stamp)
        n = 0
        while bak.exists():   # two clicks in one second: never clobber a backup
            n += 1
            bak = p.with_name(p.name + BACKUP_SUFFIX % ("%s-%d" % (stamp, n)))
        shutil.copy2(p, bak)
        backup = str(bak)
    doc["model"] = value
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    if backup is not None:
        try:
            shutil.copymode(p, tmp)   # keep the owner's file mode
        except OSError:
            pass
    os.replace(tmp, p)
    return {"model": value, "previous": previous, "backup": backup, "path": str(p)}
