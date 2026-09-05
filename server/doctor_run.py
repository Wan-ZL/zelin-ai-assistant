"""server/doctor_run.py — ``GET /api/doctor`` 的落点：跑 ``python -m act.doctor --json``（§25 / §49 / §68）。

原生「依赖检查」页 = ``python -m act.doctor --json [--fast]`` 的表格渲染。这里是
同一条命令的 server 侧：子进程经 server/subproc（注入缝），stdout 的 JSON
（``{"home", "checks": [{name, status, detail, fix, failure_id, action_id}]}``）
原样透出，外加 ``ran_at`` / ``fast`` / ``rc`` 三个 add-only 键；doctor 自己崩了
（非 JSON 输出）→ ``ok:false`` + 输出尾巴，页面如实显示而不是 500。

进程内缓存 TTL 15 s（``--fast`` 也要跑 launchctl / 文件探针，几秒钟）：权限体检页
与诊断页同时打开只跑一次；``?refresh=1`` 绕过缓存。

2026-09-05 追记（§68.4；原生 Pages.swift runFullOutput 的 ``AIASSISTANT_UI_LANG``）：``?lang=zh|en``
（``parse_lang`` 校验，其它值 400）经 ``extra_env`` 传给子进程——doctor 的 detail / fix 人话
（act/lib/failures.ui_lang 第一级）随 web 的当前语言而不是随守护进程的 locale；lang 进缓存键
（同一 home 两种语言各一份 15 s）。不带 ``lang`` = 老行为（python 侧按持久化设置 / locale 定），
且**不挑语言**：权限体检 / 向导 / 让 AI 修这些只看 status / name / failure_id 的调用复用任一
语言的新鲜条目——依赖检查区刚跑完 doctor，紧接着开权限体检不许再跑一遍。

status 词表（§25 ``ok|warn|fail`` 小写，act/lib/checks/core 的常量）在 ``_succeeded`` 归一：
这里是唯一边界，下游（web / permissions / ai_fix_launch）只认小写、不再各自比大小写。
"""
from __future__ import annotations

import datetime as _dt
import threading
import time
from pathlib import Path
from typing import Optional

from server import subproc
from server.errors import InvalidFieldError

CACHE_TTL_S = 15
TIMEOUT_S = 120
LANGS = ("zh", "en")   # §15 UI 语言词表（ai_fix_launch._LANGS 同款）

_lock = threading.Lock()
_cache: dict = {}   # (str(home), fast, lang) -> (expires_at, result)


def parse_lang(raw) -> Optional[str]:
    """URL query 的 ``lang``：缺席 / 空 = None（不注入 env）；zh / en 原样；其它 400。"""
    if raw is None or raw == "":
        return None
    if raw in LANGS:
        return raw
    raise InvalidFieldError("lang must be zh or en", {"lang": str(raw)[:20]})


def _iso(now: float) -> str:
    return _dt.datetime.fromtimestamp(now, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _failed(home: Path, rc: int, fast: bool, now: float, text: str) -> dict:
    return {"ok": False, "checks": [], "home": str(home), "rc": rc, "fast": fast,
            "ran_at": _iso(now), "error": subproc.tail(text) or "doctor produced no JSON"}


def _normalized(row: dict) -> dict:
    """§25 status 词表 ``ok|warn|fail`` 小写——doctor 本来就这么吐，这里是下游唯一的归一边界。"""
    return {**row, "status": str(row.get("status") or "").lower()}


def _succeeded(home: Path, rc: int, fast: bool, now: float, doc: dict, rows: list) -> dict:
    checks = [_normalized(c) for c in rows if isinstance(c, dict)]
    return {"ok": True, "checks": checks, "home": str(doc.get("home") or home), "rc": rc,
            "fast": fast, "ran_at": _iso(now)}


def _run(home: Path, fast: bool, runner, now: float, lang: Optional[str]) -> dict:
    args = ["--json", "--fast"] if fast else ["--json"]
    extra_env = {"AIASSISTANT_UI_LANG": lang} if lang else None
    rc, out, err = subproc.run_module(home, "act.doctor", args, timeout_s=TIMEOUT_S, runner=runner,
                                      extra_env=extra_env)
    doc = subproc.parse_json_output(out) or {}
    rows = doc.get("checks")
    if not isinstance(rows, list):
        return _failed(home, rc, fast, now, err or out)
    return _succeeded(home, rc, fast, now, doc, rows)


def _fresh(home_key: str, fast: bool, lang: Optional[str], now: float) -> Optional[dict]:
    """新鲜的缓存条目。指定了 lang 只认同语言那份；``lang=None``（权限体检 / 向导 / 让 AI 修——
    只看 status / name / failure_id）任一语言的新鲜条目都算，取最晚写入的那份。调用方持锁。"""
    exact = _cache.get((home_key, fast, lang))
    if exact and exact[0] > now:
        return exact[1]
    if lang is not None:
        return None
    fresh = [v for (h, f, _l), v in _cache.items() if h == home_key and f == fast and v[0] > now]
    return max(fresh, key=lambda v: v[0])[1] if fresh else None


def report(home: Path, *, fast: bool = True, refresh: bool = False, runner=None,
           now: Optional[float] = None, lang: Optional[str] = None) -> dict:
    """缓存包装：同 (home, fast, lang) 15 s 内复用（lang=None 不挑语言）；``refresh`` 强制重跑。"""
    now = time.time() if now is None else now
    key = (str(home), fast, lang)
    with _lock:
        hit = None if refresh else _fresh(key[0], fast, lang, now)
    if hit is not None:
        return dict(hit)
    result = _run(home, fast, runner, now, lang)
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
