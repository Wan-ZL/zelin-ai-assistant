"""POST /api/self-improve/resume — owner 清掉自动草稿 PR 通道的暂停（CONTRACT §64.4 / §49）。

通道被敏感路径护栏挂起后（actd 写 ``state/self_improve/lane.json`` 的
``paused:true``），owner 有三条出口：处理被标记的 PR（合并/关闭，actd 巡检
自动清）、终端 ``python3 -m act.lib.self_improve --resume``、或看板顶部横幅的
「恢复通道」——本模块就是最后那条的 server 落点。只翻 ``paused`` 一个键 +
``resumed_at/resumed_by``，其余键（owner_login / followups / last_tick_at）
原样保留；文件不存在 = 本来就没暂停，幂等 200。

server/ 不 import act（§49）：路径由 server/paths.py 镜像，形状与
act/lib/self_improve.clear_pause 逐字一致（tests/test_server_self_improve_resume.py 钉）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

from server import paths
from server.errors import UnknownFieldError

# 与 act/lib/self_improve.clear_pause 清掉的键逐字一致
_PAUSE_KEYS = ("paused_reason", "paused_pr", "paused_pr_url", "paused_paths",
               "paused_card")


def _read(p: Path) -> dict:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write(p: Path, doc: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, p)


def resume(home: Path, payload: dict) -> dict:
    """清暂停；回 ``{"ok": true, "paused": false, "was_paused": bool}``。"""
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    p = paths.self_improve_lane_path(home)
    st = _read(p)
    was_paused = bool(st.get("paused"))
    st.update({"paused": False, "resumed_by": "owner",
               "resumed_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    for key in _PAUSE_KEYS:
        st.pop(key, None)
    _write(p, st)
    return {"ok": True, "paused": False, "was_paused": was_paused}
