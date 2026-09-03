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

ingress 落款（via，add-only；vnext-amendments T-28）：本 server 写的每个
inbox 文件都带 ``via:"web"``；capture/comment 两动词接受可选 ``actor:"agent"``
（唯一合法值，boardctl 恒发）——present 时落款改为 ``via:"agent"``。``via``
本身不在任何入站 schema 里（client 直发 → 400 UNKNOWN_FIELD，落款不可
spoof——golden 字节对照剥 via 后仍逐字节等价）。诚实条款：落款是礼仪 + 取证，
不是密码学墙（同用户裸 HTTP 可不发 actor）；硬后盾在 actd 侧——出身从
sources 现算、天花板、W17 强制扩写、人工审批列（收紧路径见 T-29）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
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
    # answer_input：retired v0.48.8（#119）——未知动作按零容忍 400
    "capture": ({"text"}, {"mode", "images", "preset"}),
    "weekly_digest_now": (set(), set()),
    "import_claude_sessions": ({"session_ids"}, set()),
    # §63 会议 recap：无卡片级 id，meeting_key 是 recap 键；两者都不带 recipient
    # ——recap_slack_draft 的 channel_id 是 owner 自己草稿箱的会话，不是发送目标
    "recap_generate": ({"meeting_key"}, {"note", "partial"}),
    "recap_slack_draft": ({"meeting_key", "channel_id"}, set()),
}

ALLOWED_ACTIONS = CARD_VERBS | frozenset(_SPECIAL_FIELDS)

# ingress 落款（T-28）：本 server 落的文件恒带 via（Mac 文件无 via = owner-local）
_VIA_WEB = "web"
_VIA_AGENT = "agent"
# actor 字段仅 boardctl 的动词面（capture/comment）接受；唯一合法值 "agent"
_ACTOR_VERBS = frozenset({"capture", "comment"})

# §34bis 双端字面量常量（Swift ProposalsTriage.presetKey = actd 同名常量）
_CAPTURE_PRESET = "proposals_triage"
# §10bis capture images 上限（actd 边界校验同值；这里 fail-closed 提前 400）
_CAPTURE_IMAGES_MAX = 4
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


# 三个 JSON 字面量（`is` 判定：True/False 是 int 子类，不能走 isinstance）
_LITERALS = ((None, "null"), (True, "true"), (False, "false"))


def _dump_list(v: list, key_indent: int) -> str:
    pad = " " * key_indent
    if not v:
        # 空数组三行渲染：``[`` + 空行 + 缩进 ``]``（md §1 ⑦）
        return "[\n\n" + pad + "]"
    inner = " " * (key_indent + 2)
    items = ",\n".join(inner + _dump_value(x, key_indent + 2) for x in v)
    return "[\n" + items + "\n" + pad + "]"


def _dump_value(v, key_indent: int) -> str:
    for literal, text in _LITERALS:
        if v is literal:
            return text
    if isinstance(v, str):
        return _dump_str(v)
    if isinstance(v, list):
        return _dump_list(v, key_indent)
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


def _is_safe_id(x) -> bool:
    return isinstance(x, str) and bool(SAFE_ID_RE.match(x))


def _require_id_list(value, field: str, *, min_len: int = 0,
                     distinct: bool = True) -> list:
    if not isinstance(value, list) or not all(_is_safe_id(x) for x in value):
        raise InvalidFieldError(f"{field} must be a list of safe ids",
                                {"field": field})
    _require_list_shape(value, field, min_len, distinct)
    return list(value)


def _require_list_shape(value: list, field: str, min_len: int, distinct: bool) -> None:
    if distinct and len(set(value)) != len(value):
        raise InvalidFieldError(f"{field} must not contain duplicates",
                                {"field": field})
    if len(value) < min_len:
        raise InvalidFieldError(f"{field} needs at least {min_len} ids",
                                {"field": field})


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
def _optional_str(value, message: str):
    """None 放行（= 缺省），否则必须是 str。"""
    if value is not None and not isinstance(value, str):
        raise InvalidFieldError(message)
    return value


def _comment_field(action: str, payload: dict):
    """``comment`` 键：str 或 null；comment 动作本身必须带非空文本。"""
    comment = _optional_str(payload.get("comment"), "comment must be a string or null")
    if action == "comment" and not (comment or "").strip():
        # §2.3：comment 动作携带文本——空文本没有语义，fail closed。
        # rework 留空是合法 wire（空反馈替换文案是 web 客户端的活，md R9）。
        raise InvalidFieldError("comment action requires text")
    return comment


def _build_card(action: str, payload: dict) -> dict:
    # 统一四键形（md §2）：comment 键恒在，无文本 = JSON null（对齐 Swift
    # ``comment ?? NSNull()``；merge_* 在 Mac 端同走此路径，golden 带 null）
    return {"action": action,
            "id": _require_safe_id(payload.get("id"), "id"),
            "comment": _comment_field(action, payload)}


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


def _publish_flag(payload: dict) -> bool:
    # publish 恒在（缺省 false——opt-in 语义下缺省不公开最保守）
    publish = payload.get("publish", False)
    if not isinstance(publish, bool):
        raise InvalidFieldError("publish must be a boolean")
    return publish


def _feedback_body(payload: dict, rec: dict) -> None:
    """text（可空串）/ images 两个可选键落进 rec；双空 → 400（Mac 端根本不发）。"""
    text = payload.get("text")
    if text is not None:
        rec["text"] = _require_str(text, "text", allow_empty=True)
    if "images" in payload:
        rec["images"] = _require_image_list(payload["images"], "images")
    if not (rec.get("text") or "").strip() and not rec.get("images"):
        raise InvalidFieldError("feedback needs text or images")


def _build_feedback(payload: dict) -> dict:
    # §29：ids 升序 sorted（可空 = 对整体）
    ids = sorted(_require_id_list(payload.get("ids", []), "ids"))
    rec = {"action": "feedback", "ids": ids, "publish": _publish_flag(payload)}
    _feedback_body(payload, rec)
    return rec


def _capture_mode(payload: dict, rec: dict) -> None:
    mode = payload.get("mode")
    if mode is None:
        return
    # §34/§41：mode 仅恰为 "run" 时放行——未定义值 400，绝不骑进 inbox 文件
    if mode != "run":
        raise InvalidFieldError('mode is only capture mode:"run"')
    rec["mode"] = "run"


def _capture_images(payload: dict, rec: dict) -> None:
    if "images" not in payload:
        return
    images = _require_image_list(payload["images"], "images")
    if len(images) > _CAPTURE_IMAGES_MAX:
        raise InvalidFieldError(
            f"images allows at most {_CAPTURE_IMAGES_MAX} paths")
    rec["images"] = images


def _capture_preset(payload: dict, rec: dict) -> None:
    preset = payload.get("preset")
    if preset is None:
        return
    # §34bis：仅 "proposals_triage" 且必须同时 mode:"run"——actd 侧是
    # fail-safe 忽略，API 侧 fail-closed 400（两层纪律，md §1）
    if preset != _CAPTURE_PRESET or rec.get("mode") != "run":
        raise InvalidFieldError(
            'preset is only "proposals_triage" with mode:"run"')
    rec["preset"] = _CAPTURE_PRESET


def _build_capture(payload: dict) -> dict:
    rec = {"action": "capture",
           "text": _require_str(payload.get("text"), "text")}
    _capture_mode(payload, rec)
    _capture_images(payload, rec)
    _capture_preset(payload, rec)   # 依赖 rec["mode"] 已就位——顺序不可换
    return rec


def _build_import_sessions(payload: dict) -> dict:
    # §22：session UUID 也过 SAFE_ID_RE（hex+连字符全在 allow-list 内，防穿越）
    ids = _require_id_list(payload.get("session_ids"), "session_ids", min_len=1)
    return {"action": "import_claude_sessions", "session_ids": ids}


# §63 recap 键 / Slack 会话 id 的形状（镜像 act/lib/recap_store.KEY_RE /
# CHANNEL_ID_RE；tests/test_server_paths_mirror.py 钉漂移）
_RECAP_KEY_RE = re.compile(r"^meeting:\d{4}-\d{2}-\d{2}T\d{4}-[a-z0-9-]{1,32}$")
_SLACK_CHANNEL_RE = re.compile(r"^[CDG][A-Z0-9]{6,20}$")
_RECAP_NOTE_MAX = 500


def _require_recap_key(payload: dict) -> str:
    key = payload.get("meeting_key")
    if not (isinstance(key, str) and _RECAP_KEY_RE.match(key)):
        raise InvalidFieldError("meeting_key must be a recap key", {"field": "meeting_key"})
    return key


def _build_recap_generate(payload: dict) -> dict:
    # §63「重新生成」（note = ≤500 字纠正备注）/「现在生成」（partial:true，OPEN 行）
    rec = {"action": "recap_generate", "meeting_key": _require_recap_key(payload)}
    if "note" in payload:
        note = _require_str(payload.get("note"), "note")
        if len(note) > _RECAP_NOTE_MAX:
            raise InvalidFieldError(f"note allows at most {_RECAP_NOTE_MAX} chars")
        rec["note"] = note
    if "partial" in payload:
        if payload["partial"] is not True:
            raise InvalidFieldError("partial is only true", {"field": "partial"})
        rec["partial"] = True
    return rec


def _build_recap_slack_draft(payload: dict) -> dict:
    # §63.4「投到 Slack 草稿」：会话 id 形状闸（只投草稿，永不发送）
    channel = payload.get("channel_id")
    if not (isinstance(channel, str) and _SLACK_CHANNEL_RE.match(channel)):
        raise InvalidFieldError("channel_id must be a Slack conversation id",
                                {"field": "channel_id"})
    return {"action": "recap_slack_draft", "meeting_key": _require_recap_key(payload),
            "channel_id": channel}


_SPECIAL_BUILDERS = {
    "split_note": _build_split_note,
    "set_title": _build_set_title,
    "merge_review": _build_merge_review,
    "merge_force": _build_merge_force,
    "feedback": _build_feedback,
    "capture": _build_capture,
    "weekly_digest_now": lambda payload: {"action": "weekly_digest_now"},
    "import_claude_sessions": _build_import_sessions,
    "recap_generate": _build_recap_generate,
    "recap_slack_draft": _build_recap_slack_draft,
}


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def write_action(payload: dict, *, home: Optional[Path] = None) -> dict:
    """校验 ``payload`` 并原子写入 ``$home/state/inbox/``；返回
    ``{"ok": True, "file": "<写入的文件名>", "action": "<动词>",
    "via": "web"|"agent"}``。

    异常契约（app.py 依赖）：
    - UnknownFieldError —— payload 出现本动词 schema 外的键（零容忍）
    - InvalidFieldError —— 动词不在白名单 / 字段类型或取值非法
    - OSError —— inbox 目录写失败（app.py 兜成 INTERNAL_ERROR）
    """
    action = payload.get("action")
    if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
        raise InvalidFieldError("unknown action",
                                {"action": str(action)[:100]})
    _reject_unknown_fields(action, payload)
    actor = _actor_of(payload)
    rec = _build_record(action, payload, actor)
    rec["via"] = _VIA_AGENT if actor == _VIA_AGENT else _VIA_WEB
    rec["ts"] = _iso_now()
    stem = _write_inbox_file(rec, action, home)
    # via 回带（add-only 响应键）：app.py 的 steer 标注按实际 ingress 裁决
    return {"ok": True, "file": f"{stem}.json", "action": action,
            "via": rec["via"]}


def _allowed_fields(action: str) -> set:
    """本动词入站 schema 的键全集（含 action 本身；agent 动词多一个 actor）。"""
    if action in CARD_VERBS:
        allowed = {"action", "id", "comment"}
    else:
        required, optional = _SPECIAL_FIELDS[action]
        allowed = {"action"} | required | optional
    if action in _ACTOR_VERBS:
        allowed = allowed | {"actor"}
    return allowed


def _reject_unknown_fields(action: str, payload: dict) -> None:
    # 逐动词字段白名单：schema 外一律 400（含 ts/expected_status/board_seq——
    # ts 由 server 重打防 spoof，后两者不在 web 入站面上；``via`` 永远是
    # server 落款，任何动词直发都是 UNKNOWN_FIELD）
    unknown = set(payload) - _allowed_fields(action)
    if unknown:
        raise UnknownFieldError("unknown field",
                                {"fields": sorted(unknown)})


def _actor_of(payload: dict):
    # actor 是传输面字段（不落盘）：只认 "agent"，其余取值 fail-closed 400
    actor = payload.get("actor")
    if "actor" in payload and actor != _VIA_AGENT:
        raise InvalidFieldError('actor is only "agent"', {"field": "actor"})
    return actor


def _build_record(action: str, payload: dict, actor) -> dict:
    if action in CARD_VERBS:
        rec = _build_card(action, payload)
    else:
        rec = _SPECIAL_BUILDERS[action](payload)
    if actor == _VIA_AGENT and ("mode" in rec or "preset" in rec):
        # agent 通道无直跑面（boardctl 连 flag 都没有）——裸 HTTP 也 fail-closed
        raise InvalidFieldError("agent capture cannot request direct run")
    return rec


def _write_inbox_file(rec: dict, action: str, home: Optional[Path]) -> str:
    """原子落盘，返回 stem（文件名去 .json）。"""
    # 文件命名（md §1）：capture-<uuid>.json 是 Mac debug 习惯，照抄；
    # stem 全局唯一是硬要求（§34.1 幂等键 + §5.4 ack 键）——每次铸新 uuid4。
    stem = f"capture-{uuid.uuid4()}" if action == "capture" else str(uuid.uuid4())
    inbox = paths.inbox_dir(paths.home_dir(home))
    inbox.mkdir(parents=True, exist_ok=True)
    # 原子写：.json.tmp 不匹配 actd 的 *.json glob，半截文件永不被消费（md §1）
    tmp = inbox / f"{stem}.json.tmp"
    tmp.write_bytes(mac_json_bytes(rec))
    os.replace(tmp, inbox / f"{stem}.json")
    return stem
