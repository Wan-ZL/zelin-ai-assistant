"""server/claude_sessions.py — 「导入 Claude Code 工作」的扫描面：``GET /api/claude-sessions``（§22 / §68.10）。

原生 SettingsClaudeImport.swift = ``python -m act.radar_claude_sessions --scan --window N``
的预览表 + 勾选 + 一个 ``import_claude_sessions`` inbox 动作。扫描子进程经
server/subproc（注入缝；CLI 输出一行 JSON ``{"ok", "root", "candidates": [...]}``，
``~/.claude/projects`` 不在时 ``{"ok": false, "reason": "no_claude_dir"}``）；导入动作
本来就在 ``POST /api/actions``（inbox_writer ``import_claude_sessions``），这里不重造。
``?window=<1..90>`` 天，默认 7（CLI 默认同值）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from server import subproc
from server.errors import InvalidFieldError

_TIMEOUT_S = 60
WINDOW_DEFAULT = 7
WINDOW_MAX = 90


def _window(raw: Optional[str]) -> int:
    if raw is None:
        return WINDOW_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        raise InvalidFieldError("window must be an integer", {"window": raw})
    if not 1 <= n <= WINDOW_MAX:
        raise InvalidFieldError("window must be 1..%d days" % WINDOW_MAX, {"window": raw})
    return n


def scan(home: Path, window_raw: Optional[str] = None, runner=None) -> dict:
    """``GET /api/claude-sessions``：CLI 的 JSON 行 + ``window``；子进程失败 → ``ok:false``。"""
    window = _window(window_raw)
    rc, out, err = subproc.run_module(home, "act.radar_claude_sessions",
                                      ["--scan", "--window", str(window)],
                                      timeout_s=_TIMEOUT_S, runner=runner)
    doc = subproc.parse_json_output(out)
    if doc is None:
        return {"ok": False, "reason": "scan_failed", "window": window,
                "error": subproc.tail(err or out) or ("scan exited %d" % rc), "candidates": []}
    doc["window"] = window
    doc.setdefault("candidates", [])
    return doc
