"""server/analytics_ingest.py — web 看板的极简 analytics 事件入口：``POST /api/analytics {event[, fields]}``
（CONTRACT §16 features.analytics gate / §15 telemetry 上传门 / §49 路由；owner 决策 D48）。

原生 app 从向导 / 权限 / 诊断漏斗发 150+ 个 UI 事件（SetupWizard.swift / Permissions.swift / Doctor.swift…），web 移植
后一个都没有（审计 diagnostics-setup-ui-analytics-events）。D48 选项 b：**只恢复元数据级的最小子集**，事件名与字段
由 **server-owned 白名单** `EVENTS` 裁——客户端传不进自由文本，白名单外一律 400：

- ``wizard_complete``（无字段）——原生 SetupWizard.swift:615，向导「完成」；
- ``pipeline_repair_result{ok: bool}``——原生 Doctor.swift:379，一键修复的 15 s 恢复轮询下场。

落盘**不另起管线**：经 `act.lib.analytics.log_event` 追加进同一份 ``state/analytics/events.jsonl``——§16 的
`features.analytics` gate（隐私 fail-closed）、写者级版本戳 ``v``、上传端 `act.lib.telemetry_upload` 的 §15 consent 门 /
`telemetry.enabled` 都原样适用，与 Python / Swift 写者同一条路。每条记录另带 ``via:"web"``（常量，区分同名的原生
历史事件）。回执 ``{ok, event, logged}``：``logged=false`` = gate 关着（或写失败），诚实报 no-op、HTTP 仍 200——
analytics 永不弄坏 UI（宪法第 11 条）。

写者的路径是 `act/lib/analytics.py` 的模块常量（随 env ``AIASSISTANT_HOME``，§55 模板给 server 设的同一个值）——
server 不把自己的 home 传给它；`make_server(home=…)` 与 env 不同只是测试缝，判例用 ``log`` 注入缝。
"""
from __future__ import annotations

from typing import Callable, Optional

from act.lib import analytics
from server.errors import InvalidFieldError, UnknownFieldError

# server-owned 白名单：事件名 → {字段名: 期望类型}。add-only；加事件 = 加一行 + docs/TELEMETRY.md 表加一行
# + web/src/types.ts 的 WebAnalyticsEvent 加一项（判例 tests/test_web_analytics_event_vocabulary_mirror.py 钉两边同词）。
EVENTS: dict = {
    "wizard_complete": {},
    "pipeline_repair_result": {"ok": bool},
}

# 白名单里准用的字段类型 → (人话名, 严格判定)。**只收元数据级标量**：bool 只认 JSON 布尔（1 / "true" 不算）；
# int 排除 bool（Python 里 True 也是 int）。str / list / dict 永不进表——自由文本从这条路根本进不来（§16 追记）。
# 加类型 = 加一行 + 判例；EVENTS 里出现表外类型在 import 期就炸（_check_spec），绝不静默放行。
_FIELD_TYPES: dict = {
    bool: ("boolean", lambda v: isinstance(v, bool)),
    int: ("integer", lambda v: isinstance(v, int) and not isinstance(v, bool)),
}


def _check_spec(events: dict) -> None:
    """白名单自检：每个字段的期望类型都得在 _FIELD_TYPES 里，否则 TypeError（server 自己的错，fail-loud）。"""
    for event, spec in events.items():
        for key, expected in spec.items():
            if expected not in _FIELD_TYPES:
                raise TypeError(f"EVENTS[{event!r}][{key!r}]: unsupported field type {expected!r}; "
                                f"allowed: {[t.__name__ for t in _FIELD_TYPES]}")


_check_spec(EVENTS)

# log_event 同形：(event, **fields) → 是否真的落盘
Logger = Callable[..., bool]


def _typed(key: str, value, expected):
    """一个字段的类型核对，按 _FIELD_TYPES 逐型严判；表外类型 = 白名单坏了，炸 500 而不是放行。"""
    try:
        name, check = _FIELD_TYPES[expected]
    except KeyError:
        raise TypeError(f"{key}: unsupported field type {expected!r} in whitelist") from None
    if not check(value):
        raise InvalidFieldError(f"{key} must be a {name}")
    return value


def _clean_fields(event: str, raw) -> dict:
    """白名单内的字段逐个核对：缺省 {}；非对象 400；白名单外字段 UNKNOWN_FIELD。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise InvalidFieldError("fields must be an object")
    spec = EVENTS[event]
    extra = set(raw) - set(spec)
    if extra:
        raise UnknownFieldError("unknown field", {"fields": sorted(extra)})
    return {key: _typed(key, raw[key], expected) for key, expected in spec.items() if key in raw}


def ingest(payload: dict, log: Optional[Logger] = None) -> dict:
    """``POST /api/analytics`` → ``{ok, event, logged}``；白名单外 / 形状不对 400。"""
    unknown = set(payload) - {"event", "fields"}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    event = payload.get("event")
    if not isinstance(event, str) or event not in EVENTS:
        raise InvalidFieldError("event is not in the server whitelist",
                                {"allowed": sorted(EVENTS)})
    fields = _clean_fields(event, payload.get("fields"))
    writer = log or analytics.log_event
    logged = bool(writer(event, via="web", **fields))
    return {"ok": True, "event": event, "logged": logged}
