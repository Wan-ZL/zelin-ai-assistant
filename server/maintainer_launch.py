"""server/maintainer_launch.py — 设置「开发者 · 开发会话」→「在终端打开开发会话」（§54.4 / §68.1）：
``POST /api/maintainer/terminal {}``。

原生 SettingsMaintainer.openSession：``cd <repo_path> && claude [--resume <session_id>]`` 交给
TerminalLauncher。web 版沿用 terminal_launch 的 ``.command`` + ``open`` 通道；两个参数都由 server
从 settings 目录的 effective 值读（``maintainer_repo_path`` 留空 = 本 checkout；``maintainer_session_id``
过 ``[A-Za-z0-9-]`` 白名单）——**客户端零参数**，命令永远是 server 拼的（reveal / ai-fix 同一纪律）。
路径不存在 400（原生「路径不存在」）；非 darwin 501。
"""
from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path
from typing import Optional

from server import paths, settings_catalog
from server.errors import InvalidFieldError, NotImplementedError501, UnknownFieldError
from server.terminal_launch import Opener, _open, write_command_file

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


def command_for(repo: Path, sid: str) -> str:
    cmd = "cd %s && claude" % shlex.quote(str(repo))
    return cmd + (" --resume %s" % sid if sid else "")


def launch(home: Path, payload: dict, opener: Optional[Opener] = None, out_dir: Optional[Path] = None,
           platform: Optional[str] = None) -> dict:
    """``{}`` → 写 .command → open → ``{"ok": true, "command", "command_file", "cwd"}``。"""
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("opening a terminal session is macOS only")
    repo, sid = resolve(home)
    cmd = command_for(repo, sid)
    text = ("#!/bin/bash\n# Zelin's AI Assistant — development session\n"
            "export AIASSISTANT_HOME=%s\nexec %s\n" % (shlex.quote(str(home)), cmd))
    path = write_command_file(text, out_dir)
    _open(path, opener)
    return {"ok": True, "command": cmd, "command_file": str(path), "cwd": str(repo)}
