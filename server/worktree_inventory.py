"""server/worktree_inventory.py — 开发者区的 worktree 清点与一键清理（CONTRACT §75.4；
路由 §49 ``GET /api/worktrees`` / ``POST /api/worktrees/cleanup``）。

模块名按对象「worktree 清点」取 ``worktree_inventory``（防腐 #9：与真源
``act/lib/worktrees.py`` 同名会撞「同一 basename 禁止出现在两个目录层级」，
同 ``server/slack_directory.py`` 之于 ``act/lib/slack_setup.py`` 的先例）。

issue #315 第 3 条：设置「开发者」区要看得见 `.claude/worktrees/` 攒了多少条、占多少盘，
并给一键清理。判决与执行全在 `act/lib/worktrees.py`（唯一真源）——server **不 import act**
（§49），两个端点都经 `server/subproc.run_module` 起 ``python -m act.lib.worktrees``：

- ``GET``：``--json``。扫目录 + `du -sk` 可能要几十秒，所以走与 §72.1 录制磁盘同一套
  「立刻回缓存、后台线程重算」的形制——首次回 ``state: "computing"`` 的空壳，``?refresh=1``
  强制重算。GET 路径本身零子进程、零磁盘。
- ``POST /api/worktrees/cleanup``：``--sweep --json``（``{"dry_run": true}`` → ``--dry-run``，
  一个字节都不动）。同步跑（owner 刚按下按钮，回执要是真回执），跑完把缓存作废。

子进程起不来 / 没给 JSON 一律 ``ok:false`` + 一句尾巴，不 500（§0 第 11 条）。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from server import background_jobs, subproc
from server.errors import UnknownFieldError

INVENTORY_TIMEOUT_S = 180
CLEANUP_TIMEOUT_S = 600
CACHE_TTL_S = 300.0
MODULE = "act.lib.worktrees"

_lock = threading.Lock()
_cache: dict = {}    # str(home) -> {"snapshot": dict|None, "computed_at": float, "inflight": bool}


def _run(home: Path, args: list, timeout_s: int, runner) -> dict:
    """子进程的一行 JSON；没给 JSON = `state: "error"` 的失败壳（不抛、不 500）。
    `state` 这个键让调用方分得清「子进程没起来」与「清点跑完了但 ok:false」——
    后者是一份完整快照，前者什么数字都没有。"""
    rc, out, err = subproc.run_module(home, MODULE, args, timeout_s=timeout_s, runner=runner)
    doc = subproc.parse_json_output(out)
    if doc is None:
        tail = subproc.tail(err or out) or ("worktrees exited %s" % rc)
        return {"ok": False, "state": "error",
                "error": "no_python" if rc == 127 else "worktrees_failed",
                "message": tail}
    return doc


def placeholder() -> dict:
    return {"state": "computing", "ok": True, "scanned_at": None, "repos": [], "worktrees": None,
            "removable": None, "bytes": None, "bytes_partial": False, "truncated": False}


_THREADS = background_jobs.Threads("worktrees-inventory")


def _spawn_thread(fn: Callable[[], None]) -> None:
    _THREADS.spawn(fn)


def _finish(key: str, result: dict, now: float) -> None:
    with _lock:
        entry = _cache.setdefault(key, {})
        entry.update({"snapshot": result, "computed_at": now, "inflight": False})


def _job(home: Path, key: str, now: float, runner) -> None:
    """后台线程：算完落缓存。子进程失败的那一支**补满 `placeholder()` 的全部键**再落
    （`worktrees: null` 而不是键根本不在）并留着 `state: "error"`——前端逐字镜像 wire
    键，少一个键就会在数字位上渲染出 `undefined`（防腐 #10；同 screenpipe_disk._job）。"""
    try:
        doc = _run(home, ["--json"], INVENTORY_TIMEOUT_S, runner)
        result = (dict(placeholder(), **doc) if doc.get("state") == "error"
                  else dict(doc, state="ready"))
    except Exception as exc:  # noqa: BLE001 - 后台线程里的任何失败都要落成 state=error
        result = dict(placeholder(), state="error", ok=False,
                      error="%s: %s" % (type(exc).__name__, exc))
    _finish(key, result, now)


def _claim(key: str, refresh: bool, now: float) -> "tuple[bool, Optional[dict], bool]":
    with _lock:
        entry = _cache.setdefault(key, {"snapshot": None, "computed_at": 0.0, "inflight": False})
        stale = entry["snapshot"] is None or refresh or now - entry["computed_at"] >= CACHE_TTL_S
        start = stale and not entry["inflight"]
        if start:
            entry["inflight"] = True
        return start, entry["snapshot"], entry["inflight"]


def invalidate(home: Path) -> None:
    """清理跑完后作废缓存——下一次 GET 立刻重算，页面不许显示刚被删掉的那些条。"""
    with _lock:
        _cache.pop(str(home), None)


def snapshot(home: Path, *, refresh: bool = False, now: Optional[float] = None,
             runner=None, spawn: Callable[[Callable[[], None]], None] = _spawn_thread) -> dict:
    """``GET /api/worktrees[?refresh=1]``：返回缓存（首次 = computing 空壳）；过期 /
    refresh 且没有在算 → 起一个后台算。"""
    now = time.time() if now is None else now
    key = str(home)
    start, cached, inflight = _claim(key, refresh, now)
    if start:
        spawn(lambda: _job(home, key, now, runner))
    base = dict(cached) if cached is not None else placeholder()
    base["refreshing"] = inflight or start
    return base


def cleanup(home: Path, payload: dict, *, runner=None) -> dict:
    """``POST /api/worktrees/cleanup``：``{}`` = 真扫，``{"dry_run": true}`` = 只报会删谁。"""
    unknown = sorted(set(payload or {}) - {"dry_run"})
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": unknown})
    dry = bool((payload or {}).get("dry_run"))
    doc = _run(home, ["--dry-run" if dry else "--sweep"], CLEANUP_TIMEOUT_S, runner)
    doc.setdefault("ok", True)
    doc.setdefault("removed", [])
    if not dry:
        invalidate(home)
    return doc


def join_jobs_for_tests(timeout: float = background_jobs.JOIN_TIMEOUT_S) -> bool:
    """§75.4 的测试缝：等在飞的后台清点落地（有界；``False`` = 到点还有活的）。"""
    return _THREADS.join(timeout)


def reset_cache_for_tests() -> None:
    """清场 = **先等在飞的后台清点落地**再清缓存：判例的 `mock.patch` 一 stop，还活着的
    `_job` 就会拿回真 runner 去起 `python -m act.lib.worktrees` 这个真子进程，而它的临时
    home 可能已经被 `rmtree` 掉了（同 `screenpipe_disk`，CI 2026-09-15 的 Errno 39）。
    判例要把这一下的 ``addCleanup`` 排在临时目录 / patcher 那几下**之后**登记（LIFO）。"""
    join_jobs_for_tests()
    with _lock:
        _cache.clear()
