"""server/diagnostics.py — 「诊断」页的只读数据面（§23 / §25 / §47.4 / §56 / §68.4）。

原生主窗口的 依赖检查 + 录制与 ingest + 关于 三页里凡是「读文件、看状态」的部分
合成一页：``GET /api/diagnostics`` = doctor 报告（server/doctor_run，缓存）+
管线活性（server/health）+ ``deploy_state``（dashboard.json 顶层键，§56）+
``install_report``（§23 每步回执）+ ``radar_sources``（§48 投影）+ 可看的日志清单；
``GET /api/logs/{name}?lines=N`` 回一个日志的尾巴（只读、size-cap：最多读末尾
64 KiB、最多 1000 行）。

日志白名单 = 三个目录里**实际存在**的 ``*.log``：``~/Library/Logs/zelin-ai-assistant/``
（launchd 模板的 StandardOut/ErrorPath，§55）、``<home>/state/logs/`` 与 ``~/.screenpipe/``
（录制引擎自己的 ``engine.log``——录制页「查看引擎日志」的落点，§15.2）；``name``
只认 ``[A-Za-z0-9._-]+\\.log``（basename，无路径分隔符——防穿越），且必须出现在
清单里才服务。server 永不写、永不删日志（§55 审计 L3：用户日志不删）。

2026-09-03 追记（add-only，§15.1 / §15.2 原生依赖检查 + 录制页的「读文件」部分）：
``cron_probe`` = ``state/cron_probe.json`` 的公开子集（ts / read_ok / protected_path；
原生 CronProbe.read，「定时任务磁盘权限」行的四态由页面按原生规则判）；``activity`` =
原生 IngestModel.refreshLabels 的三个时间戳（``screenpipe_db`` / ``actd_log`` / vault
``unprocessed`` 最新文件）。**server 永不读 ~/Documents**（§68.3）：unprocessed 目录在
mirror 模式看 ``state/vault-mirror``，直连模式只在它不住 TCC 保护位置时列目录，
否则如实 ``readable:false``。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from server import board_source, doctor_run, health, paths, permissions
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
    return [paths.user_log_dir(), home / "state" / "logs", paths.screenpipe_dir()]


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


def cron_probe(home: Path) -> Optional[dict]:
    """§25 cron FDA 探针的公开子集（原生 CronProbe.read）：文件缺席 / 坏 JSON → None。"""
    doc = _read_json(paths.cron_probe_path(home))
    if doc is None:
        return None
    return {"ts": doc.get("ts"), "read_ok": doc.get("read_ok"),
            "protected_path": doc.get("protected_path")}


def _mtime(p: Path) -> Optional[int]:
    try:
        return int(p.stat().st_mtime)
    except OSError:
        return None


def _newest_mtime(d: Path) -> Optional[int]:
    """目录里（不含点文件）最新的 mtime；列不出来 = None。"""
    try:
        stamps = [_mtime(p) for p in d.iterdir() if not p.name.startswith(".")]
    except OSError:
        return None
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def _vault_sync_mode(home: Path) -> str:
    try:
        return paths.vault_sync_mode_path(home).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def unprocessed_activity(home: Path) -> dict:
    """vault「1 - unprocessed」最新文件。mirror 模式读 state/vault-mirror（链就在那里干活）；
    直连模式只在目录不住 TCC 保护位置时列——server 永不读 ~/Documents（§68.3）。"""
    real = Path(permissions.vault_root(home)) / "1 - unprocessed"
    if _vault_sync_mode(home) == "mirror":
        target = paths.vault_mirror_dir(home) / "1 - unprocessed"
    elif permissions.protected_location(real):
        return {"path": str(real), "mtime": None, "readable": False}
    else:
        target = real
    return {"path": str(real), "mtime": _newest_mtime(target), "readable": True}


def activity(home: Path) -> dict:
    """原生 IngestModel.refreshLabels 的三个时间戳（epoch 秒；缺席 = null）。"""
    db = paths.screenpipe_dir() / "db.sqlite"
    log = paths.actd_log_path(home)
    return {
        "screenpipe_db": {"path": str(db), "mtime": _mtime(db)},
        "actd_log": {"path": str(log), "mtime": _mtime(log)},
        "unprocessed": unprocessed_activity(home),
    }


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
        "cron_probe": cron_probe(home),
        "activity": activity(home),
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
