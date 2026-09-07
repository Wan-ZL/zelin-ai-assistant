"""server/telemetry_consent.py — consent-surface 标记的 web 写入口：``POST /api/telemetry/consent-shown {}``
（CONTRACT §15 consent 门 / §49 路由；owner 决策 D49）。

原生 `mac/Sources/Permissions.swift` ``TelemetryConsent.markSurfaceShown`` 在披露块进入视口时写两个标记文件；
web 移植后没人写它们，于是 `act.lib.telemetry_upload.consent_surfaced()` 在从未碰过 telemetry 开关的新装机上
永远为假、每小时的 sync 永远 no-op（审计 diagnostics-setup-telemetry-consent-marker）。自此 web 宿主在两个
**显式动作**上请求 server 落笔（D49 选项 b，取代 IntersectionObserver 可见性探针）：向导「完成」（第 3 步渲染
披露块）与 `TelemetryBlock` 的复选框保存成功后（复选框就在披露块里，保存 = 块在屏上）；永不在挂载时写。

写什么：v1 ``state/telemetry_consent_shown``（上传端的 consent 门看它在不在）+ v2 ``state/telemetry_consent_shown_v2``
（仅「披露展示过」的记录，v0.48 起不开内容门）。两个文件都 **write-once**：内容 = 首次展示的 UTC 时间戳一行
（原生同形），已存在原样不动、不改 mtime；tmp + os.replace 原子落盘。路径由 server/paths.py 镜像
（`telemetry_consent_marker_path` / `telemetry_consent_v2_path`，判例钉与 act 常量同名）。body 必须是 ``{}``
（未知字段 400 UNKNOWN_FIELD，与其它写面同一零容忍）。四闸在 server/app.py `_check_auth`。
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from server import paths
from server.errors import UnknownFieldError


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_once(path: Path, stamp: str) -> bool:
    """标记不存在才写（内容 = 时间戳一行，原子 rename）；已存在返回 False、文件零改动。"""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(stamp + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return True


def _first_shown_at(path: Path, fallback: str) -> str:
    """v1 标记里记的首次展示时刻（读不出 / 空文件 → 本次时间戳）。"""
    try:
        return path.read_text(encoding="utf-8").strip() or fallback
    except OSError:
        return fallback


def mark_shown(home: Path, payload: dict) -> dict:
    """``POST /api/telemetry/consent-shown {}`` → ``{ok, written, shown_at}``。

    ``written`` = 本次真的新写了 v1 标记（第二次起 False，幂等）；``shown_at`` = v1 标记里的首次展示时刻。
    v2 标记跟着 v1 一起补（老安装可能只有 v1——原生 v0.13–v0.17 只写 v1）。"""
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    stamp = _iso_now()
    v1 = paths.telemetry_consent_marker_path(home)
    written = _write_once(v1, stamp)
    _write_once(paths.telemetry_consent_v2_path(home), stamp)
    return {"ok": True, "written": written, "shown_at": _first_shown_at(v1, stamp)}
