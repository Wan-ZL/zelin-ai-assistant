"""server/ingest_run.py — 「录制与数据接入」页「手动触发」的 server 落点（§15.2 / §54.4 / §68）：
``POST /api/ingest/export {}`` · ``POST /api/ingest/run {}`` · ``GET /api/ingest/jobs/{id}``。

原生 Pages.swift IngestModel.runExport / runIngest：起 repo 的 ``ingest/screenpipe-export.sh`` /
``ingest/process-screenpipe.sh``（后者带 ``SCREENPIPE_NO_WAIT=1``——手动点没有导出在抢跑，跳过
脚本的 90 s 半截写入守卫），完成后按退出码给一句：0 = 完成 ✓；ingest 的 3 = 另一个 ingest 持锁
（通常是 cron 那轮）→ 「已有 ingest 在运行，本次跳过」；其它 = 失败 (exit N) + 输出尾巴。web 版跑
**同一条脚本、同一套退出码**：server 只起子进程并把 ``{ok, rc, skipped, tail, seconds}`` 交回，
判词在页面（与原生一样按 rc 判）。server 不改写脚本、不碰 registry——脚本写的是 vault / inbox，
与 cron 那轮同一条路（宪法第 1 条不动）。

**异步 + 轮询**（ingest 含一次 headless claude，可能跑十几分钟；壳里的 WKWebView 对一个 fetch 只等
60 s，同步等会把成功的一轮报成超时）：POST 立刻回 ``{"ok", "job", "state": "running"}`` 并在后台线程
跑脚本；``GET /api/ingest/jobs/{id}`` 回 ``{"id", "script", "state": "running" | "done", "started_at"[, 回执五键]}``，
未知 id 404。同一条脚本已有一轮在跑 → 复用那个 job（``reused: true``；原生 ``guard !exportRunning`` 同款）。
job 表进程内、最多留 :data:`JOBS_CAP` 条（旧的 done 先淘汰）——不是账本，server 重启即清。超时 export 5 min /
ingest 15 min（subproc 约定 rc 124）；脚本缺席 404；``runner`` / ``spawn`` 注入缝，测试绝不真跑脚本、不起线程。
"""
from __future__ import annotations

import datetime as _dt
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from server import paths, subproc
from server.errors import NotFoundError, UnknownFieldError

EXPORT_SCRIPT = "ingest/screenpipe-export.sh"
INGEST_SCRIPT = "ingest/process-screenpipe.sh"
EXPORT_TIMEOUT_S = 300
INGEST_TIMEOUT_S = 900
INGEST_SKIP_RC = 3          # process-screenpipe.sh：另一个 ingest 持锁（与原生同一约定）
TAIL_CHARS = 400            # 原生状态行只留 120 字，tooltip 才是全尾巴——这里给页面 400 字自己截
JOBS_CAP = 20

Spawn = Callable[[Callable[[], None]], None]

_lock = threading.Lock()
_jobs: dict = {}            # job id -> record（见 job_status）


def _default_spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="zai-ingest-run", daemon=True).start()


def _iso(now: float) -> str:
    return _dt.datetime.fromtimestamp(now, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gate(payload: dict, script_rel: str) -> None:
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    if not (paths.repo_root() / script_rel).is_file():
        raise NotFoundError("ingest script not found", {"path": str(paths.repo_root() / script_rel)})


def _receipt(rc: int, out: str, err: str, skip_rc: Optional[int], seconds: float) -> dict:
    tail = subproc.tail("\n".join(part for part in (out, err) if part), TAIL_CHARS)
    return {"ok": rc == 0, "rc": rc, "skipped": skip_rc is not None and rc == skip_rc,
            "tail": tail, "seconds": round(seconds, 1)}


def _running_job(script_rel: str) -> Optional[dict]:
    for job in _jobs.values():
        if job["script"] == script_rel and job["state"] == "running":
            return job
    return None


def _evict_done() -> None:
    """超过上限时先淘汰最老的 done（running 的永不淘汰——页面还在轮询它）。"""
    while len(_jobs) > JOBS_CAP:
        done = [jid for jid, job in _jobs.items() if job["state"] == "done"]
        if not done:
            return
        del _jobs[done[0]]


def _start(home: Path, script_rel: str, timeout_s: int, skip_rc: Optional[int], runner,
           extra_env: Optional[dict], now: Callable[[], float], spawn: Optional[Spawn]) -> dict:
    with _lock:
        running = _running_job(script_rel)
        if running is not None:
            return {"ok": True, "job": running["id"], "state": "running", "script": script_rel, "reused": True}
        job_id = uuid.uuid4().hex[:12]
        t0 = now()
        _jobs[job_id] = {"id": job_id, "script": script_rel, "state": "running", "started_at": _iso(t0)}
        _evict_done()

    def work() -> None:
        rc, out, err = subproc.run_script(home, script_rel, timeout_s=timeout_s, runner=runner,
                                          extra_env=extra_env)
        receipt = _receipt(rc, out, err, skip_rc, now() - t0)
        with _lock:
            _jobs[job_id].update(receipt, state="done")

    (spawn or _default_spawn)(work)
    return {"ok": True, "job": job_id, "state": "running", "script": script_rel, "reused": False}


def export_now(home: Path, payload: dict, runner=None, now: Callable[[], float] = time.time,
               spawn: Optional[Spawn] = None) -> dict:
    """``POST /api/ingest/export {}``：原生「立即导出」= ``bash ingest/screenpipe-export.sh``（后台起，回 job id）。"""
    _gate(payload, EXPORT_SCRIPT)
    return _start(home, EXPORT_SCRIPT, EXPORT_TIMEOUT_S, None, runner, None, now, spawn)


def ingest_now(home: Path, payload: dict, runner=None, now: Callable[[], float] = time.time,
               spawn: Optional[Spawn] = None) -> dict:
    """``POST /api/ingest/run {}``：原生「立即 ingest」= ``SCREENPIPE_NO_WAIT=1 bash ingest/process-screenpipe.sh``。"""
    _gate(payload, INGEST_SCRIPT)
    return _start(home, INGEST_SCRIPT, INGEST_TIMEOUT_S, INGEST_SKIP_RC, runner,
                  {"SCREENPIPE_NO_WAIT": "1"}, now, spawn)


def job_status(job_id: str) -> dict:
    """``GET /api/ingest/jobs/{id}``：running 只有四键；done 多出 ok / rc / skipped / tail / seconds。"""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise NotFoundError("no such ingest job", {"job": str(job_id)[:40]})
        return dict(job)


def reset_jobs_for_tests() -> None:
    with _lock:
        _jobs.clear()
