"""§68.7 「在终端接管会话」场景的共用件（B-14 / B-15）。

``POST /api/terminal`` 的落点是 ``server.terminal_launch.launch``（路由表见
server/app.py）。这里直接调它的公开面：payload 同 wire、``platform="darwin"``
注入（Apple Events 只有 macOS 有）、时钟注入；错误按 ``ApiError`` 自带的
status / code 判 HTTP 形。绝不起 server、绝不 spawn 任何进程。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from server import paths, terminal_launch  # noqa: E402
from server.errors import ApiError  # noqa: E402

CARD_ID = "R-001"
SESSION = "aa11bb22-cc33-dd44-ee55-ff6677889900"
NOW = 1_800_000_000.0


def seed_board(home: Path) -> dict:
    """一行「正在执行」的投影（``copy_cmd`` = 可接管的会话）。"""
    row = {"id": CARD_ID, "title": "正在跑的一张卡", "state": "running",
           "session_id": SESSION, "copy_cmd": f"claude --resume {SESSION}",
           "cwd": str(home)}
    board = {"generated_at": "2026-09-15T03:30:00Z", "running": [row],
             "proposals": [], "approved": [], "review": [], "completed": []}
    (home / "state").mkdir(parents=True, exist_ok=True)
    (home / "state" / "dashboard.json").write_text(
        json.dumps(board, ensure_ascii=False), encoding="utf-8")
    return row


def beat(home: Path) -> Path:
    """壳在跑 = ``state/shell.heartbeat`` 新鲜（壳每 5 s touch 一次）。"""
    p = paths.shell_heartbeat_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("pid=1\n", encoding="utf-8")
    os.utime(p, (NOW, NOW))          # 心跳「刚刚」= 注入时钟的那一刻（壳每 5 s touch）
    return p


def post(home: Path) -> "tuple[int, str, dict]":
    """(status, code, body) —— 成功 = (200, "", 回执)，失败 = envelope 的 status/code。"""
    try:
        return 200, "", terminal_launch.launch(home, {"card_id": CARD_ID},
                                               platform="darwin", now=NOW)
    except ApiError as exc:
        return exc.status, exc.code, exc.envelope()


def queue_entries(home: Path) -> list:
    qdir = paths.terminal_queue_dir(home)
    return sorted(p.name for p in qdir.glob("*.json")) if qdir.is_dir() else []
