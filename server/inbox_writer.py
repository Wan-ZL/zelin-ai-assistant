"""POST /api/actions → ``state/inbox/<uuid>.json``（G1 实现）。

wire 真源 = ``docs/design/inbox-actions.md``（F3 提取稿）+ 33 个 golden
fixtures（``tests/fixtures/inbox/``）。字节形状逐字复刻 Mac
``JSONSerialization [.prettyPrinted, .sortedKeys]``（`\\/` 转义、空数组三行、
`" : "` 分隔、末尾无换行）——golden 逐字节等价，不止 JSON 语义等价。

闸门纪律（BUILD-CONTRACT §2.1 zero-tolerance，对比 act/webui.py 的静默丢弃）：
- 动词白名单外 → InvalidFieldError（app.py 渲染 400 INVALID_FIELD）。
- 本动词 schema 外的任何键 → UnknownFieldError（400 UNKNOWN_FIELD）；
  ``ts``/``expected_status``/``board_seq`` 也拒——ts 一律 server 重打，
  后两者只活在 syncd 落的文件里，不在 web 入站面（inbox-actions.md §1 末条）。
- ``id``/``primary``/``ids[*]``/``session_ids[*]`` 全过 SAFE_ID_RE
  （board_source 同款，防 merge job_path 穿越）。
- 落盘后 actd 侧永远 fail-safe——两层纪律不混淆（md §1）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from pathlib import Path
from typing import Optional

from server import paths
from server.board_source import SAFE_ID_RE
from server.errors import InvalidFieldError, UnknownFieldError

# --------------------------------------------------------------------------- #
# 动词全集（inbox-actions.md §2 + §3 目录 = 一个不多一个不少）
# --------------------------------------------------------------------------- #
# 卡片决策类：统一四键 {action, comment, id, ts}，comment 键恒在（缺省 null）。
# merge_apply/merge_dismiss 走同一 card 路径（id = MS-*，Mac 形带 comment:null）。
CARD_VERBS = frozenset({
    "approve", "reject", "comment", "defer", "raise", "trash", "restore",
    "pin", "accept", "rework", "done_external", "abort_execution",
    "stop_to_review", "revert_review", "archive", "unarchive",
    "merge_apply", "merge_dismiss",
})

# 特形动作 → 入站字段 schema（action 之外允许出现的键；ts 恒由 server 重打）。
# required ⊆ optional∪required 全集之外的键一律 UnknownFieldError。
_SPECIAL_FIELDS = {
    "split_note": ({"id", "note_ts"}, set()),
    "set_title": ({"id", "title"}, set()),
    "merge_review": ({"ids"}, set()),
    "merge_force": ({"ids", "primary"}, set()),
    # feedback：publish/ids 缺省补 Mac 恒在形（false / []）——见 _build_feedback
    "feedback": (set(), {"ids", "publish", "text", "images"}),
    "answer_input": ({"id", "text"}, set()),
    "capture": ({"text"}, {"mode", "images", "preset"}),
    "weekly_digest_now": (set(), set()),
    "import_claude_sessions": ({"session_ids"}, set()),
}

ALLOWED_ACTIONS = CARD_VERBS | frozenset(_SPECIAL_FIELDS)

# §34bis 双端字面量常量（Swift ProposalsTriage.presetKey = actd 同名常量）
_CAPTURE_PRESET = "proposals_triage"
# §10bis capture images 上限（actd 边界校验同值；这里 fail-closed 提前 400）
_CAPTURE_IMAGES_MAX = 4
# §39.2 answer_input text 上限（code points，Python len 即是）
_ANSWER_MAX = 4000
# §37 set_title 归一后上限（code points——比 Swift Character 计数更贴 actd 复验）
_TITLE_MAX = 64


def _iso_now() -> str:
    # ISO8601DateFormatter 同格式：UTC 秒级 YYYY-MM-DDTHH:MM:SSZ
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Mac 字节形序列化（JSONSerialization [.prettyPrinted, .sortedKeys] 复刻）
# --------------------------------------------------------------------------- #
# 结构化实现（非 md §4 的字符串 replace 配方）：斜杠转义只作用于字符串值的
# json.dumps 产物，空数组三行渲染在 list 分支结构化生成——用户文本含字面
# ``[]`` 或 ``\\/`` 也不会被误伤。
def _dump_str(s: str) -> str:
    # NSJSONSerialization 特性：正斜杠转义为 \/（md §1 ④，byte-parity 第一雷点）。
    # json.dumps 产物里裸 ``/`` 只可能来自内容本身（\\ 已转义），replace 安全；
    # 内容为 ``\\/`` 时 dumps 给 ``\\\\/`` → 替换得 ``\\\\\\/``，与 NSJSON 一致。
    return json.dumps(s, ensure_ascii=False).replace("/", "\\/")


def _dump_value(v, key_indent: int) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, str):
        return _dump_str(v)
    if isinstance(v, list):
        pad = " " * key_indent
        if not v:
            # 空数组三行渲染：``[`` + 空行 + 缩进 ``]``（md §1 ⑦）
            return "[\n\n" + pad + "]"
        inner = " " * (key_indent + 2)
        items = ",\n".join(inner + _dump_value(x, key_indent + 2) for x in v)
        return "[\n" + items + "\n" + pad + "]"
    raise TypeError(f"unsupported inbox value type: {type(v).__name__}")


def mac_json_bytes(rec: dict) -> bytes:
    """顶层 dict → Mac prettyPrinted+sortedKeys 字节（末尾无换行，md §1 ⑨）。"""
    lines = [f'  {_dump_str(k)} : {_dump_value(rec[k], 2)}' for k in sorted(rec)]
    return ("{\n" + ",\n".join(lines) + "\n}").encode("utf-8")


# --------------------------------------------------------------------------- #
# 字段校验小件（fail-closed；毒值绝不落盘）
# --------------------------------------------------------------------------- #
def _require_safe_id(value, field: str) -> str:
    if not (isinstance(value, str) and SAFE_ID_RE.match(value)):
        raise InvalidFieldError(f"{field} must be a safe id", {"field": field})
    return value


def _require_str(value, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise InvalidFieldError(f"{field} must be a string", {"field": field})
    if not allow_empty and not value.strip():
        raise InvalidFieldError(f"{field} must not be empty", {"field": field})
    return value


def _require_id_list(value, field: str, *, min_len: int = 0,
                     distinct: bool = True) -> list:
    if not (isinstance(value, list)
            and all(isinstance(x, str) and SAFE_ID_RE.match(x) for x in value)):
        raise InvalidFieldError(f"{field} must be a list of safe ids",
                                {"field": field})
    if distinct and len(set(value)) != len(value):
        raise InvalidFieldError(f"{field} must not contain duplicates",
                                {"field": field})
    if len(value) < min_len:
        raise InvalidFieldError(f"{field} needs at least {min_len} ids",
                                {"field": field})
    return list(value)


def _require_image_list(value, field: str) -> list:
    # 本机 PNG 绝对路径（md §3.5/§3.7）；值 opaque 但形状 fail-closed
    if not (isinstance(value, list)
            and all(isinstance(x, str) and x.startswith("/") for x in value)):
        raise InvalidFieldError(
            f"{field} must be a list of absolute paths", {"field": field})
    if len(set(value)) != len(value):
        raise InvalidFieldError(f"{field} must not contain duplicates",
                                {"field": field})
    return list(value)


# --------------------------------------------------------------------------- #
# 逐动词组装（返回待序列化 rec；ts 由调用方统一补）
# --------------------------------------------------------------------------- #
def _build_card(action: str, payload: dict) -> dict:
    # 统一四键形（md §2）：comment 键恒在，无文本 = JSON null（对齐 Swift
    # ``comment ?? NSNull()``；merge_* 在 Mac 端同走此路径，golden 带 null）
    comment = payload.get("comment")
    if comment is not None and not isinstance(comment, str):
        raise InvalidFieldError("comment must be a string or null")
    if action == "comment" and not (comment or "").strip():
        # §2.3：comment 动作携带文本——空文本没有语义，fail closed。
        # rework 留空是合法 wire（空反馈替换文案是 web 客户端的活，md R9）。
        raise InvalidFieldError("comment action requires text")
    return {"action": action,
            "id": _require_safe_id(payload.get("id"), "id"),
            "comment": comment}


def _build_split_note(payload: dict) -> dict:
    # §38.2：note_ts = 折叠备注行的 ts 标签，逐字回传（不 restamp）
    return {"action": "split_note",
            "id": _require_safe_id(payload.get("id"), "id"),
            "note_ts": _require_str(payload.get("note_ts"), "note_ts")}


def _build_set_title(payload: dict) -> dict:
    # §37：客户端归一在此复刻（本 server 就是 web 的客户端层）——所有空白 run
    # （含 U+3000，str.split 覆盖）折成单空格并 trim；1..64 code points 才发。
    raw = _require_str(payload.get("title"), "title")
    title = " ".join(raw.split())
    if not (1 <= len(title) <= _TITLE_MAX):
        raise InvalidFieldError(
            f"title must be 1..{_TITLE_MAX} chars after normalization")
    return {"action": "set_title",
            "id": _require_safe_id(payload.get("id"), "id"),
            "title": title}


def _build_merge_review(payload: dict) -> dict:
    # §21：保持用户选择顺序（不排序、不去重——重复选区本就非法，fail closed）
    ids = _require_id_list(payload.get("ids"), "ids", min_len=2)
    return {"action": "merge_review", "ids": ids}


def _build_merge_force(payload: dict) -> dict:
    # §21bis：去重保序 ≥2、primary ∈ ids（Mac 客户端守卫同款；actd 照旧复验）
    raw = _require_id_list(payload.get("ids"), "ids", distinct=False)
    ids = list(dict.fromkeys(raw))
    primary = _require_safe_id(payload.get("primary"), "primary")
    if len(ids) < 2 or primary not in ids:
        raise InvalidFieldError(
            "merge_force needs >=2 distinct ids and primary among them")
    return {"action": "merge_force", "ids": ids, "primary": primary}


def _build_feedback(payload: dict) -> dict:
    # §29：ids 升序 sorted（可空 = 对整体）；publish 恒在（缺省 false——opt-in
    # 语义下缺省不公开最保守）；text 与 images 双空 → 400（Mac 端根本不发）
    ids = sorted(_require_id_list(payload.get("ids", []), "ids"))
    publish = payload.get("publish", False)
    if not isinstance(publish, bool):
        raise InvalidFieldError("publish must be a boolean")
    rec = {"action": "feedback", "ids": ids, "publish": publish}
    text = payload.get("text")
    if text is not None:
        rec["text"] = _require_str(text, "text", allow_empty=True)
    if "images" in payload:
        rec["images"] = _require_image_list(payload["images"], "images")
    if not (rec.get("text") or "").strip() and not rec.get("images"):
        raise InvalidFieldError("feedback needs text or images")
    return rec


def _build_answer_input(payload: dict) -> dict:
    # §39.2：trimmed 1..4000 code points（Python len 即 code points）；
    # 附图尾行由客户端拼进 text，无新键——这里只按总长校验，原文照写。
    text = _require_str(payload.get("text"), "text", allow_empty=True)
    if not (1 <= len(text.strip()) <= _ANSWER_MAX):
        raise InvalidFieldError(f"text must be 1..{_ANSWER_MAX} chars")
    return {"action": "answer_input",
            "id": _require_safe_id(payload.get("id"), "id"),
            "text": text}


def _build_capture(payload: dict) -> dict:
    rec = {"action": "capture",
           "text": _require_str(payload.get("text"), "text")}
    mode = payload.get("mode")
    if mode is not None:
        # §34/§41：mode 仅恰为 "run" 时放行——未定义值 400，绝不骑进 inbox 文件
        if mode != "run":
            raise InvalidFieldError('mode is only capture mode:"run"')
        rec["mode"] = "run"
    if "images" in payload:
        images = _require_image_list(payload["images"], "images")
        if len(images) > _CAPTURE_IMAGES_MAX:
            raise InvalidFieldError(
                f"images allows at most {_CAPTURE_IMAGES_MAX} paths")
        rec["images"] = images
    preset = payload.get("preset")
    if preset is not None:
        # §34bis：仅 "proposals_triage" 且必须同时 mode:"run"——actd 侧是
        # fail-safe 忽略，API 侧 fail-closed 400（两层纪律，md §1）
        if preset != _CAPTURE_PRESET or rec.get("mode") != "run":
            raise InvalidFieldError(
                'preset is only "proposals_triage" with mode:"run"')
        rec["preset"] = _CAPTURE_PRESET
    return rec


def _build_import_sessions(payload: dict) -> dict:
    # §22：session UUID 也过 SAFE_ID_RE（hex+连字符全在 allow-list 内，防穿越）
    ids = _require_id_list(payload.get("session_ids"), "session_ids", min_len=1)
    return {"action": "import_claude_sessions", "session_ids": ids}


_SPECIAL_BUILDERS = {
    "split_note": _build_split_note,
    "set_title": _build_set_title,
    "merge_review": _build_merge_review,
    "merge_force": _build_merge_force,
    "feedback": _build_feedback,
    "answer_input": _build_answer_input,
    "capture": _build_capture,
    "weekly_digest_now": lambda payload: {"action": "weekly_digest_now"},
    "import_claude_sessions": _build_import_sessions,
}


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def write_action(payload: dict, *, home: Optional[Path] = None) -> dict:
    """校验 ``payload`` 并原子写入 ``$home/state/inbox/``；返回
    ``{"ok": True, "file": "<写入的文件名>", "action": "<动词>"}``。

    异常契约（app.py 依赖）：
    - UnknownFieldError —— payload 出现本动词 schema 外的键（零容忍）
    - InvalidFieldError —— 动词不在白名单 / 字段类型或取值非法
    - OSError —— inbox 目录写失败（app.py 兜成 INTERNAL_ERROR）
    """
    action = payload.get("action")
    if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
        raise InvalidFieldError("unknown action",
                                {"action": str(action)[:100]})

    # 逐动词字段白名单：schema 外一律 400（含 ts/expected_status/board_seq——
    # ts 由 server 重打防 spoof，后两者不在 web 入站面上）
    if action in CARD_VERBS:
        allowed = {"action", "id", "comment"}
    else:
        required, optional = _SPECIAL_FIELDS[action]
        allowed = {"action"} | required | optional
    unknown = set(payload) - allowed
    if unknown:
        raise UnknownFieldError("unknown field",
                                {"fields": sorted(unknown)})

    if action in CARD_VERBS:
        rec = _build_card(action, payload)
    else:
        rec = _SPECIAL_BUILDERS[action](payload)
    rec["ts"] = _iso_now()

    # 文件命名（md §1）：capture-<uuid>.json 是 Mac debug 习惯，照抄；
    # stem 全局唯一是硬要求（§34.1 幂等键 + §5.4 ack 键）——每次铸新 uuid4。
    stem = f"capture-{uuid.uuid4()}" if action == "capture" else str(uuid.uuid4())
    inbox = paths.inbox_dir(paths.home_dir(home))
    inbox.mkdir(parents=True, exist_ok=True)
    # 原子写：.json.tmp 不匹配 actd 的 *.json glob，半截文件永不被消费（md §1）
    tmp = inbox / f"{stem}.json.tmp"
    tmp.write_bytes(mac_json_bytes(rec))
    os.replace(tmp, inbox / f"{stem}.json")
    return {"ok": True, "file": f"{stem}.json", "action": action}
