"""演示视频的分镜真源（CONTRACT §77 拟，QA 面归 §58）。

`shots.json` 是唯一的分镜真源：storyboard.md / 旁白 / 字幕 / 录制 / 剪辑五处都从它派生，
没有第二份镜头清单（防腐第 9 条 命名单源）。本模块只做读取与时间线计算，不碰网络、不写文件。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_SHOTS = Path(__file__).resolve().parent / "shots.json"

MAX_SHOT_SECONDS = 15
MIN_TOTAL_SECONDS = 60
MAX_TOTAL_SECONDS = 180


def _check_shot(shot: Dict[str, Any]) -> None:
    """一个镜头的必填字段与时长上限。"""
    for key in ("id", "seconds", "zh", "en"):
        if not shot.get(key):
            raise ValueError(f"shots.json: shot missing {key!r}: {shot.get('id')!r}")
    seconds = shot["seconds"]
    if not isinstance(seconds, int) or seconds <= 0:
        raise ValueError(f"shots.json: {shot['id']}: seconds must be a positive int")
    if seconds > MAX_SHOT_SECONDS:
        raise ValueError(f"shots.json: {shot['id']}: {seconds}s > {MAX_SHOT_SECONDS}s cap")


def load(path: Path | str | None = None) -> Dict[str, Any]:
    """读 shots.json；结构不合法即抛（分镜坏了不许静默出片）。"""
    data = json.loads(Path(path or DEFAULT_SHOTS).read_text(encoding="utf-8"))
    shots = data.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("shots.json: 'shots' must be a non-empty list")
    for shot in shots:
        _check_shot(shot)
    return data


def total_seconds(shots: List[Dict[str, Any]]) -> int:
    return sum(int(s["seconds"]) for s in shots)


def timeline(shots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """每个镜头的 [start, end)（秒，整数累加——剪辑时每段都被 ffmpeg 裁到计划时长）。"""
    out: List[Dict[str, Any]] = []
    cursor = 0
    for shot in shots:
        span = int(shot["seconds"])
        out.append({"id": shot["id"], "start": cursor, "end": cursor + span, "shot": shot})
        cursor += span
    return out
