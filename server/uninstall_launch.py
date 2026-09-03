"""server/uninstall_launch.py — 关于页「卸载…」→「在 Terminal 中卸载…」的 server 落点（§54.4 / §68.6）：
``POST /api/uninstall/terminal {}``。

原生 Pages.swift AboutView.confirmUninstall：确认后在 Terminal.app 里跑 repo 的 ``uninstall.sh``
（交互式，脚本自己再问一次、任务历史与密钥默认保留）。web 版沿用 terminal_launch 的 ``.command`` +
``open`` 通道：server 写一个 ``cd <repo> && exec bash uninstall.sh`` 的 .command 并打开——**server 自己
不删任何东西**，删的是用户在终端里亲手确认的脚本。脚本缺席 404（原生「找不到卸载脚本」）；非 darwin 501；
open 失败 500 带手动命令（原生「请手动在 Terminal 里运行：…」）。
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Optional

from server import paths
from server.errors import NotFoundError, NotImplementedError501, UnknownFieldError
from server.terminal_launch import Opener, _open, write_command_file

SCRIPT_NAME = "uninstall.sh"


def script_path() -> Path:
    return paths.repo_root() / SCRIPT_NAME


def shell_command() -> str:
    """用户手动可跑的一行（open 失败时原样给页面显示）。"""
    return "cd %s && bash %s" % (shlex.quote(str(paths.repo_root())), SCRIPT_NAME)


def _script_text() -> str:
    return (
        "#!/bin/bash\n"
        "# Zelin's AI Assistant — uninstall (interactive; the script asks before removing anything)\n"
        "cd %s || { echo \"repo not found: %s\"; exit 1; }\n"
        "exec bash %s\n" % (shlex.quote(str(paths.repo_root())), paths.repo_root(), SCRIPT_NAME))


def launch(payload: dict, opener: Optional[Opener] = None, out_dir: Optional[Path] = None,
           platform: Optional[str] = None) -> dict:
    """``{}`` → 写 .command → open → ``{"ok": true, "command": <手动命令>, "command_file": path}``。"""
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("uninstalling from a terminal window is macOS only")
    if not script_path().is_file():
        raise NotFoundError("uninstall script not found", {"path": str(script_path())})
    path = write_command_file(_script_text(), out_dir)
    _open(path, opener)
    return {"ok": True, "command": shell_command(), "command_file": str(path)}
