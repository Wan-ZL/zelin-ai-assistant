"""server/terminal_launch.py — 「在终端接管会话」的 server 落点：``POST /api/terminal {card_id}``（§54.1 / §68.7）。

原生看板双击指令行 = TerminalLauncher（Apple Events 到 Ghostty / iTerm2 / Terminal）。
web 没有 Apple Events；这里走 ai_fix 同款的 ``.command`` 路径：server 从**投影行**
推导命令（``copy_cmd``，其次 ``claude --resume <session_id>``）、写一个可执行的
``.command`` 文件到 ``$TMPDIR``、``open`` 它——Terminal.app 直接执行 .command，
不需要任何自动化授权。**命令永远由 server 从卡片记录推导，绝不接受客户端文本**
（与 reveal / ai-fix 同一条纪律：客户端只给 SAFE_ID 白名单内的 card_id）。

- 非 darwin → 501（.command 只有 Terminal.app 会执行）；
- 卡不存在 / 投影行没有可接管的会话 → 404 / 400；
- ``opener`` 注入缝（测试绝不真 ``open``）。文件名带时间戳，内容含 cd 到 ``cwd``
  （投影行有则用，没有就在 home 下运行）。
"""
from __future__ import annotations

import datetime as _dt
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

from server.board_source import SAFE_ID_RE, locate_card
from server.errors import (ApiError, InvalidFieldError, NotFoundError,
                           NotImplementedError501, UnknownFieldError)

Opener = Callable[[Path], None]


def _default_opener(path: Path) -> None:
    subprocess.run(["/usr/bin/open", str(path)], check=False, timeout=20)


def _validate(payload: dict) -> str:
    unknown = set(payload) - {"card_id"}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    card_id = payload.get("card_id")
    if not (isinstance(card_id, str) and SAFE_ID_RE.match(card_id)):
        raise InvalidFieldError("card_id must be a card id", {"id": card_id})
    return card_id


def command_for(row: dict) -> Optional[str]:
    """投影行 → 接管命令：``copy_cmd`` 优先，其次 ``claude --resume <session_id>``。"""
    cmd = row.get("copy_cmd")
    if isinstance(cmd, str) and cmd.strip():
        return cmd.strip()
    sid = row.get("session_id")
    if isinstance(sid, str) and SAFE_ID_RE.match(sid):
        return "claude --resume %s" % sid
    return None


def script_for(card_id: str, cmd: str, cwd: Optional[str], home: Path) -> str:
    """.command 文件正文：cd 到工作目录 → exec 命令。cwd 用 shlex.quote，命令本身
    是投影里 actd 写好的一行 shell（原生 TerminalLauncher 也是逐字送进终端）。"""
    where = cwd if isinstance(cwd, str) and cwd.startswith("/") else str(home)
    return (
        "#!/bin/bash\n"
        "# Zelin's AI Assistant — take over the session of %s\n"
        "cd %s || { echo \"folder not found: %s\"; exit 1; }\n"
        "export AIASSISTANT_HOME=%s\n"
        "exec %s\n" % (card_id, shlex.quote(where), where, shlex.quote(str(home)), cmd))


def write_command_file(text: str, out_dir: Optional[Path] = None) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = (out_dir or Path(tempfile.gettempdir())) / ("zelin-ai-terminal-%s.command" % stamp)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _resolve(home: Path, card_id: str) -> "tuple[dict, str]":
    """投影行 + 接管命令；卡不存在 404、没有会话 400。"""
    _lane, row = locate_card(home, card_id)
    if row is None:
        raise NotFoundError("card not found", {"id": card_id})
    cmd = command_for(row)
    if cmd is None:
        raise InvalidFieldError("this card has no session to take over", {"id": card_id})
    return row, cmd


def _open(path: Path, opener: Optional[Opener]) -> None:
    try:
        (opener or _default_opener)(path)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ApiError("could not open Terminal: %s" % exc, {"command_file": str(path)})


def launch(home: Path, payload: dict, opener: Optional[Opener] = None,
           out_dir: Optional[Path] = None, platform: Optional[str] = None) -> dict:
    """校验 → 推导命令 → 写 .command → open → ``{"ok": true, "command": cmd, "command_file": path}``。"""
    card_id = _validate(payload)
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("opening a terminal session is macOS only")
    row, cmd = _resolve(home, card_id)
    cwd = row.get("cwd") if isinstance(row.get("cwd"), str) else None
    path = write_command_file(script_for(card_id, cmd, cwd, home), out_dir)
    _open(path, opener)
    return {"ok": True, "command": cmd, "command_file": str(path), "cwd": cwd or str(home)}
