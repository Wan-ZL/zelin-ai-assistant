"""素材库 HTTP 面（CONTRACT §62.4；路由登记在 §49 路由表）。

模块名 ``material_box``：存储层是 ``act/lib/materials.py``，同名会撞防腐 #9
「同一 basename 禁止出现在两个目录层级」——HTTP 面按 §62 的对象名「素材库
(material box)」命名，路由 ``/api/materials/*`` 不变。

薄层：字段白名单（UNKNOWN_FIELD 零容忍）+ 类型闸 + 错误映射；存储、归一与
状态机全在 ``act/lib/materials.py``（server 只准 import act.lib，§58.3 规则 3，
与 board_source 的 store2 只读面同款守卫 import）。三个端点：

- ``GET  /api/materials/list?status=open|all|<status>`` → ``{items, status, counts}``
- ``POST /api/materials/add {url?, note?}``            → 新记录
- ``POST /api/materials/dismiss {id}``                 → 更新后的记录

写端点走 §49 四闸（app.py ``_check_auth``）。错误映射：not_found → 404；
full / bad_transition → 409 ``CONFLICT``（台账状态不允许这次写）；其余 →
400 ``INVALID_FIELD``。act.lib 不可 import 时诚实 501，不装成功。
"""
from __future__ import annotations

from pathlib import Path

from server.errors import (ConflictError, InvalidFieldError, NotFoundError,
                           NotImplementedError501, UnknownFieldError)

try:
    from act.lib import materials as _materials
except Exception:  # pragma: no cover - 部分安装形态：act 不可 import
    _materials = None  # type: ignore[assignment]

_ERROR_MAP = {"not_found": NotFoundError, "full": ConflictError,
              "bad_transition": ConflictError}


def _lib():
    if _materials is None:
        raise NotImplementedError501("materials storage unavailable (act.lib not importable)")
    return _materials


def _map(exc) -> Exception:
    cls = _ERROR_MAP.get(exc.code, InvalidFieldError)
    return cls(str(exc), {"reason": exc.code})


def _whitelist(payload: dict, allowed: frozenset) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})


def _string(payload: dict, key: str) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise InvalidFieldError("%s must be a string" % key, {"field": key})
    return value


def list_items(home: Path, query: dict) -> dict:
    lib = _lib()
    path = lib.ledger_path(home)
    status = str(query.get("status", "open"))
    try:
        items = lib.list_items(path, status)
    except lib.MaterialsError as exc:
        raise _map(exc)
    everything = lib.list_items(path, "all")
    counts = {"open": sum(1 for r in everything if r["status"] in lib.OPEN_STATUSES),
              "total": len(everything)}
    return {"items": items, "status": status, "counts": counts}


def add(home: Path, payload: dict) -> dict:
    _whitelist(payload, frozenset({"url", "note"}))
    url = _string(payload, "url")
    note = _string(payload, "note")
    lib = _lib()
    try:
        return lib.add(lib.ledger_path(home), url=url, note=note)
    except lib.MaterialsError as exc:
        raise _map(exc)


def dismiss(home: Path, payload: dict) -> dict:
    _whitelist(payload, frozenset({"id"}))
    item_id = _string(payload, "id")
    lib = _lib()
    try:
        return lib.transition(lib.ledger_path(home), item_id, "dismissed")
    except lib.MaterialsError as exc:
        raise _map(exc)
