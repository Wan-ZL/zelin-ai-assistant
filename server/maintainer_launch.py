"""server/maintainer_launch.py — 设置「开发者 · 开发会话」→「在终端打开开发会话」（§54.4 / §68.1 / §68.7 追记）：
``POST /api/maintainer/terminal {}``。

原生 SettingsMaintainer.openSession：``cd <repo_path> && claude [--resume <session_id>]`` 交给
TerminalLauncher。web 版沿用 terminal_launch 的 ``.command`` + ``open`` 通道；两个参数都由 server
从 settings 目录的 effective 值读（``maintainer_repo_path`` 留空 = 本 checkout；``maintainer_session_id``
启动前再过目录的 ``session_id`` check——``settings_catalog.SESSION_ID_RE``：首字符字母 / 数字、其余
[A-Za-z0-9-]，首连字符 = CLI 选项的形状——原生 openSession 重跑 validateSessionID 同款，400 带目录的
双语句与 ``check`` / ``reason``）——**客户端零参数**，命令永远是 server 拼的（reveal / ai-fix 同一纪律）。
路径不存在 400（原生「路径不存在」）；非 darwin 501；回执 add-only ``terminal_app_name``（resolved 终端
的展示名，原生「已在 <终端> 打开」）；open 失败 500 的 details 带 ``command``（原生「或手动在终端运行：」）。
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Optional

from server import paths, settings_catalog
from server.errors import ApiError, InvalidFieldError, NotImplementedError501, UnknownFieldError
from server.terminal_launch import Opener, open_command_file, preferred_terminal_name, write_command_file

SESSION_ID_KEY = "maintainer_session_id"
REPO_PATH_KEY = "maintainer_repo_path"


def _field(key: str) -> dict:
    return settings_catalog.field_index(settings_catalog.lookup("maintainer"))[key]


def _effective(home: Path, key: str) -> str:
    value, _src = settings_catalog.effective(_field(key), settings_catalog.read_overrides(home),
                                            settings_catalog.load_config_doc(home))
    return value if isinstance(value, str) else ""


def resolve(home: Path) -> "tuple[Path, str]":
    """(repo 目录, session id)；目录不存在 400、session id 不合目录 check 400（同一句、同一 details）。"""
    raw = _effective(home, REPO_PATH_KEY).strip()
    # ~ 展开与目录灰字 / path_exists 同一把（~nosuchuser 不炸成 500，落到下面的「路径不存在」400）
    repo = settings_catalog.expand_user_path(raw) if raw else paths.repo_root()
    if not repo.is_dir():
        raise InvalidFieldError("repo path does not exist", {"path": str(repo)})
    sid = _effective(home, SESSION_ID_KEY).strip()
    # effective 的 id 可能来自 config.yaml（没经过 PUT 的闸），启动前重过同一道（原生 openSession）
    settings_catalog.run_check(_field(SESSION_ID_KEY), sid or None, SESSION_ID_KEY)
    return repo, sid


def command_for(repo: Path, sid: str) -> str:
    cmd = "cd %s && claude" % shlex.quote(str(repo))
    return cmd + (" --resume %s" % sid if sid else "")


def launch(home: Path, payload: dict, opener: Optional[Opener] = None, out_dir: Optional[Path] = None,
           platform: Optional[str] = None) -> dict:
    """``{}`` → 写 .command → open → ``{"ok": true, "command", "command_file", "cwd", "terminal_app_name"}``。"""
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("opening a terminal session is macOS only")
    repo, sid = resolve(home)
    cmd = command_for(repo, sid)
    text = ("#!/bin/bash\n# Zelin's AI Assistant — development session\n"
            "export AIASSISTANT_HOME=%s\nexec %s\n" % (shlex.quote(str(home)), cmd))
    path = write_command_file(text, out_dir)
    try:
        open_command_file(path, opener, home)
    except ApiError as exc:
        # 原生「打开终端失败——…或手动在终端运行：<cmd>」：details 里带上（add-only），页面原句照印
        exc.details = dict(exc.details, command=cmd)
        raise
    return {"ok": True, "command": cmd, "command_file": str(path), "cwd": str(repo),
            "terminal_app_name": preferred_terminal_name(home)}
