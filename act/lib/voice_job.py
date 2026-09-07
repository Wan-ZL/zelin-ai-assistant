"""act/lib/voice_job.py — 「从我的消息生成/更新档案」：inbox 特形动作 ``voice_generate`` 的
actd 落点 + 子进程回执台账（CONTRACT §68.1 追记 / §10 动词表 / §49 `GET /api/voice/generate-status`；D47）。

原生 Settings.swift ``runVoiceGen`` 在 app 进程里同步跑 ``python -m act.voice_gen``（几分钟）、
按钮忙态 + 一句结果。web 看板没有进程可挂，走既有的 inbox 路（§48.7 ``radar_test_round`` 同款）：
server 落 ``{"action": "voice_generate"}``，actd 在 pass 里（``_DETACHED_ACTIONS``）**分离启动**
``python -m act.voice_gen --job``——子进程跑完自己把结果写回同一份 job 文件；actd 永不等它。

台账 ``state/voice_gen/job.json``（单文件、永远只有最近一次——有界，不需要 cap）::

    {"status": "running" | "done" | "failed", "started_at": iso, "finished_at": iso | null,
     "error": str | null, "message": str | null, "profile_path": str | null}

两个写者、先后接力不重叠：actd 在 spawn **之前**写 ``running``（spawn 失败 → ``failed`` +
``error: "launch_failed: …"``）；子进程在 ``generate()`` 之后写 ``done`` / ``failed``（保留
``started_at``；``message`` = act.voice_gen 打给 stdout 的那一句人话，``error`` = 失败时同一句）。
``running`` 超过 :data:`LOST_AFTER_S` 仍无回执 = 子进程崩在 import / 被杀——诚实说「丢了」而不是
永远「生成中」：server 投影 ``lost: true``，actd 也不再把它当正在跑。同一时刻只跑一份：running
且未过期时新的请求 = noop（原生 ``guard !voiceGenRunning``），两份 claude 同时改写档案没有好结果。
子进程 stdout/err 追加 ``state/voice_gen/run.log``（出生带 logcap 帽，防腐 #4）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Callable, Optional

from act.lib import config, detached, logcap

JOB_DIR: Path = config.STATE_DIR / "voice_gen"
JOB_PATH: Path = JOB_DIR / "job.json"
# detached.spawn 的 log_name 相对 STATE_DIR；目录由 request() 先建好
LOG_NAME = "voice_gen/run.log"
# act.voice_gen 的 claude 超时是 600 s；再给启动 / 落盘留余量——超过它还没回执就判 lost
LOST_AFTER_S = 15 * 60

RUNNING = "running"
DONE = "done"
FAILED = "failed"
STATUSES: tuple = (RUNNING, DONE, FAILED)

ACTION = "voice_generate"
MODULE_ARGV: tuple = ("act.voice_gen", "--job")


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value) -> Optional[_dt.datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def load() -> Optional[dict]:
    """最近一次 job；缺失 / 坏文件 / 词表外的 status → None。Never raises。"""
    try:
        data = json.loads(JOB_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 台账坏了不许崩 pass / 端点
        return None
    if not isinstance(data, dict) or data.get("status") not in STATUSES:
        return None
    return data


def is_lost(job: Optional[dict], now: Optional[_dt.datetime] = None) -> bool:
    """running 却超过 LOST_AFTER_S 没回执（起了却没落笔：崩在 import / 被杀）。"""
    if not isinstance(job, dict) or job.get("status") != RUNNING:
        return False
    since = _parse_iso(job.get("started_at"))
    if since is None:
        return True   # running 却连开始时间都读不出——没法再等它
    when = now if now is not None else _dt.datetime.now(_dt.timezone.utc)
    return (when - since).total_seconds() > LOST_AFTER_S


def is_running(job: Optional[dict], now: Optional[_dt.datetime] = None) -> bool:
    return isinstance(job, dict) and job.get("status") == RUNNING and not is_lost(job, now)


def _write(data: dict) -> None:
    """原子写（tmp + replace）。写失败只影响回执，不反噬调用方。"""
    try:
        JOB_DIR.mkdir(parents=True, exist_ok=True)
        tmp = JOB_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, JOB_PATH)
    except OSError:
        pass


def _record(status: str, started_at: str, *, finished_at: Optional[str] = None,
            error: Optional[str] = None, message: Optional[str] = None,
            profile_path: Optional[str] = None) -> dict:
    data = {"status": status, "started_at": started_at, "finished_at": finished_at,
            "error": error, "message": message, "profile_path": profile_path}
    _write(data)
    return data


def mark_running() -> dict:
    """actd 侧：spawn 之前落 running（started_at = now）。"""
    return _record(RUNNING, _iso_now())


def mark_launch_failed(reason: str) -> dict:
    now = _iso_now()
    return _record(FAILED, now, finished_at=now, error=f"launch_failed: {reason}"[:300])


def finish(ok: bool, message: str, profile_path: Optional[str] = None) -> dict:
    """子进程侧（``act.voice_gen --job``）：generate() 之后落 done / failed，保留 actd 写的
    started_at（缺席——比如 CLI 手动带 --job——就用 finished_at 补）。"""
    now = _iso_now()
    prev = load()
    started = prev.get("started_at") if isinstance(prev, dict) and isinstance(prev.get("started_at"), str) else now
    if ok:
        return _record(DONE, started, finished_at=now, message=message, profile_path=profile_path)
    return _record(FAILED, started, finished_at=now, error=message)


def request(decision: dict, log: Optional[Callable[[str], None]] = None) -> str:
    """actd 侧入口：没在跑才 ``detached.launch(["act.voice_gen", "--job"])`` → 记台账。
    返回 §5.4 ack 词表（running / noop）。绝不 raise。"""
    say = log or (lambda _msg: None)
    if not isinstance(decision, dict) or decision.get("action") != ACTION:
        say(f"inbox: {ACTION} malformed decision — dropped")
        return detached.NOOP
    if is_running(load()):
        # 原生 guard !voiceGenRunning：一份在跑就不再起第二份（两份 claude 同时改档案没有好结果）
        say(f"inbox: {ACTION} — a generation is already running, not started again")
        return detached.NOOP
    try:
        JOB_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        say(f"inbox: {ACTION} launch FAILED: {e}")
        mark_launch_failed(str(e))
        return detached.NOOP
    logcap.cap(config.STATE_DIR / LOG_NAME)   # 防腐 #4：append-only 日志出生即带帽
    mark_running()                             # spawn 之前落笔：子进程回执只覆盖、不竞争
    # detached.launch 的同一套（Popen 新会话、永不 wait）；自己接异常是为了把原因写进台账给 web 的结果行
    try:
        detached.spawn(list(MODULE_ARGV), LOG_NAME)
    except Exception as e:  # noqa: BLE001 — never let a button kill the pass
        say(f"inbox: {ACTION} launch FAILED: {e}")
        mark_launch_failed(str(e))
        return detached.NOOP
    say(f"inbox: {ACTION} — subprocess started")
    return detached.RUNNING
