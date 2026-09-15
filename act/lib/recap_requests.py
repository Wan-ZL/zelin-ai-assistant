"""act/lib/recap_requests.py — 「重新生成 / 现在生成」的请求台账 + 行投影 ``generate_request``（CONTRACT §63.8）。

issue #297：按「重新生成」只换来一条 toast，新版本落地前后面板看起来一模一样——
一次 59 秒的成功被读成失败。§48.7 ``radar_rounds`` 的模式原样搬过来：

- ``state/recap_requests.json``（**actd 单写者**；``state/recap/`` 整目录仍归
  act/recap.py，这里不碰它）：``{<meeting_key>: {"requested_at": iso, "launch":
  "running"|"noop", "note": null|"launch_failed"}}``——每键一条（同键再按 = 覆盖），
  写时剪掉超过 :data:`TTL_S` 的旧条、只留最新 :data:`CAP` 条（防腐 #4：出生即有界）。
- 投影 ``recaps[].generate_request``（add-only；无请求 / 过了 TTL = null）：
  ``{"requested_at", "state": running|done|noop|lost, "note"}``。``done`` ⇔ recap 文件的
  ``generated_at`` ≥ ``requested_at``（子进程落笔了——版本 +1 一定伴随新 generated_at；
  模型输出两次解析不出 JSON 落成 generation_failed 也算落笔）；``running`` = 起了还没落笔；
  ``lost`` = 超过 :data:`LOST_AFTER_S` 仍无落笔（崩在 import / 被杀 / 锁等超时 / **模型调用
  本身出错**——claude 非零退出或超时时 ``act.recap.generate`` 不捕获、整次运行 crash 不落笔，
  原因只在 state/recap.log——诚实说丢了，不永远「生成中」）；``noop`` = 没起。
  纯磁盘真值函数：dashboard 一次性构建同样算得出。

actd 在 **起子进程之前** 取 ``requested_at``（:func:`iso_now`），子进程的 ``generated_at``
取自它自己的启动时刻，同一台机器同一口钟、同为秒级 ISO-Z，字典序 = 时间序。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Optional

from act.lib import config

REQUESTS_PATH: Path = config.STATE_DIR / "recap_requests.json"
# 子进程起了却迟迟没落笔：一次成功的生成 = 锁等待（≤ LOCK_WAIT_S 120 s）+ 模型调用（≤ LLM_TIMEOUT_S
# 240 s × 重试一次）——10 分钟之外仍无新版本，按丢了处理（与 radar_rounds.LOST_AFTER_S 同款）。
# crash 掉的运行（模型非零退出 / 超时 / 未知 key）不落笔，也在这条线之后才显 lost。
LOST_AFTER_S = 10 * 60
# 台账上界：一条请求最多活 24 h（之后投影回 null）、最多 60 条（= recap_store.PROJECTION_CAP）
TTL_S = 24 * 3600
CAP = 60

RUNNING = "running"
NOOP = "noop"


def iso_now(now: Optional[_dt.datetime] = None) -> str:
    when = now if now is not None else _dt.datetime.now(_dt.timezone.utc)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value) -> Optional[_dt.datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def load() -> dict:
    """整份台账；缺失 / 坏文件 → {}。Never raises。"""
    try:
        data = json.loads(REQUESTS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 台账坏了不许崩 pass / 投影
        return {}
    return data if isinstance(data, dict) else {}


def _entry(rec) -> Optional[dict]:
    """一条形状合格的台账记录，否则 None。"""
    if not isinstance(rec, dict) or _parse_iso(rec.get("requested_at")) is None:
        return None
    return rec


def _age_s(requested_at: str, now: _dt.datetime) -> float:
    since = _parse_iso(requested_at)
    return (now - since).total_seconds() if since is not None else float("inf")


def _prune(data: dict, now: _dt.datetime) -> dict:
    """剪掉坏条 / 过 TTL 的条，按 requested_at 倒序只留 CAP 条。"""
    kept = {k: v for k, v in data.items()
            if isinstance(k, str) and _entry(v) is not None and _age_s(v["requested_at"], now) <= TTL_S}
    newest = sorted(kept.items(), key=lambda kv: kv[1]["requested_at"], reverse=True)[:CAP]
    return dict(newest)


def record(key: str, launch: str, requested_at: Optional[str] = None,
           now: Optional[_dt.datetime] = None) -> None:
    """actd 单写者：原子写一条（tmp + replace），顺手剪台账。写失败只影响投影，不反噬 pass。"""
    when = now if now is not None else _dt.datetime.now(_dt.timezone.utc)
    try:
        config.ensure_state_dirs()
        data = load()
        data[key] = {"requested_at": requested_at or iso_now(when), "launch": launch,
                     "note": None if launch == RUNNING else "launch_failed"}
        data = _prune(data, when)
        tmp = REQUESTS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, REQUESTS_PATH)
    except OSError:
        pass


def _state(rec: dict, generated_at, now: _dt.datetime) -> str:
    if rec.get("launch") != RUNNING:
        return NOOP
    requested_at = rec["requested_at"]
    # 同格式 ISO-Z 字串，字典序 = 时间序；子进程在请求之后落过笔就是跑完了
    if isinstance(generated_at, str) and generated_at >= requested_at:
        return "done"
    return "lost" if _age_s(requested_at, now) > LOST_AFTER_S else RUNNING


def _live_entry(key, data, now: _dt.datetime) -> Optional[dict]:
    """``key`` 的台账记录：无记录 / 坏条 / 过了 TTL → None。"""
    rec = _entry(data.get(key)) if isinstance(data, dict) else None
    if rec is None or _age_s(rec["requested_at"], now) > TTL_S:
        return None
    return rec


def _note(rec: dict) -> Optional[str]:
    note = rec.get("note")
    return note if isinstance(note, str) and note else None


def projection(key, generated_at, requests: Optional[dict] = None,
               now: Optional[_dt.datetime] = None) -> Optional[dict]:
    """行的 ``generate_request``：无请求记录 / 过了 TTL → None。

    ``requests`` = :func:`load` 的结果（调用方一次读、逐行传，投影 60 行不读 60 遍）。"""
    when = now if now is not None else _dt.datetime.now(_dt.timezone.utc)
    rec = _live_entry(key, load() if requests is None else requests, when)
    if rec is None:
        return None
    return {"requested_at": rec["requested_at"],
            "state": _state(rec, generated_at, when),
            "note": _note(rec)}
