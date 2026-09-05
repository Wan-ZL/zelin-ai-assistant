"""server/terminal_launch.py — 「在终端接管会话」的 server 落点：``POST /api/terminal {card_id}``（§54.1 / §68.7）。

**2026-09-05（issue #216）起 server 不再写 ``.command``、不再 ``open``**——macOS 26 对每个时间戳
文件名的「脚本文档」都弹一次 "Allow Ghostty to execute …?"，结构上没有「记住我」可言，原
docstring 里「不需要任何自动化授权」的断言被现实推翻。现在 server 只把一条 launch 请求**入队**：
``state/terminal_queue/<id>.json``（§28 通知中继同款形制：原子 ``.json.tmp`` + rename、写侧清扫
过期条目），壳（``shell/Sources/TerminalRelay.swift``）按节拍消费队列、经 Apple Events
（``shell/Sources/TerminalLauncher.swift``，老版 mac/ 实战验证过的那份）在 Ghostty / iTerm2 /
Terminal 新开窗口跑命令。自动化授权按（壳, 终端）这一对记忆——首次弹一次，此后安静。

**命令永远由 server 从投影行推导，绝不接受客户端文本**（``copy_cmd``，其次
``claude --resume <session_id>``；与 reveal / ai-fix 同一条纪律：客户端只给 SAFE_ID 白名单内的
card_id）。队列条目里 ``shell_line`` 是壳逐字交给终端的一行（``cd <cwd|home>`` + ``export
AIASSISTANT_HOME`` + ``exec <cmd>``），``command`` 是给人看的原命令。

用哪个终端仍是设置「通用 · 终端应用」（``terminal_app``，§68.1 overrides）——偏好住 server 侧
不变，**执行者换成壳**：壳只读同一把旋钮（§61.3 SettingsIO 读侧），``auto`` = 装了 Ghostty 就
Ghostty，否则 Terminal；选了没装的回落同款。maintainer_launch / uninstall_launch 复用同一条
``enqueue(...)`` 通道。

- 非 darwin → 501（Apple Events 只有 macOS 有）；
- 壳没在跑（``state/shell.heartbeat`` 缺席或过期）→ 503 ``SHELL_UNAVAILABLE``（队列没有消费者；
  页面降级为复制指令 + 提示，与 501 同一条降级逻辑）；
- 卡不存在 / 投影行没有可接管的会话 → 404 / 400；
- ``now`` 注入缝（时钟）；测试用 tmp home，绝不 spawn 任何进程。

**tombstone（防腐 #6）**：``write_command_file`` / ``open_command_file`` / ``_default_opener`` /
``script_for`` 的 ``.command`` + ``open -a`` 通道 retired 2026-09-05（issue #216），并入本模块的
``enqueue``；``act/ai_fix.py`` 的 .command 用途另行裁定，不在此列。
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from server import paths
from server.board_source import SAFE_ID_RE, locate_card
from server.errors import (ApiError, InvalidFieldError, NotFoundError,
                           NotImplementedError501, ShellUnavailableError,
                           UnknownFieldError)

# 队列条目过期阈值（两侧同值：壳 TerminalRelay.staleAfter）——一分钟没被消费的接管请求
# 直接丢弃：用户早走开了，终端一分钟后才蹦出来只会吓人。
STALE_AFTER_S = 60.0
# 壳心跳新鲜阈值：壳每 5 s touch 一次（shell/Sources/main.swift 引擎 tick），15 s = 三拍容错。
HEARTBEAT_FRESH_S = 15.0
# 队列条目 kind 词表（add-only）：接管会话 / 开发会话（§68.1）/ 卸载（§68.6）
KINDS = ("takeover", "maintainer", "uninstall")


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


def shell_line_for(cmd: str, cwd: Optional[str], home: Optional[Path]) -> str:
    """壳逐字交给终端的一行：cd 到工作目录 → 导出 AIASSISTANT_HOME → exec 命令。cwd / home 用
    shlex.quote；命令本身是投影里 actd 写好的一行 shell（老版 TerminalLauncher 也是逐字送进终端）。
    ``home`` 为 None 时不导出（卸载脚本不需要）。"""
    parts = []
    if isinstance(cwd, str) and cwd.startswith("/"):
        parts.append("cd %s || { echo \"folder not found: %s\"; exit 1; }" % (shlex.quote(cwd), cwd))
    if home is not None:
        parts.append("export AIASSISTANT_HOME=%s" % shlex.quote(str(home)))
    parts.append("exec %s" % cmd)
    return "; ".join(parts)


def shell_alive(home: Path, now: Optional[float] = None) -> bool:
    """壳在跑 ⇔ ``state/shell.heartbeat`` 存在且 mtime 在 HEARTBEAT_FRESH_S 内。"""
    try:
        age = (now if now is not None else time.time()) - paths.shell_heartbeat_path(home).stat().st_mtime
    except OSError:
        return False
    return age <= HEARTBEAT_FRESH_S


def require_shell(home: Path, now: Optional[float] = None) -> None:
    """没有消费者就不入队——503，页面据此降级（复制指令 + 提示）。"""
    if not shell_alive(home, now):
        raise ShellUnavailableError("the app is not running, so no terminal can be opened",
                                    {"heartbeat": str(paths.shell_heartbeat_path(home))})


def _unlink_if_stale(f: Path, cutoff: float) -> int:
    """1 = 早于 cutoff 且已删；0 = 新鲜或 stat 失败（与壳的删除竞态无妨，missing_ok）。"""
    try:
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            return 1
    except OSError:
        pass
    return 0


def sweep_stale(qdir: Path, now: Optional[float] = None) -> int:
    """删掉 mtime 早于 STALE_AFTER_S 的条目（含 .tmp 尸体）；尽力而为、永不抛，返回删了几个。"""
    cutoff = (now if now is not None else time.time()) - STALE_AFTER_S
    try:
        return sum(_unlink_if_stale(f, cutoff) for f in qdir.iterdir())
    except OSError:
        return 0


def enqueue(home: Path, kind: str, command: str, shell_line: str, cwd: str,
            card_id: Optional[str] = None, now: Optional[float] = None) -> "tuple[dict, Path]":
    """写一条队列条目（原子 .json.tmp + rename），先清扫过期同伴；返回 (entry, path)。
    写不进去 → 500（磁盘 / 权限问题如实报，不吞）。"""
    if kind not in KINDS:
        raise ValueError("unknown terminal queue kind: %r" % (kind,))
    stamp = now if now is not None else time.time()
    qdir = paths.terminal_queue_dir(home)
    entry = {"id": uuid.uuid4().hex, "kind": kind, "command": command,
             "shell_line": shell_line, "cwd": cwd, "created_at": int(stamp)}
    if card_id is not None:
        entry["card_id"] = card_id
    target = qdir / (entry["id"] + ".json")
    tmp = qdir / (entry["id"] + ".json.tmp")   # 壳只认 *.json，半写的文件永不被读到
    try:
        qdir.mkdir(parents=True, exist_ok=True)
        sweep_stale(qdir, stamp)
        try:
            tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
    except OSError as exc:
        raise ApiError("could not queue the terminal request: %s" % exc, {"queue_dir": str(qdir)})
    return entry, target


def _resolve(home: Path, card_id: str) -> "tuple[dict, str]":
    """投影行 + 接管命令；卡不存在 404、没有会话 400。"""
    _lane, row = locate_card(home, card_id)
    if row is None:
        raise NotFoundError("card not found", {"id": card_id})
    cmd = command_for(row)
    if cmd is None:
        raise InvalidFieldError("this card has no session to take over", {"id": card_id})
    return row, cmd


def launch(home: Path, payload: dict, platform: Optional[str] = None,
           now: Optional[float] = None) -> dict:
    """校验 → 推导命令 → 壳在跑？→ 入队 → ``{"ok": true, "command", "cwd", "queue_id", "command_file"}``
    （``command_file`` 自 2026-09-05 起 = 队列条目路径，键名保留：跨组件字段只增不删）。"""
    card_id = _validate(payload)
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("opening a terminal session is macOS only")
    row, cmd = _resolve(home, card_id)
    raw_cwd = row.get("cwd")
    cwd = raw_cwd if isinstance(raw_cwd, str) and raw_cwd.startswith("/") else str(home)
    require_shell(home, now)
    entry, path = enqueue(home, "takeover", cmd, shell_line_for(cmd, cwd, home), cwd,
                          card_id=card_id, now=now)
    return {"ok": True, "command": cmd, "cwd": cwd, "queue_id": entry["id"], "command_file": str(path)}
