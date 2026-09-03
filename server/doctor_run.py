"""server/doctor_run.py — ``GET /api/doctor`` 的落点：跑 ``python -m act.doctor --json``（§25 / §49 / §68）。

原生「依赖检查」页 = ``python -m act.doctor --json [--fast]`` 的表格渲染。这里是
同一条命令的 server 侧：子进程经 server/subproc（注入缝），stdout 的 JSON
（``{"home", "checks": [{name, status, detail, fix, failure_id, action_id}]}``）
原样透出，外加 ``ran_at`` / ``fast`` / ``rc`` 三个 add-only 键；doctor 自己崩了
（非 JSON 输出）→ ``ok:false`` + 输出尾巴，页面如实显示而不是 500。

进程内缓存 TTL 15 s（``--fast`` 也要跑 launchctl / 文件探针，几秒钟）：权限体检页
与诊断页同时打开只跑一次；``?refresh=1`` 绕过缓存。
"""
from __future__ import annotations

import datetime as _dt
import threading
import time
from pathlib import Path
from typing import Optional

from server import subproc

CACHE_TTL_S = 15
TIMEOUT_S = 120

_lock = threading.Lock()
_cache: dict = {}   # (str(home), fast) -> (expires_at, result)


def _iso(now: float) -> str:
    return _dt.datetime.fromtimestamp(now, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _failed(home: Path, rc: int, fast: bool, now: float, text: str) -> dict:
    return {"ok": False, "checks": [], "home": str(home), "rc": rc, "fast": fast,
            "ran_at": _iso(now), "error": subproc.tail(text) or "doctor produced no JSON"}


def _succeeded(home: Path, rc: int, fast: bool, now: float, doc: dict, rows: list) -> dict:
    checks = [c for c in rows if isinstance(c, dict)]
    return {"ok": True, "checks": checks, "home": str(doc.get("home") or home), "rc": rc,
            "fast": fast, "ran_at": _iso(now)}


def _run(home: Path, fast: bool, runner, now: float) -> dict:
    args = ["--json", "--fast"] if fast else ["--json"]
    rc, out, err = subproc.run_module(home, "act.doctor", args, timeout_s=TIMEOUT_S, runner=runner)
    doc = subproc.parse_json_output(out) or {}
    rows = doc.get("checks")
    if not isinstance(rows, list):
        return _failed(home, rc, fast, now, err or out)
    return _succeeded(home, rc, fast, now, doc, rows)


def report(home: Path, *, fast: bool = True, refresh: bool = False, runner=None,
           now: Optional[float] = None) -> dict:
    """缓存包装：同 (home, fast) 15 s 内复用；``refresh`` 强制重跑。"""
    now = time.time() if now is None else now
    key = (str(home), fast)
    with _lock:
        hit = _cache.get(key)
        if hit and not refresh and hit[0] > now:
            return dict(hit[1])
    result = _run(home, fast, runner, now)
    with _lock:
        _cache[key] = (now + CACHE_TTL_S, result)
    return dict(result)


def reset_cache_for_tests() -> None:
    with _lock:
        _cache.clear()


def counts(checks: list) -> dict:
    out = {"ok": 0, "warn": 0, "fail": 0}
    for c in checks:
        status = str(c.get("status") or "").lower()
        if status in out:
            out[status] += 1
    return out
