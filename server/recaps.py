"""server/recaps.py — the web 会议纪要 page's server side (CONTRACT §63 / §63.9 / §63.10 / §63.14 / §63.16).

Three small things, all stdlib (config.yaml is read through
server.settings.config_yaml_doc, which degrades to {} without PyYAML):

1. **Recap settings** ``GET/PUT /api/settings/recap`` — the three knobs the
   pipeline reads (act/lib/config.py): ``enabled`` (default true),
   ``default_language`` (auto | zh | en), ``slack_draft_enabled`` (**default
   false**: a CLOSED recap is placed as a Slack *draft*, the send button stays
   the owner's). Effective value = settings_overrides.json flat key
   (``recap_enabled`` / ``recap_default_language`` / ``recap_slack_draft_enabled``)
   → config.yaml ``recap:`` block → default; PUT diff-writes the flat keys with
   the §15 semantics server/settings.py already implements for the model knobs.
   §63.10 adds a fourth, **read-only** value on the same GET: ``default_shape``
   (``lines`` | ``sections``, config.yaml only — the pipeline has no override
   flat key for it), which the 会议纪要 panel seeds its shape picker from.

2. **Local marks** ``POST /api/recaps/mark`` — 「复制」/「标记已发送」/「忽略」
   write ``state/recap/marks.json``
   ``{key: {copied_at, sent_at, dismissed_at, end_override}}`` (§63.16 adds
   ``POST /api/recaps/end`` for the hand-set end time, same file, add-only
   key; the recap file's captured ``end`` is never touched). This file is server-owned
   (act/recap.py never writes it; act/lib/recap_store.py only reads it).
   §63.5 追记（2026-09-15，issue #301）retires the old clause «**no control
   flow reads a mark** — it is a badge, not a state transition»: ``sent_at``
   (= 已归档, derived — un-marking restores) and ``dismissed_at`` now decide
   exactly two things, both in ``recap_store``, both read-only there — which
   projection budget a row spends (活跃 / 已归档 / 已忽略) and when a dismissed
   recap is pruned (`recap.dismissed_retention_days`). A mark still never
   enters the registry and never triggers a send / dispatch / card
   transition; this endpoint is still the only writer of the file.

3. **Stored versions** ``GET /api/recaps/history?key=…`` (§63.9, issue #300) —
   the earlier text, which the board projection deliberately does not carry
   (``recaps[]`` keeps only the §63.9 scalar handles ``history_versions``, so a
   10-second board poll never hauls 60 × 5 × 2 recap bodies). **Read-only**:
   the revert the panel offers goes inbox ``recap_revert`` → actd →
   ``python -m act.recap --revert``, because ``act/recap.py`` is the only
   writer of ``state/recap/recaps/`` (§63.6) and this process must never write
   a recap file.

server/ does not import act (§49): the key shape, the language vocabulary and
the override key names are mirrored from act/lib/recap_store.py /
act/lib/config.py and pinned by tests/test_server_paths_mirror.py.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Optional

from server import settings
from server.errors import InvalidFieldError, UnknownFieldError

# ---- mirrors (drift-pinned) ------------------------------------------------ #
# act/lib/recap_store.KEY_RE
KEY_RE = re.compile(r"^meeting:\d{4}-\d{2}-\d{2}T\d{4}-[a-z0-9-]{1,32}$")
# act/lib/config.RECAP_LANGUAGES
LANGUAGES: tuple = ("auto", "zh", "en")
# act/lib/recap_text.SHAPES（§63.10；未知值按第一个 = 五行形兜）
SHAPES: tuple = ("lines", "sections")
# wire key → settings_overrides.json flat key (config._OVERRIDE_FIELDS)
OVERRIDE_KEYS = {"enabled": "recap_enabled",
                 "default_language": "recap_default_language",
                 "slack_draft_enabled": "recap_slack_draft_enabled"}
# §63.10：`default_shape` 在 DEFAULTS 里但**不在** OVERRIDE_KEYS 里——它只住 config.yaml
# 的 recap 块（act/lib/recap_store.settings 读的就是那里，没有 overrides 扁平键），
# 所以它是**只读**的一格：GET 照层报给面板（面板拿它当形状选择器的初值），PUT 仍只认三把旋钮
DEFAULTS = {"enabled": True, "default_language": "auto", "slack_draft_enabled": False,
            "default_shape": SHAPES[0]}
# §63.5 追记（issue #301）：dismissed = 「忽略」（add-only 词表，永不改写已有值）
MARKS: tuple = ("copied", "sent", "dismissed")
# §63.9（issue #300）GET /api/recaps/history 的读门与上限
# mirrors act/recap.HISTORY_CAP —— 面板照它说「只留最近 5 版，更早的会老化掉」
HISTORY_CAP = 5
# 一份纪要文件（5 条 history × 两语言 × 5 行）是几 KB：超过这个就不是纪要了，不读、不解析
MAX_FILE_BYTES = 2 * 1024 * 1024
# 手改坏的文件可能有任意多条；投影不为它无界（防腐 #4 的精神：读面也有帽）
ENTRIES_CAP = 20

_BOOL_TRUE = ("true", "yes", "on", "1")
_BOOL_FALSE = ("false", "no", "off", "0")


def marks_path(home: Path) -> Path:
    # mirrors act/lib/recap_store.marks_path (STATE_DIR / recap / marks.json)
    return home / "state" / "recap" / "marks.json"


def glossary_path(home: Path) -> Path:
    # mirrors act/lib/recap_glossary.glossary_path (STATE_DIR / recap-glossary.md; §63.14).
    # **Read-only here**: the owner edits that file by hand, act/recap.py reads it at
    # generation time; this process only reports whether it exists.
    return home / "state" / "recap-glossary.md"


def recap_file_path(home: Path, key: str) -> Path:
    """mirrors act/lib/recap_store.recap_path (``recaps/<key with ':' → '_'>.json``).

    **Read-only here** (§63.6: ``act/recap.py`` is the only writer of that
    directory). The client never names a path: ``key`` must have passed
    :data:`KEY_RE` first, which leaves nothing but
    ``meeting_<date>T<hhmm>-<slug>`` — no separator, no dot segment, no room
    for a traversal."""
    if not (isinstance(key, str) and KEY_RE.match(key)):
        raise InvalidFieldError("key must be a recap key", {"field": "key"})
    return home / "state" / "recap" / "recaps" / (key.replace(":", "_") + ".json")


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


def coerce_shape(value) -> str:
    """§63.10 出稿形状：两个字面量之外一律 ValueError（调用方回落到默认形，
    与 act 侧 `recap_text.normalize_shape` 对一个手改坏的值的结论一致）。"""
    v = str(value or "").strip().lower()
    if v not in SHAPES:
        raise ValueError("default_shape must be one of %s" % ", ".join(SHAPES))
    return v


_COERCE = {"enabled": coerce_bool, "default_language": coerce_language,
           "slack_draft_enabled": coerce_bool, "default_shape": coerce_shape}


def _coerce_or(field: str, value, default):
    try:
        return _COERCE[field](value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# layered read: overrides → config.yaml → default
# --------------------------------------------------------------------------- #
def _config_block(home: Path) -> dict:
    """config.yaml ``recap:`` block as {wire key: raw value} for the keys it
    spells (slack_draft.enabled flattened); {} when absent / unreadable."""
    blk = settings.config_yaml_doc(home).get("recap")
    blk = blk if isinstance(blk, dict) else {}
    out = {k: blk[k] for k in ("enabled", "default_language", "default_shape") if k in blk}
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
         "slack_draft_enabled": bool, "default_shape": "lines|sections",
         "languages": [...],
         "source": {"enabled": "override|config|default", ...},
         "glossary": {"path": str, "present": bool, "config_terms": int}}

    §63.10：``default_shape`` 是**只读**的一格（config.yaml 层，无 overrides 扁平键）——
    面板拿它当形状选择器的初值，否则「重新生成」会替配置做主，把每一份老纪要
    永久盖成五行形。§63.14：``glossary`` 也是只读的一格（:func:`glossary_hint`），
    不进 ``source``、PUT 不收。"""
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
    # §63.14 add-only, read-only: where the glossary lives and whether anything is there yet —
    # the Settings section points the owner at the file; the parser lives in act (§49: no import)
    out["glossary"] = glossary_hint(home)
    return out


def _config_glossary_count(home: Path) -> int:
    """config.yaml ``recap.glossary`` 的字符串条数（只数形状，不解析——解析器住 act）。"""
    blk = settings.config_yaml_doc(home).get("recap")
    items = blk.get("glossary") if isinstance(blk, dict) else None
    return sum(1 for item in items if isinstance(item, str)) if isinstance(items, list) else 0


def glossary_hint(home: Path) -> dict:
    """``{"path": <abs>, "present": bool, "config_terms": int}``（§63.14）——面板据此说
    「术语表在哪、有没有」；条目怎么解析、换了几处，都是 act 侧的事（记录上的 ``glossary_hits``）。"""
    path = glossary_path(home)
    try:
        present = path.is_file() and path.stat().st_size > 0
    except OSError:
        present = False
    return {"path": str(path), "present": bool(present), "config_terms": _config_glossary_count(home)}


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
        raise InvalidFieldError("mark must be one of %s" % ", ".join(MARKS), {"field": "mark"})
    on = payload.get("on", True)
    if not isinstance(on, bool):
        raise InvalidFieldError("on must be a boolean", {"field": "on"})
    return which, on


# --------------------------------------------------------------------------- #
# history: GET /api/recaps/history?key=… (§63.9)
# --------------------------------------------------------------------------- #
def _list(value) -> list:
    return value if isinstance(value, list) else []


def _lines(value) -> list:
    """5 行纯文本 → 只留字符串项（手改坏的文件里什么都可能有；数字 title 真出现过）。"""
    return [line for line in _list(value) if isinstance(line, str)]


def _text_or_none(value):
    """粘出去的那份正文，只收非空字符串（数字 / dict 都是手改过的文件里见过的）。"""
    return value if isinstance(value, str) and value.strip() else None


def _version_shape(entry: dict) -> dict:
    """一版（当前记录或一条 history 条目）→ 固定形，键恒在。``version`` 解析不出 = 0
    （那一项前端不给回退按钮），``quality`` 只放字符串（history 条目在 §63.9 之前
    没有这个键 = null，面板照此说「这一版的校验结论没有存下来」）。

    §63.10 追记（issue #303）add-only：``shape`` 与两语言的 ``copy_*``（daemon 出稿时
    渲染好的那份正文）。可发送长版的 ``en`` / ``zh`` 是空的，两版对照因此读 ``copy_*``
    ——server 只搬运，**永不自己拼正文**（渲染单点在 act/lib/recap_text.py）。"""
    version = entry.get("version")
    return {"version": int(version) if isinstance(version, int) and not isinstance(version, bool) else 0,
            "generated_at": entry.get("generated_at") if isinstance(entry.get("generated_at"), str) else None,
            "partial": bool(entry.get("partial")),
            "quality": entry.get("quality") if isinstance(entry.get("quality"), str) else None,
            "en": _lines(entry.get("en")), "zh": _lines(entry.get("zh")),
            "shape": entry.get("shape") if entry.get("shape") in SHAPES else SHAPES[0],
            "copy_en": _text_or_none(entry.get("copy_en")),
            "copy_zh": _text_or_none(entry.get("copy_zh"))}


def _empty_history(key: str) -> dict:
    """层缺席（文件不在 / 坏文件 / 超读门）的空投影——页面对三者一条路：没有上一版可看。"""
    return {"key": key, "current": None, "entries": [], "history_cap": HISTORY_CAP,
            "truncated": False}


def _read_doc(path: Path) -> "tuple[Optional[dict], bool]":
    """``(顶层 dict, truncated)``：缺席 / 坏 JSON / 顶层不是 dict → ``(None, False)``；
    超过 :data:`MAX_FILE_BYTES` → ``(None, True)``（**不读、不解析**）。永不抛。"""
    try:
        size = path.stat().st_size
    except OSError:
        return None, False                  # 还没出过稿 / 已过保留期：空层，不是错误
    if size > MAX_FILE_BYTES:
        return None, True
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, False
    return (doc if isinstance(doc, dict) else None), False


def _shaped_entries(doc: dict) -> list:
    """``history[]`` → **newest first** 的完整形，条数按 :data:`ENTRIES_CAP` 截；
    只留有正文的条目（面板上每一项都必须真能回退，判据同
    ``act/lib/recap_store.has_text``：五行形看 ``en``，§63.10 可发送长版看
    渲染好的 ``copy_en``）。"""
    shaped = [_version_shape(entry) for entry in reversed(_list(doc.get("history")))
              if isinstance(entry, dict)][:ENTRIES_CAP]
    return [entry for entry in shaped if entry["en"] or entry["copy_en"]]


def history(home: Path, query: dict) -> dict:
    """``GET /api/recaps/history?key=…`` (§63.9, issue #300) — one recap's stored
    versions **with their text**, the one place the earlier text is readable.

    Wire shape (``web/src/types.ts`` ``RecapHistory`` mirrors verbatim)::

        {"key": "meeting:…",
         "current":  {version, generated_at, partial, quality, en[], zh[],
                      shape, copy_en, copy_zh} | null,
         "entries": [ …the same shape, newest first… ],
         "history_cap": 5, "truncated": false}

    Read-only and fail-open, mirroring ``server/search_index_source.py``: a
    missing / corrupt / non-object file is **200 with an empty projection**
    (宪法第 11 条 — a layer being absent is not an error, and a 404 would need
    the page to grow a second story for the same outcome); over
    :data:`MAX_FILE_BYTES` the file is not read at all →
    ``truncated: true``. Never 500. A bad / missing ``key`` is the one 400:
    that is the client naming something it is not allowed to name."""
    key = _require_key({"key": (query or {}).get("key")})
    doc, truncated = _read_doc(recap_file_path(home, key))
    out = _empty_history(key)
    out["truncated"] = truncated
    if doc is None:
        return out
    out["current"] = _version_shape(doc)
    out["entries"] = _shaped_entries(doc)
    return out


# §63.16 手改的结束时间：ISO-Z 秒级（与 recap 文件的 start / end 同一形）
END_OVERRIDE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _require_end_override(payload: dict):
    """``end_override`` = ISO-Z 字符串（改成这个时刻）或 null（回到录制到的结束时间）；其余 400。"""
    value = payload.get("end_override", "")
    if value is None:
        return None
    if not (isinstance(value, str) and END_OVERRIDE_RE.match(value)):
        raise InvalidFieldError("end_override must be an ISO-8601 UTC timestamp (…Z) or null",
                                {"field": "end_override"})
    try:
        _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise InvalidFieldError("end_override is not a real timestamp", {"field": "end_override"})
    return value


def end(home: Path, payload: dict) -> dict:
    """``POST /api/recaps/end`` ``{"key": "meeting:…", "end_override": "2026-09-21T19:30:00Z" | null}``
    → ``{"ok": true, "key", "end_override"}`` (§63.16, issue #440 / #299).

    The header shows the last **captured** segment (12:36 when the meeting really ended
    12:30); the owner may set the end time by hand. It is a display-layer fact like the
    other marks: it lives in marks.json (server-owned, add-only key ``end_override``),
    the recap file's ``end`` stays what the engine captured, generation never reads it.
    ``null`` clears it (back to the captured time)."""
    _reject_unknown(payload, ("key", "end_override"))
    key = _require_key(payload)
    value = _require_end_override(payload)
    marks = _read_marks(home)
    entry = marks.get(key) if isinstance(marks.get(key), dict) else {}
    entry["end_override"] = value
    marks[key] = entry
    settings.atomic_write_json(marks_path(home), marks)
    return {"ok": True, "key": key, "end_override": entry.get("end_override")}


def mark(home: Path, payload: dict) -> dict:
    """``{"key": "meeting:…", "mark": "copied"|"sent"|"dismissed", "on": bool?}``
    → ``{"ok": true, "key", "copied_at", "sent_at", "dismissed_at"}``. ``on``
    defaults to true; false clears the stamp (「标记已发送」/「忽略」are
    toggles — un-marking is the 恢复 out of 已归档 / 已忽略)."""
    _reject_unknown(payload, ("key", "mark", "on"))
    key = _require_key(payload)
    which, on = _require_mark(payload)
    marks = _read_marks(home)
    entry = marks.get(key) if isinstance(marks.get(key), dict) else {}
    entry["%s_at" % which] = _iso_now() if on else None
    marks[key] = entry
    settings.atomic_write_json(marks_path(home), marks)
    return {"ok": True, "key": key, "copied_at": entry.get("copied_at"),
            "sent_at": entry.get("sent_at"), "dismissed_at": entry.get("dismissed_at")}
