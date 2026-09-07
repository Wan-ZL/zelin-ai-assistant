"""act/lib/screenpipe_retention.py — screenpipe DB 保留期清理（CONTRACT §71.2；§18 cron 链 cleanup 步）。

录制引擎把 OCR 文本 / 音频转写永久攒在 ``~/.screenpipe/db.sqlite``（owner 机器 9/7 实测 10 GB），
此前唯一的清理是 ``ingest/screenpipe-cleanup.sh`` 删一小时前的 jpg/mp4 与本机 launchd 的 a11y
日清——文本行没有任何保留期。本模块给它一把旋钮 ``recording.retention_days``
（设置页「录制数据与磁盘」区 ``screenpipe_retention_days``；config.Config.screenpipe_retention_days）：

- **0 = 永久保留（出厂默认，现状不变）**；N ≥ 1 = 删掉早于 N 天的 frames（连带 ocr_text /
  elements）与 audio_transcriptions 行。
- **只删已导出的行**：``id <= ~/.screenpipe/export_markers/last_frame_id``（音频同款
  ``last_audio_id``）——ingest 已把它们的文本写进 vault ``2 - raw``，那份笔记就是回程票
  （§0 第 2 条）；标记缺席 = 0 = 什么都不删。
- 分批短事务（``BATCH`` 条 / 笔、``busy_timeout``）——引擎同时在写；每轮有时间预算
  （``MAX_SECONDS``），没删完的下一轮 cron（30 分钟）接着删；不 VACUUM（要独占锁），释放的页
  由新数据复用，文件大小到达稳态而不是立刻缩小——设置页据 ``PRAGMA freelist_count`` 报「可复用」。
- 回执 ``state/screenpipe_retention.json``（覆盖写、原子 rename，§0 第 3 条诚实报告）：删了多少、
  截止点、预算是否用尽、错误；server ``GET /api/screenpipe/disk`` 原样投影为「上次清理」。
- 永不外溢（§0 第 11 条）：任何异常进回执、退出码 0；cleanup.sh 再 ``|| true`` 兜一层。

CLI：``python3 -m act.lib.screenpipe_retention [--dry-run] [--db PATH] [--days N]``，stdout 一行 JSON。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

from act.lib import config as _config

BATCH = 2000              # 每笔事务最多删多少条 frame / 转写行（锁窗要短——引擎在写）
BUSY_TIMEOUT_MS = 30000
MAX_SECONDS = 120.0       # 单轮时间预算；没删完的下一轮 cron 接着删
RECEIPT_NAME = "screenpipe_retention.json"   # server/screenpipe_disk.py 同名镜像（判例钉）
MARKER_FRAME = "last_frame_id"
MARKER_AUDIO = "last_audio_id"


def default_db_path() -> Path:
    return Path.home() / ".screenpipe" / "db.sqlite"


def receipt_path(state_dir: Optional[Path] = None) -> Path:
    return Path(state_dir or _config.STATE_DIR) / RECEIPT_NAME


def read_marker(marker_dir: Path, name: str) -> int:
    """export_markers/<name> 的整数；缺席 / 坏形 = 0（= 一行都不删）。"""
    try:
        return max(0, int((marker_dir / name).read_text(encoding="utf-8").strip() or 0))
    except (OSError, ValueError):
        return 0


def cutoff_iso(now: float, days: int) -> str:
    """frames.timestamp 的 ISO 形（``2026-04-16T11:54:01.723008+00:00``）按字面序比；截止点写成同前缀的
    ``YYYY-MM-DDTHH:MM:SS``（UTC）。"""
    stamp = _dt.datetime.fromtimestamp(now - days * 86400, _dt.timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S")


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def count_eligible(conn: sqlite3.Connection, table: str, cutoff: str, marker: int) -> int:
    row = conn.execute("SELECT COUNT(*) FROM %s WHERE timestamp < ? AND id <= ?" % table, (cutoff, marker)).fetchone()
    return int(row[0] or 0)


def _eligible_ids(conn: sqlite3.Connection, table: str, cutoff: str, marker: int, batch: int) -> list:
    rows = conn.execute("SELECT id FROM %s WHERE timestamp < ? AND id <= ? ORDER BY id LIMIT ?" % table,
                        (cutoff, marker, int(batch))).fetchall()
    return [int(r[0]) for r in rows]


def delete_frames_batch(conn: sqlite3.Connection, cutoff: str, marker: int, batch: int = BATCH) -> int:
    """一笔事务删一批 frames + 它们的 ocr_text / elements（有这张表才删）；返回删掉的 frame 数（0 = 没了）。"""
    ids = _eligible_ids(conn, "frames", cutoff, marker, batch)
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with conn:
        conn.execute("DELETE FROM ocr_text WHERE frame_id IN (%s)" % marks, ids)
        if table_exists(conn, "elements"):
            conn.execute("DELETE FROM elements WHERE frame_id IN (%s)" % marks, ids)
        conn.execute("DELETE FROM frames WHERE id IN (%s)" % marks, ids)
    return len(ids)


def delete_audio_batch(conn: sqlite3.Connection, cutoff: str, marker: int, batch: int = BATCH) -> int:
    ids = _eligible_ids(conn, "audio_transcriptions", cutoff, marker, batch)
    if not ids:
        return 0
    with conn:
        conn.execute("DELETE FROM audio_transcriptions WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
    return len(ids)


def _drain(step, budget_end: float, batch: int) -> "tuple[int, int, bool]":
    """反复调 ``step(batch)`` 直到它返回 0 或预算用尽；返回 (删了多少, 几笔, 预算是否用尽)。"""
    deleted, batches = 0, 0
    while True:
        if time.monotonic() >= budget_end:
            return deleted, batches, True
        n = step(batch)
        if n == 0:
            return deleted, batches, False
        deleted += n
        batches += 1


def plan(conn: sqlite3.Connection, cutoff: str, frame_marker: int, audio_marker: int) -> dict:
    """只数不删（``--dry-run`` / 回执的 eligible_* 字段）。"""
    return {"eligible_frames": count_eligible(conn, "frames", cutoff, frame_marker),
            "eligible_audio": count_eligible(conn, "audio_transcriptions", cutoff, audio_marker)}


def prune(conn: sqlite3.Connection, *, retention_days: int, now: float, frame_marker: int,
          audio_marker: int, dry_run: bool = False, batch: int = BATCH,
          max_seconds: float = MAX_SECONDS) -> dict:
    """一轮清理。``retention_days < 1`` = 关（skipped=retention_off，零写入）。"""
    receipt = {"retention_days": int(retention_days), "frame_marker": frame_marker, "audio_marker": audio_marker,
               "dry_run": bool(dry_run), "deleted_frames": 0, "deleted_audio": 0, "batches": 0,
               "budget_exhausted": False, "cutoff": None, "skipped": None}
    if retention_days < 1:
        receipt["skipped"] = "retention_off"
        return receipt
    cutoff = cutoff_iso(now, retention_days)
    receipt["cutoff"] = cutoff
    receipt.update(plan(conn, cutoff, frame_marker, audio_marker))
    if dry_run:
        return receipt
    conn.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
    budget_end = time.monotonic() + max_seconds
    frames, fb, out1 = _drain(lambda b: delete_frames_batch(conn, cutoff, frame_marker, b), budget_end, batch)
    audio, ab, out2 = _drain(lambda b: delete_audio_batch(conn, cutoff, audio_marker, b), budget_end, batch)
    receipt.update({"deleted_frames": frames, "deleted_audio": audio, "batches": fb + ab,
                    "budget_exhausted": out1 or out2})
    return receipt


def write_receipt(path: Path, receipt: dict) -> None:
    """覆盖写 + 原子 rename（单文件、无增长——防腐 #4 的「日志有帽」在这里是结构性的）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _iso(now: float) -> str:
    return _dt.datetime.fromtimestamp(now, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def retention_days_from_config(cfg=None) -> int:
    """config 层的 effective 值（config.yaml recording.retention_days ← settings_overrides.json 扁平键）。"""
    cfg = cfg if cfg is not None else _config.load_config()
    return max(0, int(getattr(cfg, "screenpipe_retention_days", 0) or 0))


def run(*, db_path: Optional[Path] = None, state_dir: Optional[Path] = None, days: Optional[int] = None,
        dry_run: bool = False, now: Optional[float] = None, cfg=None) -> dict:
    """一轮完整流程：读旋钮 → 开库 → 清理 → 回执落盘。永不抛（错误进回执 ``error``）。"""
    now = time.time() if now is None else now
    db = Path(db_path or default_db_path())
    started = time.monotonic()
    receipt: dict = {"ran_at": _iso(now), "db": str(db), "error": None}
    try:
        retention = retention_days_from_config(cfg) if days is None else max(0, int(days))
        if not db.is_file():
            receipt.update(prune_skipped(retention, "no_db"))
        else:
            receipt.update(_run_on_db(db, retention, now, dry_run))
    except Exception as exc:  # noqa: BLE001 — §0 第 11 条：任何失败只进回执
        receipt["error"] = "%s: %s" % (type(exc).__name__, exc)
    receipt["duration_s"] = round(time.monotonic() - started, 3)
    try:
        write_receipt(receipt_path(state_dir), receipt)
    except OSError as exc:
        receipt["error"] = (receipt.get("error") or "") + " receipt_write_failed: %s" % exc
    return receipt


def prune_skipped(retention: int, reason: str) -> dict:
    return {"retention_days": int(retention), "skipped": reason, "deleted_frames": 0, "deleted_audio": 0,
            "batches": 0, "budget_exhausted": False, "cutoff": None, "dry_run": False}


def _run_on_db(db: Path, retention: int, now: float, dry_run: bool) -> dict:
    markers = db.parent / "export_markers"
    conn = sqlite3.connect(str(db), timeout=BUSY_TIMEOUT_MS / 1000.0)
    try:
        out = prune(conn, retention_days=retention, now=now, frame_marker=read_marker(markers, MARKER_FRAME),
                    audio_marker=read_marker(markers, MARKER_AUDIO), dry_run=dry_run)
    finally:
        conn.close()
    try:
        out["db_bytes_after"] = db.stat().st_size
    except OSError:
        out["db_bytes_after"] = None
    return out


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m act.lib.screenpipe_retention",
                                     description="Prune exported screenpipe rows older than the retention window.")
    parser.add_argument("--dry-run", action="store_true", help="count only, delete nothing")
    parser.add_argument("--db", default=None, help="db path (default ~/.screenpipe/db.sqlite)")
    parser.add_argument("--days", type=int, default=None, help="override the configured retention (0 = off)")
    args = parser.parse_args(argv)
    receipt = run(db_path=Path(args.db) if args.db else None, days=args.days, dry_run=args.dry_run)
    sys.stdout.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
