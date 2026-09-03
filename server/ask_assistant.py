"""server/ask_assistant.py — 问问助手的 server 落点：``GET /api/ask/history`` + ``POST /api/ask``（§27 / §54.4）。

模块名按对象「问问助手」取 ``ask_assistant``——``ask.py`` 与入口 ``act/ask.py`` 同名会撞
防腐 #9「同一 basename 禁止出现在两个目录层级」；路由 ``/api/ask*`` 不变。

原生 Ask.swift = 输入框 → ``python3 -m act.ask "<question>"``（一次 tool-less ``claude -p``，
≤60 s）→ stdout 一行 JSON → 追加 ``state/ask_history.json``（最新在前、cap 20）。web 版
把同一条子进程搬到 server（server/subproc 注入缝；server 不 import act 的 entrypoint 层，
§49）：问题只校验长度 / 单行后进 argv（不经 shell），答案与失败分类原样透传给页面；
历史只读——写者仍是 ``act.ask`` 本人（单写者）。
"""
from __future__ import annotations

import json
from pathlib import Path

from server import subproc
from server.errors import InvalidFieldError, UnknownFieldError

QUESTION_MAX = 500
HISTORY_CAP = 20            # 与 act/ask.py HISTORY_CAP 同值（读侧兜底，不放大写侧的帽）
TIMEOUT_S = 75              # act.ask 自己 60 s 超时 + 解释器启动余量


def history_path(home: Path) -> Path:
    return home / "state" / "ask_history.json"


def history(home: Path) -> dict:
    """``GET /api/ask/history``：``{"items": [...]}``（文件缺席 / 坏 JSON → 空表，不 500）。"""
    try:
        doc = json.loads(history_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"items": []}
    items = [row for row in doc if isinstance(row, dict)] if isinstance(doc, list) else []
    return {"items": items[:HISTORY_CAP]}


def _question(payload: dict) -> str:
    """字段白名单（UNKNOWN_FIELD 零容忍，§49）+ 非空单行 + 长度帽。"""
    unknown = set(payload) - {"question"}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    raw = payload.get("question")
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidFieldError("question must be a non-empty string", {"field": "question"})
    text = " ".join(raw.split())
    if len(text) > QUESTION_MAX:
        raise InvalidFieldError("question is too long (max %d chars)" % QUESTION_MAX, {"field": "question"})
    return text


def ask(home: Path, payload: dict, runner=None) -> dict:
    """``POST /api/ask {question}``：跑 ``act.ask``，透传它的一行 JSON；子进程没给 JSON → ``ok:false``。"""
    question = _question(payload)
    rc, out, err = subproc.run_module(home, "act.ask", [question], timeout_s=TIMEOUT_S, runner=runner)
    doc = subproc.parse_json_output(out)
    if doc is None:
        return {"ok": False, "error": subproc.tail(err or out) or ("ask exited %d" % rc),
                "failure_id": None, "timeout": rc == 124, "elapsed_s": 0.0}
    doc.setdefault("ok", False)
    return doc
