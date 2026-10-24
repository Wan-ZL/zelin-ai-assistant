"""server/recaps.py — the web 会议纪要 page's server side (CONTRACT §63 / §63.8 / §63.9 / §63.10 / §63.11).

v2.0.0: the board server owns the whole page (actd and dashboard.json retired —
full text in tag ``full``). Five things:

1. **The listing** ``GET /api/recaps`` → ``{"recaps": [...], "recap_counts": {...}}``
   — ``act/lib/recap_store.snapshot`` over ``<home>/state`` (the §63.5 two-budget
   projection + true per-lane totals, the §63.8 ``generate_request`` receipt per
   row, the §63.11 ``questions``; history text stripped). Read-only.

2. **Generate / regenerate** ``POST /api/recaps/generate``
   ``{key, note?, partial?, shape?, answers?}`` (「重新生成」/「现在生成」/
   「重新生成成可发送长版」/「按我的答案再出一版」). Field rules are the v1
   inbox_writer gate verbatim (note non-blank and ≤ 500 chars, ``partial`` only ``true``,
   ``shape`` ∈ lines | sections, ``answers`` = 1..12 distinct ``id=value`` strings);
   ``recap_store.request_argv`` then applies the daemon-side vocabulary
   (``recap_intent.answers_ok``) — a malformed request is a 400 and spawns
   nothing. The server takes ``requested_at`` **before** spawning, launches
   ``python -m act.recap --generate …`` detached (``act/lib/detached.launch``, the
   child serializes on the recap flock), and records the attempt in the §63.8
   ledger ``state/recap_requests.json`` (``recap_requests.record``; this module
   is its single writer).

3. **Revert** ``POST /api/recaps/revert {key, version}`` (§63.9「回退到这一版」) →
   ``python -m act.recap --revert KEY --to-version N`` detached. Whether that
   version exists is the locked writer's call (``act/recap.revert``).

4. **Recap settings** ``GET/PUT /api/settings/recap`` — the two knobs the pipeline
   reads (act/lib/config.py): ``enabled`` (default true) and ``default_language``
   (auto | zh | en). Effective value = settings_overrides.json flat key
   (``recap_enabled`` / ``recap_default_language``) → config.yaml ``recap:`` block
   → default; PUT diff-writes the flat keys (§15). ``default_shape``
   (``lines`` | ``sections``, config.yaml only) rides read-only on the same GET.
   The §63.4 ``slack_draft_enabled`` knob retired (the isolated claude of §78 has
   no Slack MCP server to place a draft with).

5. **Local marks** ``POST /api/recaps/mark`` and **stored versions**
   ``GET /api/recaps/history?key=…`` — unchanged: marks.json is server-owned
   (act only reads it); history is read-only (``act/recap.py`` stays the only
   writer of ``state/recap/recaps/``, §63.6 — this process never writes a recap
   file, it only spawns the writer).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Optional

from act.lib import detached, recap_requests, recap_store   # server → act.lib（§58.3 规则 3）
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
                 "default_language": "recap_default_language"}
# §63.10：`default_shape` 在 DEFAULTS 里但**不在** OVERRIDE_KEYS 里——它只住 config.yaml
# 的 recap 块，所以它是**只读**的一格：GET 照层报给面板，PUT 仍只认两把旋钮
DEFAULTS = {"enabled": True, "default_language": "auto", "default_shape": SHAPES[0]}
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

# ---- request gates (the v1 server/inbox_writer rules, verbatim) ------------- #
NOTE_MAX = 500
# §63.11 意图问答的一条答案（镜像 act/lib/recap_intent.ANSWER_RE）——这里只查**形状**，
# id 与选项的闭表住 act 侧（``recap_intent.answers_ok``，经 ``recap_store.request_argv``）
ANSWER_RE = re.compile(r"^[a-z]{1,8}\d{0,2}=[a-z_]{1,12}$")
ANSWERS_MAX = 12          # 镜像 act/lib/recap_intent.MAX_ANSWERS
VERSION_MAX = 99_999      # §63.9 回退目标的版本号理智闸
GENERATE_FIELDS = ("key", "note", "partial", "shape", "answers")
REVERT_FIELDS = ("key", "version")
LOG_NAME = "recap.log"    # mirrors act/lib/recap_store.log_path (the detached runs append here)


def marks_path(home: Path) -> Path:
    # mirrors act/lib/recap_store.marks_path (STATE_DIR / recap / marks.json)
    return home / "state" / "recap" / "marks.json"


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
           "default_shape": coerce_shape}


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
    spells; {} when absent / unreadable."""
    blk = settings.config_yaml_doc(home).get("recap")
    blk = blk if isinstance(blk, dict) else {}
    return {k: blk[k] for k in ("enabled", "default_language", "default_shape") if k in blk}


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
         "default_shape": "lines|sections",
         "languages": [...],
         "source": {"enabled": "override|config|default", ...}}

    §63.10：``default_shape`` 是**只读**的一格（config.yaml 层，无 overrides 扁平键）——
    面板拿它当形状选择器的初值，否则「重新生成」会替配置做主，把每一份老纪要
    永久盖成五行形。"""
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
    """The validated subset of the two knobs the PUT carries."""
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
    """Validate ``{enabled?, default_language?}`` and
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


# --------------------------------------------------------------------------- #
# listing: GET /api/recaps
# --------------------------------------------------------------------------- #
def list_recaps(home: Path, query: Optional[dict] = None) -> dict:
    """``GET /api/recaps`` → ``{"recaps": [...], "recap_counts": {active, archived, dismissed}}``.
    The query is ignored (the projection has its own budgets)."""
    del query
    return recap_store.snapshot(home / "state")


# --------------------------------------------------------------------------- #
# generate / revert: POST /api/recaps/generate | revert
# --------------------------------------------------------------------------- #
def _bad_answers(answers) -> bool:
    """形状闸：1..ANSWERS_MAX 条 ``id=value`` 字符串、id 不重复。"""
    if not (isinstance(answers, list) and 1 <= len(answers) <= ANSWERS_MAX):
        return True
    if not all(isinstance(a, str) and ANSWER_RE.match(a) for a in answers):
        return True
    ids = [a.split("=", 1)[0] for a in answers]
    return len(set(ids)) != len(ids)


def _check_note(note) -> str:
    if not isinstance(note, str):
        raise InvalidFieldError("note must be a string", {"field": "note"})
    if not note.strip():
        raise InvalidFieldError("note must not be empty", {"field": "note"})
    if len(note) > NOTE_MAX:
        raise InvalidFieldError("note allows at most %d chars" % NOTE_MAX, {"field": "note"})
    return note


def _check_partial(partial) -> bool:
    if partial is not True:
        raise InvalidFieldError("partial is only true", {"field": "partial"})
    return True


def _check_shape(shape) -> str:
    if shape not in SHAPES:
        raise InvalidFieldError("shape must be %s" % " or ".join(SHAPES), {"field": "shape"})
    return shape


def _check_answers(answers) -> list:
    if _bad_answers(answers):
        raise InvalidFieldError("answers must be 1..%d distinct id=value strings" % ANSWERS_MAX,
                                {"field": "answers"})
    return list(answers)


# optional generate fields → their gate (order = the 400 a malformed body names first)
_GENERATE_CHECKS = (("note", _check_note), ("partial", _check_partial),
                    ("shape", _check_shape), ("answers", _check_answers))


def _generate_decision(payload: dict) -> dict:
    """The validated ``recap_generate`` request (400 on the first bad field)."""
    _reject_unknown(payload, GENERATE_FIELDS)
    decision = {"action": "recap_generate", "meeting_key": _require_key(payload)}
    for field, check in _GENERATE_CHECKS:
        if field in payload:
            decision[field] = check(payload[field])
    return decision


def _revert_decision(payload: dict) -> dict:
    _reject_unknown(payload, REVERT_FIELDS)
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) \
            or not (1 <= version <= VERSION_MAX):
        raise InvalidFieldError("version must be an integer 1..%d" % VERSION_MAX,
                                {"field": "version"})
    return {"action": "recap_revert", "meeting_key": _require_key(payload), "version": version}


def _argv(decision: dict) -> list:
    """``recap_store.request_argv`` (the act-side vocabulary); None = 400, nothing spawned."""
    tail = recap_store.request_argv(decision)
    if tail is None:
        raise InvalidFieldError("malformed recap request", {"field": "answers"})
    return ["act.recap"] + tail


def generate(home: Path, payload: dict, launcher=None) -> dict:
    """``POST /api/recaps/generate`` → ``{"ok", "key", "launch", "requested_at",
    "generate_request"}``. ``launch`` = ``running`` (spawned) | ``noop`` (the spawn
    failed — the receipt says so, the row shows ``launch_failed``). ``launcher``
    = ``detached.launch``-shaped seam (tests never spawn)."""
    decision = _generate_decision(payload)
    argv = _argv(decision)
    key = decision["meeting_key"]
    requested_at = recap_requests.iso_now()
    launch = (launcher or detached.launch)(argv, LOG_NAME, "recap generate %s" % key, home=home)
    ledger = home / "state" / "recap_requests.json"
    recap_requests.record(key, launch, requested_at, path=ledger)
    rec = _read_doc(recap_file_path(home, key))[0] or {}
    return {"ok": launch == recap_requests.RUNNING, "key": key, "launch": launch,
            "requested_at": requested_at,
            "generate_request": recap_requests.projection(key, rec.get("generated_at"),
                                                          recap_requests.load(ledger))}


def revert(home: Path, payload: dict, launcher=None) -> dict:
    """``POST /api/recaps/revert {key, version}`` → ``{"ok", "key", "version", "launch"}``."""
    decision = _revert_decision(payload)
    argv = _argv(decision)
    key = decision["meeting_key"]
    launch = (launcher or detached.launch)(argv, LOG_NAME, "recap revert %s" % key, home=home)
    return {"ok": launch == detached.RUNNING, "key": key, "version": decision["version"],
            "launch": launch}
