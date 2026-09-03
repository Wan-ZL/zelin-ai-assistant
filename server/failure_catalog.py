"""server/failure_catalog.py — §25 失败目录的 server-owned 投影：``GET /api/failures``（§68.4；防腐 #10）。

原生 Doctor.swift ``FailureCatalog.message(id)`` 是 ``act/lib/failures.FAILURES[id].plain_zh / plain_en``
的 Swift 镜像；web 不再抄第二份句子——录制页的引擎诊断行（壳只给 failure id）、依赖检查页的
引擎 / 屏幕录制权限行按 id 从这里取一句人话。纯目录、只读、不依赖 home；``action_id`` 一并投影
（web 的对症按钮标签仍由 failureAction.tsx 逐字镜像原生 actionLabel，§66.2 探针要求短标签在 web 源码里）。
"""
from __future__ import annotations

from act.lib import failures


def catalog() -> dict:
    """``GET /api/failures`` → ``{"failures": {id: {"zh", "en", "action_id"}}}``。"""
    return {"failures": {
        fid: {"zh": str(entry.get("plain_zh") or ""), "en": str(entry.get("plain_en") or ""),
              "action_id": entry.get("action_id")}
        for fid, entry in failures.FAILURES.items() if isinstance(entry, dict)}}
