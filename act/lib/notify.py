"""Native notifications through the shell's relay queue (CONTRACT §28).

On darwin nothing here ever runs osascript: :func:`notify` queues one JSON file
into ``state/notify_queue/`` for the shell app, which posts it via
UNUserNotificationCenter (the app's own name and icon) and deletes the file.
There is NO fallback by owner decision (2026-07-10): app closed = no native
notification.

v2.0.0 (constitution art.10): the only kinds left are ``recap_ready`` (§63 — a
meeting recap landed) and ``failure`` (recording / ingest broke and needs a
human). The card-lifecycle kinds, their per-category switches and message
builders retired with the Act pipeline (full text in tag ``full``); a stray
entry without a kind still posts.

Quiet hours (§28 追记, issue #29) are judged by the **writer**: the queue sweeps
entries older than 10 minutes, so "hold it until morning" would be a lie —
inside the window the entry is simply not queued. ``failure`` pierces quiet
hours (a broken recorder at 2am is still worth knowing about).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from act.lib import platform

KIND_RECAP_READY = "recap_ready"    # §63 会议纪要已生成
KIND_FAILURE = "failure"            # 录制 / ingest 坏到需要人修（穿透安静时段）
KINDS: tuple = (KIND_RECAP_READY, KIND_FAILURE)
QUIET_HOURS_EXEMPT = frozenset({KIND_FAILURE})

STALE_AFTER_S = 600.0   # §28 stale storm guard (both sides, 10 min)


def notify(title: str, body: str, subtitle: Optional[str] = None,
           kind: Optional[str] = None) -> bool:
    """Fire a native notification via the app relay queue (§28). Never raises.

    Returns whether the notification was handed to the consumer. A deliberate
    **True** for one suppressed by quiet hours: that is not a failure — the
    only False means "could not hand it over" (an unwritable queue dir).
    """
    if suppressed_now(kind):
        return True
    if not platform.is_darwin():
        return platform.notify_user(title, body, subtitle)
    return _queue_write(title, body, subtitle, kind=kind) is not None


# --------------------------------------------------------------------------- #
# §28 quiet hours
# --------------------------------------------------------------------------- #
def _minute_of_day(hhmm) -> Optional[int]:
    """``"HH:MM"`` → 当日分钟数；坏值 None（安静时段当关——fail-open）。"""
    from act.lib import config as _config
    try:
        text = _config.coerce_clock_time(hhmm)
    except (TypeError, ValueError):
        return None
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def in_quiet_hours(start, end, minute_of_day: int) -> bool:
    """半开区间 ``[start, end)`` 是否盖住当日第 ``minute_of_day`` 分钟。跨午夜算两段；
    ``start == end`` 或任一端坏值 = 关。"""
    a, b = _minute_of_day(start), _minute_of_day(end)
    if a is None or b is None or a == b:
        return False
    if a < b:
        return a <= minute_of_day < b
    return minute_of_day >= a or minute_of_day < b


def suppression_reason(kind, cfg, now=None) -> Optional[str]:
    """``"quiet_hours"`` when this notification must be dropped, None = post it.
    ``cfg`` = ``act.lib.config.Config``; ``now`` = local ``time.struct_time``."""
    if kind in QUIET_HOURS_EXEMPT or not bool(getattr(cfg, "quiet_hours_enabled", False)):
        return None
    local = now if now is not None else time.localtime()
    if in_quiet_hours(getattr(cfg, "quiet_hours_start", ""), getattr(cfg, "quiet_hours_end", ""),
                      local.tm_hour * 60 + local.tm_min):
        return "quiet_hours"
    return None


def suppressed_now(kind) -> bool:
    """Read the preference fresh and judge; any surprise = post (fail-open)."""
    try:
        from act.lib import config as _config
        return suppression_reason(kind, _config.load_config()) is not None
    except Exception:  # noqa: BLE001 - a notification must never break a caller
        return False


# --------------------------------------------------------------------------- #
# app relay queue (§28)
# --------------------------------------------------------------------------- #
def _queue_write(title: str, body: str, subtitle: Optional[str] = None,
                 kind: Optional[str] = None,
                 now: Optional[float] = None) -> Optional[Path]:
    """Write one §28 queue entry (atomic .json.tmp + rename). Sweeps stale
    siblings first so an always-closed app can't grow the dir without bound.
    Returns the queue file path, or None on ANY failure."""
    try:
        from act.lib import config as _config
        qdir = Path(_config.NOTIFY_QUEUE_DIR)
        qdir.mkdir(parents=True, exist_ok=True)
        _sweep_stale(qdir, now=now)
        nid = uuid.uuid4().hex
        entry = {"id": nid, "title": str(title), "body": str(body),
                 "created_at": int(now if now is not None else time.time())}
        if subtitle:
            entry["subtitle"] = str(subtitle)
        if kind:
            entry["kind"] = str(kind)
        target = qdir / (nid + ".json")
        tmp = qdir / (nid + ".json.tmp")   # the app only ever matches *.json
        try:
            tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)    # no corpse on a failed rename
        return target
    except Exception:  # noqa: BLE001 - a notification must never break a caller
        return None


def _sweep_stale(qdir: Path, now: Optional[float] = None) -> int:
    """Delete queue entries (and tmp corpses) older than STALE_AFTER_S. Never raises."""
    removed = 0
    cutoff = (now if now is not None else time.time()) - STALE_AFTER_S
    try:
        for f in qdir.iterdir():
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed
