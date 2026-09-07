"""server/voice_profile.py — 语气档案区的状态行（docs/VOICE.md；CONTRACT §68.1 追记 / §49）：``GET /api/voice``
+ 「从我的消息生成/更新档案」的回执 ``GET /api/voice/generate-status``（D47）。

原生 Settings.swift voiceGroup 的「当前生效」一行（voiceStatusText / voiceEffectivePath）搬到 server：
两个候选文件与 act/lib/dispatch_prompt 的解析顺序**同一张表**——

  1. ``<home>/state/voice-profile.md``            私有档案（真实说话样本 = 工作数据，永不进 git）
  2. ``<home>/config/voice-profile.default.md``   出厂默认（作者风格，随仓库）

``enabled`` = 设置目录 ``voice`` 区 ``voice_enabled`` 的 effective（override → config.yaml → default）。
``effective_path`` = 执行者此刻会注入的文件；关掉时 = 重开后**会**生效的那个（原生「打开档案」开的就是它）；
两个都不在 → null。状态词四选一由页面按 (enabled, private_exists, default_exists) 组（原生同表）。
``POST /api/reveal {target:"voice_profile"}`` 定位它（server/files.REVEAL_TARGETS；缺席 404）。

「从我的消息生成/更新档案」（原生 runVoiceGen：几分钟的 ``act.voice_gen``）在 web 是 inbox 特形
``voice_generate`` → actd 分离起 ``act.voice_gen --job``（§44 单写者精神：server 不起子进程、不写台账）。
本模块只**读** ``state/voice_gen/job.json``（act/lib/voice_job 的两个写者：actd 写 running、子进程写
done / failed）并投影 ``{job: null | {status, started_at, finished_at, error, message, profile_path, lost}}``：
``lost`` = running 却超过 :data:`LOST_AFTER_S` 没回执（子进程崩在 import / 被杀——诚实说丢了，
不永远「生成中」）。坏文件 / 词表外的 status → ``job: null``，永不 500。
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Optional

from server import paths, settings_catalog

# 镜像 act/lib/voice_job（server 绝不 import act；tests/test_server_voice_generate_status.py 钉漂移）
JOB_STATUSES: tuple = ("running", "done", "failed")
LOST_AFTER_S = 15 * 60
_JOB_KEYS: tuple = ("status", "started_at", "finished_at", "error", "message", "profile_path")


def private_path(home: Path) -> Path:
    return home / "state" / "voice-profile.md"


def default_path(home: Path) -> Path:
    return home / "config" / "voice-profile.default.md"


def effective_path(home: Path) -> Optional[Path]:
    """私有档案 > 出厂默认 > None（与 dispatch_prompt 的两级回退同序，不看开关）。"""
    for candidate in (private_path(home), default_path(home)):
        if candidate.is_file():
            return candidate
    return None


def snapshot(home: Path) -> dict:
    """``GET /api/voice`` → ``{enabled, private_path, private_exists, default_path, default_exists, effective_path}``。"""
    effective = effective_path(home)
    return {
        "enabled": settings_catalog.effective_value(home, "voice", "voice_enabled") is not False,
        "private_path": str(private_path(home)),
        "private_exists": private_path(home).is_file(),
        "default_path": str(default_path(home)),
        "default_exists": default_path(home).is_file(),
        "effective_path": str(effective) if effective else None,
    }


def _parse_iso(value) -> Optional[_dt.datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def _opt_str(value) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def load_job(home: Path) -> Optional[dict]:
    """最近一次 job 的原始记录；缺失 / 坏文件 / 词表外 status → None。Never raises。"""
    try:
        data = json.loads(paths.voice_gen_job_path(home).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 台账坏了不许 500
        return None
    if not isinstance(data, dict) or data.get("status") not in JOB_STATUSES:
        return None
    return data


def is_lost(job: dict, now: Optional[_dt.datetime] = None) -> bool:
    """running 却超过 LOST_AFTER_S 没回执（或连 started_at 都读不出）。"""
    if job.get("status") != "running":
        return False
    since = _parse_iso(job.get("started_at"))
    if since is None:
        return True
    when = now if now is not None else _dt.datetime.now(_dt.timezone.utc)
    return (when - since).total_seconds() > LOST_AFTER_S


def generate_status(home: Path, now: Optional[_dt.datetime] = None) -> dict:
    """``GET /api/voice/generate-status`` → ``{"job": null | {…六键逐字消毒成 str|null, "lost": bool}}``。"""
    raw = load_job(home)
    if raw is None:
        return {"job": None}
    job = {key: _opt_str(raw.get(key)) for key in _JOB_KEYS}
    job["status"] = raw["status"]
    job["lost"] = is_lost(raw, now)
    return {"job": job}
