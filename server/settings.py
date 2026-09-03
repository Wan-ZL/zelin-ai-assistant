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


def config_yaml_doc(home: Path) -> dict:
    """config.yaml as a dict; {} when PyYAML / the file / the shape is absent
    (shared by the per-section settings modules: recaps, display)."""
    if yaml is None:
        return {}
    try:
        doc = yaml.safe_load(paths.config_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return doc if isinstance(doc, dict) else {}


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
    unknown = set(payload) - set(MODEL_MODES)
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    if not payload:
        raise InvalidFieldError("nothing to save: give dispatch and/or pipeline")
    wanted: dict = {}
    for mode in MODEL_MODES:
        if mode not in payload:
            continue
        try:
            wanted[mode] = coerce_model(payload[mode])
        except ValueError as exc:
            raise InvalidFieldError(str(exc), {"field": mode})
    overrides = read_overrides(home)
    base, _present = _config_models(home)
    for mode, value in wanted.items():
        key = OVERRIDE_KEY % mode
        if value == base[mode]:
            overrides.pop(key, None)      # diff-write: same as effective → no key
        else:
            overrides[key] = value
    atomic_write_json(settings_overrides_path(home), overrides)
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
