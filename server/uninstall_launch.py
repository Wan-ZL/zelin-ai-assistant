"""server/uninstall_launch.py — 关于页「卸载…」→「在 Terminal 中卸载…」的 server 落点（§54.4 / §68.6）：
``POST /api/uninstall/terminal {}``。

原生 Pages.swift AboutView.confirmUninstall：确认后在 Terminal.app 里跑 repo 的 ``uninstall.sh``
（交互式，脚本自己再问一次、任务历史与密钥默认保留）。web 版走 terminal_launch 的队列通道（§68.7，
2026-09-05 起：server 入队 ``cd <repo>; exec bash uninstall.sh``、壳经 Apple Events 开终端；
``.command`` + ``open`` 已 retired）——**server 自己不删任何东西**，删的是用户在终端里亲手确认的脚本。
脚本缺席 404（原生「找不到卸载脚本」）；非 darwin 501；壳没在跑 503 / 入队失败 500 都带手动命令
（原生「请手动在 Terminal 里运行：…」）。
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Optional

from server import paths
from server.errors import ApiError, NotFoundError, NotImplementedError501, UnknownFieldError
from server.terminal_launch import enqueue, require_shell, shell_line_for

SCRIPT_NAME = "uninstall.sh"


def script_path() -> Path:
    return paths.repo_root() / SCRIPT_NAME


def shell_command() -> str:
    """用户手动可跑的一行（开不了终端时原样给页面显示）。"""
    return "cd %s && bash %s" % (shlex.quote(str(paths.repo_root())), SCRIPT_NAME)


def _gates(payload: dict, platform: Optional[str]) -> None:
    """多余字段 400 → 非 darwin 501 → 脚本缺席 404（带手动命令）。"""
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("uninstalling from a terminal window is macOS only")
    if not script_path().is_file():
        raise NotFoundError("uninstall script not found",
                            {"path": str(script_path()), "command": shell_command()})


def _enqueue(home: Path, now: Optional[float]) -> "tuple[dict, Path]":
    """壳在跑？→ 入队；503 / 500 的 details 都补上手动命令（原生「无法打开 Terminal」弹窗附带的那句，
    add-only），页面原句照印。"""
    repo = str(paths.repo_root())
    try:
        require_shell(home, now)
        return enqueue(home, "uninstall", shell_command(),
                       shell_line_for("bash %s" % SCRIPT_NAME, repo, None), repo, now=now)
    except ApiError as exc:
        exc.details = dict(exc.details, command=shell_command())
        raise


def launch(payload: dict, platform: Optional[str] = None, home: Optional[Path] = None,
           now: Optional[float] = None) -> dict:
    """``{}`` → 壳在跑？→ 入队 → ``{"ok": true, "command": <手动命令>, "queue_id", "command_file"}``
    （``command_file`` = 队列条目路径，键名保留）。"""
    _gates(payload, platform)
    entry, path = _enqueue(home if home is not None else paths.home_dir(), now)
    return {"ok": True, "command": shell_command(), "queue_id": entry["id"], "command_file": str(path)}
