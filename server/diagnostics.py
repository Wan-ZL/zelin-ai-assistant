"""server/diagnostics.py — 「诊断」页的只读数据面（§23 / §25 / §47.4 / §56 / §68.4）。

原生主窗口的 依赖检查 + 录制与 ingest + 关于 三页里凡是「读文件、看状态」的部分
合成一页：``GET /api/diagnostics`` = doctor 报告（server/doctor_run，缓存）+
管线活性（server/health）+ ``deploy_state``（dashboard.json 顶层键，§56）+
``install_report``（§23 每步回执）+ ``radar_sources``（§48 投影）+ 可看的日志清单；
``GET /api/logs/{name}?lines=N`` 回一个日志的尾巴（只读、size-cap：最多读末尾
64 KiB、最多 1000 行）。

日志白名单 = 两个目录里**实际存在**的 ``*.log``：``~/Library/Logs/zelin-ai-assistant/``
（launchd 模板的 StandardOut/ErrorPath，§55）与 ``<home>/state/logs/``；``name``
只认 ``[A-Za-z0-9._-]+\\.log``（basename，无路径分隔符——防穿越），且必须出现在
清单里才服务。server 永不写、永不删日志（§55 审计 L3：用户日志不删）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from server import board_source, doctor_run, health, paths
from server.errors import InvalidFieldError, NotFoundError

LOG_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.log$")
LOG_LIST_CAP = 60
TAIL_BYTES_CAP = 64 * 1024
TAIL_LINES_DEFAULT = 200
TAIL_LINES_MAX = 1000


def _read_json(p: Path) -> Optional[dict]:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _log_dirs(home: Path) -> list:
    return [paths.user_log_dir(), home / "state" / "logs"]


def _log_files(d: Path) -> list:
    try:
        return [p for p in d.iterdir() if p.is_file() and LOG_NAME_RE.match(p.name)]
    except OSError:
        return []


def _log_entry(p: Path) -> Optional[dict]:
    try:
        st = p.stat()
    except OSError:
        return None
    return {"name": p.name, "path": str(p), "size": st.st_size, "mtime": int(st.st_mtime)}


def _log_entries(home: Path) -> list:
    out = []
    for d in _log_dirs(home):
        for p in _log_files(d):
            entry = _log_entry(p)
            if entry is not None:
                out.append(entry)
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out[:LOG_LIST_CAP]


def install_report(home: Path) -> Optional[dict]:
    """§23 回执的公开子集（steps 只留 name/status/detail）。"""
    doc = _read_json(paths.install_report_path(home))
    if doc is None:
        return None
    steps = [{"name": s.get("name"), "status": s.get("status"), "detail": s.get("detail")}
             for s in doc.get("steps", []) if isinstance(s, dict)]
    return {"version": doc.get("version"), "generated_at": doc.get("generated_at"),
            "ok": doc.get("ok"), "steps": steps}


def snapshot(home: Path, *, refresh: bool = False, runner=None) -> dict:
    """``GET /api/diagnostics``。"""
    board = _read_json(paths.dashboard_path(home)) or {}
    return {
        "doctor": doctor_run.report(home, fast=True, refresh=refresh, runner=runner),
        "health": health.snapshot(home),
        "deploy_state": board.get("deploy_state") if isinstance(board.get("deploy_state"), dict) else None,
        "radar_sources": board.get("radar_sources") if isinstance(board.get("radar_sources"), dict) else None,
        "install_report": install_report(home),
        "registry_backend": board_source.registry_backend(home),
        "logs": _log_entries(home),
    }


def _resolve_log(home: Path, name: str) -> Path:
    if not LOG_NAME_RE.match(name or ""):
        raise InvalidFieldError("bad log name", {"name": str(name)[:100]})
    for entry in _log_entries(home):
        if entry["name"] == name:
            return Path(entry["path"])
    raise NotFoundError("log not found", {"name": name})


def _parse_lines(raw: Optional[str]) -> int:
    if raw is None:
        return TAIL_LINES_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        raise InvalidFieldError("lines must be an integer", {"lines": raw})
    if n < 1:
        raise InvalidFieldError("lines must be >= 1", {"lines": raw})
    return min(n, TAIL_LINES_MAX)


def _tail_bytes(p: Path) -> "tuple[bytes, int, bool]":
    with p.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        start = max(0, size - TAIL_BYTES_CAP)
        fh.seek(start)
        return fh.read(), size, start > 0


def tail(home: Path, name: str, lines_raw: Optional[str] = None) -> dict:
    """``GET /api/logs/{name}``：``{"name", "path", "size", "lines": [...], "truncated": bool}``。"""
    p = _resolve_log(home, name)
    n = _parse_lines(lines_raw)
    try:
        data, size, cut = _tail_bytes(p)
    except OSError as exc:
        raise NotFoundError("log unreadable", {"name": name, "error": str(exc)})
    text = data.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    if cut and all_lines:
        all_lines = all_lines[1:]   # 首行可能被字节截半
    lines = all_lines[-n:]
    return {"name": name, "path": str(p), "size": size, "lines": lines,
            "truncated": cut or len(lines) < len(all_lines)}
