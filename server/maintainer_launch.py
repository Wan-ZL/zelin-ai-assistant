"""server/maintainer_launch.py — 设置「开发者 · 开发会话」→「在终端打开开发会话」（§54.4 / §68.1 / §68.7 追记）：
``POST /api/maintainer/terminal {}``。

原生 SettingsMaintainer.openSession：``cd <repo_path> && claude [--resume <session_id>]`` 交给
TerminalLauncher。web 版走 terminal_launch 的队列通道（§68.7，2026-09-05 起：server 入队、壳经
Apple Events 开终端；``.command`` + ``open`` 已 retired）；两个参数都由 server 从 settings 目录的
effective 值读（``maintainer_repo_path`` 留空 = 本 checkout；``maintainer_session_id`` 启动前再过目录的
``session_id`` check——``settings_catalog.SESSION_ID_RE``：首字符字母 / 数字、其余 [A-Za-z0-9-]，首连字符
= CLI 选项的形状——原生 openSession 重跑 validateSessionID 同款，400 带目录的双语句与 ``check`` /
``reason``）——**客户端零参数**，命令永远是 server 拼的（reveal / ai-fix 同一纪律）。
路径不存在 400（原生「路径不存在」）；非 darwin 501；壳没在跑 503；回执 add-only ``terminal_app_name``
（resolved 终端的展示名，原生「已在 <终端> 打开」）；503 / 入队失败 500 的 details 带 ``command``
（原生「或手动在终端运行：」）。
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Optional

from server import paths, settings_catalog
from server.errors import ApiError, InvalidFieldError, NotImplementedError501, UnknownFieldError
from server.terminal_launch import enqueue, preferred_terminal_name, require_shell, shell_line_for

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


def claude_command(sid: str) -> str:
    """终端里真正 exec 的那段（cd 由 shell_line_for 负责）。"""
    return "claude" + (" --resume %s" % sid if sid else "")


def command_for(repo: Path, sid: str) -> str:
    """给人看 / 复制的整行（原生 openSession 同款）：``cd <repo> && claude [--resume <id>]``。"""
    return "cd %s && %s" % (shlex.quote(str(repo)), claude_command(sid))


def launch(home: Path, payload: dict, platform: Optional[str] = None,
           now: Optional[float] = None) -> dict:
    """``{}`` → 壳在跑？→ 入队 → ``{"ok": true, "command", "cwd", "queue_id", "command_file", "terminal_app_name"}``
    （``command_file`` = 队列条目路径，键名保留）。"""
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("opening a terminal session is macOS only")
    repo, sid = resolve(home)
    cmd = command_for(repo, sid)
    try:
        require_shell(home, now)
        entry, path = enqueue(home, "maintainer", cmd,
                              shell_line_for(claude_command(sid), str(repo), home), str(repo), now=now)
    except ApiError as exc:
        # 原生「打开终端失败——…或手动在终端运行：<cmd>」：details 里带上（add-only），页面原句照印
        exc.details = dict(exc.details, command=cmd)
        raise
    return {"ok": True, "command": cmd, "cwd": str(repo), "queue_id": entry["id"], "command_file": str(path),
            "terminal_app_name": preferred_terminal_name(home)}
