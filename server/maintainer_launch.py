"""server/maintainer_launch.py — 设置「开发者 · 开发会话」→「在终端打开开发会话」（§54.4 / §68.1）：
``POST /api/maintainer/terminal {}``。

原生 SettingsMaintainer.openSession：``cd <repo_path> && claude [--resume <session_id>]`` 交给
TerminalLauncher。web 版走 terminal_launch 的队列通道（§68.7，2026-09-05 起：server 入队、壳经
Apple Events 开终端；``.command`` + ``open`` 已 retired）；两个参数都由 server 从 settings 目录的
effective 值读（``maintainer_repo_path`` 留空 = 本 checkout；``maintainer_session_id`` 过
``[A-Za-z0-9-]`` 白名单）——**客户端零参数**，命令永远是 server 拼的（reveal / ai-fix 同一纪律）。
路径不存在 400（原生「路径不存在」）；非 darwin 501；壳没在跑 503。
"""
from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path
from typing import Optional

from server import paths, settings_catalog
from server.errors import InvalidFieldError, NotImplementedError501, UnknownFieldError
from server.terminal_launch import enqueue, require_shell, shell_line_for

_SESSION_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _effective(home: Path, key: str) -> str:
    section = settings_catalog.lookup("maintainer")
    field = settings_catalog.field_index(section)[key]
    value, _src = settings_catalog.effective(field, settings_catalog.read_overrides(home),
                                            settings_catalog.load_config_doc(home))
    return value if isinstance(value, str) else ""


def resolve(home: Path) -> "tuple[Path, str]":
    """(repo 目录, session id)；目录不存在 400、session id 不合白名单 400。"""
    raw = _effective(home, "maintainer_repo_path").strip()
    repo = Path(raw).expanduser() if raw else paths.repo_root()
    if not repo.is_dir():
        raise InvalidFieldError("repo path does not exist", {"path": str(repo)})
    sid = _effective(home, "maintainer_session_id").strip()
    if sid and not _SESSION_RE.match(sid):
        raise InvalidFieldError("session id must be [A-Za-z0-9-]", {"session_id": sid})
    return repo, sid


def claude_command(sid: str) -> str:
    """终端里真正 exec 的那段（cd 由 shell_line_for 负责）。"""
    return "claude" + (" --resume %s" % sid if sid else "")


def command_for(repo: Path, sid: str) -> str:
    """给人看 / 复制的整行（原生 openSession 同款）：``cd <repo> && claude [--resume <id>]``。"""
    return "cd %s && %s" % (shlex.quote(str(repo)), claude_command(sid))


def launch(home: Path, payload: dict, platform: Optional[str] = None,
           now: Optional[float] = None) -> dict:
    """``{}`` → 壳在跑？→ 入队 → ``{"ok": true, "command", "cwd", "queue_id", "command_file"}``
    （``command_file`` = 队列条目路径，键名保留）。"""
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("opening a terminal session is macOS only")
    repo, sid = resolve(home)
    cmd = command_for(repo, sid)
    require_shell(home, now)
    entry, path = enqueue(home, "maintainer", cmd,
                          shell_line_for(claude_command(sid), str(repo), home), str(repo), now=now)
    return {"ok": True, "command": cmd, "cwd": str(repo), "queue_id": entry["id"], "command_file": str(path)}
