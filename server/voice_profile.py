"""server/voice_profile.py — 语气档案区的状态行（docs/VOICE.md；CONTRACT §68.1 追记 / §49）：``GET /api/voice``。

原生 Settings.swift voiceGroup 的「当前生效」一行（voiceStatusText / voiceEffectivePath）搬到 server：
两个候选文件与 act/lib/dispatch_prompt 的解析顺序**同一张表**——

  1. ``<home>/state/voice-profile.md``            私有档案（真实说话样本 = 工作数据，永不进 git）
  2. ``<home>/config/voice-profile.default.md``   出厂默认（作者风格，随仓库）

``enabled`` = 设置目录 ``voice`` 区 ``voice_enabled`` 的 effective（override → config.yaml → default）。
``effective_path`` = 执行者此刻会注入的文件；关掉时 = 重开后**会**生效的那个（原生「打开档案」开的就是它）；
两个都不在 → null。状态词四选一由页面按 (enabled, private_exists, default_exists) 组（原生同表）。
``POST /api/reveal {target:"voice_profile"}`` 定位它（server/files.REVEAL_TARGETS；缺席 404）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from server import settings_catalog


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
