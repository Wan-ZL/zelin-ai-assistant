"""GET /api/health — the pipeline liveness the web board shows as a banner.

CONTRACT §47.4 / §49. Three read-only files, one verdict:

- ``state/actd.heartbeat`` (§47.4) — mtime = last time the actd loop touched a
  phase boundary; the body carries the WRITER's ``stale_after_s`` so this
  module never re-derives the stall threshold (single owner: act/lib/heartbeat).
- ``state/dashboard.json`` ``generated_at`` — what the board renders from.
- ``state/loop_health.json`` (§47.3) — consecutive pass crashes.

Verdict ladder (first match wins; mirrors what doctor's ``actd heartbeat`` +
``dashboard`` rows would say, minus process probing — the server has no
launchctl and must not spawn):

    "stalled"  heartbeat present but older than stale_after_s   (2026-08-31 22:31)
    "failing"  loop_health.consecutive_failures >= 3            (§47.3 alarm)
    "stale"    no heartbeat file AND dashboard older than 90 s  (pre-v0.48.4 daemon / dead)
    "unknown"  no heartbeat file, dashboard fresh                (old daemon still writing)
    "ok"       heartbeat fresh

Add-only `unreadable` block (§47.4 amendment 2026-09-18, issue #423): the three
readers above turn every OSError into "absent", which reported a per-process
read denial as "no data yet" — constitution #3 wants those told apart. Each
block that could not be READ (as opposed to being absent or torn) also lands in
`unreadable[<block name>] = {errno, strerror}`; the key is always present, `{}`
when everything read cleanly, so its absence means "old server", not "clean".
The verdict ladder above is deliberately unchanged: `verdict` is a claim about
*pipeline* liveness (actd may be beating perfectly while this process cannot
read a file), and a new rung would outrank "stalled" and render nowhere.

server/ is stdlib-only and never imports act (§49); the file layout is mirrored
in server/paths.py and pinned by tests/test_server_paths_mirror.py.
"""
from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path
from typing import Optional

from server import paths, state_read

# mirrors act/lib/heartbeat.STALE_FLOOR_SECONDS and doctor.DASHBOARD_FRESH_SECONDS
DASHBOARD_FRESH_SECONDS = 90
LOOP_ALARM_AFTER = 3   # mirrors act/actd.LOOP_ALARM_AFTER (§47.3)


def _note_failure(unreadable: dict, block: str, exc: OSError) -> None:
    """读不了 ≠ 不在（宪法第 3 条）：非「文件不在」的 OSError 记进 ``unreadable``；
    缺席照旧静默——那是首次安装的正常状态，不是故障源（§47.4 原文不动）。"""
    if not state_read.is_absent(exc):
        unreadable[block] = state_read.failure_detail(exc)


def _read_json(p: Path, unreadable: dict, block: str) -> Optional[dict]:
    """读一个 state JSON；语义与旧版逐字相同（读不到/解不出 → None），只多一件
    事：真·读失败顺手记进 ``unreadable[block]``。"""
    doc, failure = state_read.read_json(p)
    if failure is not None:
        unreadable[block] = failure
    return doc


def _age(p: Path, now: float, unreadable: dict, block: str) -> Optional[float]:
    """mtime 年龄。``stat()`` 与 ``read`` 要的权限不同（前者只要目录可穿越），
    同一次拒绝可能只炸其中一个——两处都记，否则 verdict 会因为哪个 syscall
    先被拒而给出不同答案，却没有一行说明为什么。"""
    try:
        return max(0.0, now - p.stat().st_mtime)
    except OSError as exc:
        _note_failure(unreadable, block, exc)
        return None


def _parse_iso(ts) -> Optional[float]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


def _stale_after(body: dict) -> int:
    """The writer's own ``stale_after_s`` (torn/absent → the dashboard floor)."""
    try:
        return int(body.get("stale_after_s") or 0) or DASHBOARD_FRESH_SECONDS
    except (TypeError, ValueError):
        return DASHBOARD_FRESH_SECONDS


def _heartbeat_view(home: Path, now: float, unreadable: dict) -> Optional[dict]:
    """``heartbeat`` block, or None when the file is missing (pre-v0.48.4 daemon)."""
    hb_path = paths.heartbeat_path(home)
    hb_age = _age(hb_path, now, unreadable, "heartbeat")
    if hb_age is None:
        return None
    hb_body = _read_json(hb_path, unreadable, "heartbeat") or {}
    stale_after = _stale_after(hb_body)
    return {
        "age_s": round(hb_age, 1),
        "phase": hb_body.get("phase"),
        "pid": hb_body.get("pid"),
        "interval": hb_body.get("interval"),
        "stale_after_s": stale_after,
        "stale": hb_age > stale_after,
    }


def _dashboard_view(home: Path, now: float, unreadable: dict) -> Optional[dict]:
    """``dashboard`` block, or None when there is no parseable ``generated_at``
    —— 包括读不出来时（wire 值仍是 null：让它变成真值会让 web 的
    ``dashboard.age_s`` 镜像当场说谎，§47.4 追记 2026-09-18）；读不了的那次
    由 ``unreadable["dashboard"]`` 报出来。"""
    dash_body = _read_json(paths.dashboard_path(home), unreadable, "dashboard") or {}
    gen_ts = _parse_iso(dash_body.get("generated_at"))
    if gen_ts is None:
        return None
    d_age = max(0.0, now - gen_ts)
    return {"generated_at": dash_body.get("generated_at"),
            "age_s": round(d_age, 1),
            "stale": d_age > DASHBOARD_FRESH_SECONDS}


def _loop_health_view(home: Path, unreadable: dict) -> dict:
    """``loop_health`` block; a non-int / bool / negative counter reads as 0."""
    lh = _read_json(paths.loop_health_path(home), unreadable, "loop_health") or {}
    failures = lh.get("consecutive_failures")
    if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
        failures = 0
    return {"consecutive_failures": failures,
            "last_error": lh.get("last_error") if failures else None}


def _beating_verdict(heartbeat: dict, failures: int) -> str:
    """Heartbeat file present: stalled beats failing beats ok."""
    if heartbeat["stale"]:
        return "stalled"
    if failures >= LOOP_ALARM_AFTER:
        return "failing"
    return "ok"


def _verdict(heartbeat: Optional[dict], dashboard: Optional[dict],
             failures: int) -> str:
    """The ladder from the module docstring — first match wins."""
    if heartbeat is not None:
        return _beating_verdict(heartbeat, failures)
    if failures >= LOOP_ALARM_AFTER:
        return "failing"
    if dashboard is None or dashboard["stale"]:
        return "stale"
    return "unknown"


def snapshot(home: Path, now: Optional[float] = None) -> dict:
    """The /api/health body. Never raises; missing files are reported as such,
    and files that exist but could not be read are reported in ``unreadable``
    (add-only, §47.4 amendment 2026-09-18 — the route still always answers 200)."""
    now = time.time() if now is None else now
    unreadable: dict = {}
    heartbeat = _heartbeat_view(home, now, unreadable)
    dashboard = _dashboard_view(home, now, unreadable)
    loop_health = _loop_health_view(home, unreadable)
    return {
        "verdict": _verdict(heartbeat, dashboard, loop_health["consecutive_failures"]),
        "heartbeat": heartbeat,
        "dashboard": dashboard,
        "loop_health": loop_health,
        "unreadable": unreadable,
        "checked_at": _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
