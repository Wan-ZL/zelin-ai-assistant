"""fold_receipts — 静默并入的看板回执台账（CONTRACT §44.6）.

2026-08-07 拍板：radar / 普通 capture 通道的静默并入（fold）保留，但并入发生时
看板必须给「已并入 R-xxx」回执——不能再有"卡片转圈后消失、文本不知去向"的
黑洞体验（8-07 事故：两条 [run] 输入被静默并入正在执行的卡，看板零回执）。

机制（复用 §28 notify_queue 的 one-file-per-entry 形制，天然免并发写竞态——
radar cron 与 actd 都可能是 fold 的执行者）：

- fold 执行点调用 :func:`record` → ``state/fold_receipts/<uuid>.json`` 原子落
  一条 ``{"id","req","title","channel","text","at"}``；写入前顺手清扫过期
  兄弟条目（同 notify 的 stale sweep，目录不会无界增长）。
- dashboard 投影（actd 单写者）经 :func:`load_recent` 读回 TTL 内的条目，
  作为 add-only 顶层键 ``fold_receipts`` 发给 App；Swift 端渲染为一行可消失
  的 info 提示（LocalNotice 机制复用）。
- 回执是尽力而为的观测面：record/load 任何失败都绝不打断 fold 本身
  （宪法第 11 条——失败不外溢）。

[run] 通道（capture mode:"run"）与本台账无关：§34 修订后它彻底不做判重并入，
一律新卡直接开跑——新卡出现在运行中列本身就是回执。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

# 回执存活窗口：App 5s 刷新一次、通知 10 分钟 stale（§28 同款）——足够用户
# 看到一次；过期即从投影消失（"短暂 notice"的产品语义）。
TTL_S = 600.0
# 投影上限：防御性 cap（正常一个窗口内不会有 10 条 fold）。
PROJECTION_CAP = 10


def _dir() -> Path:
    from act.lib import config
    return Path(config.FOLD_RECEIPTS_DIR)


def record(req_id: str, title: str, channel: str, text: str = "",
           now: Optional[float] = None) -> Optional[Path]:
    """落一条并入回执（原子写 .tmp→rename + 过期清扫）。绝不 raise。

    ``req_id``/``title`` = 并入目标（主卡）；``channel`` = 触发并入的入库
    通道（quick_capture / quick / radar）；``text`` = 被并入内容的摘要
    （capture 原话 / fold note），投影侧截 120 字。
    """
    try:
        qdir = _dir()
        qdir.mkdir(parents=True, exist_ok=True)
        _sweep_stale(qdir, now=now)
        rid = uuid.uuid4().hex
        entry = {
            "id": rid,
            "req": str(req_id or ""),
            "title": " ".join(str(title or "").split())[:80],
            "channel": str(channel or ""),
            "text": " ".join(str(text or "").split())[:120],
            "at": int(now if now is not None else time.time()),
        }
        target = qdir / (rid + ".json")
        tmp = qdir / (rid + ".json.tmp")
        try:
            tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)   # 同 notify：rename 失败不留尸体
        return target
    except Exception:  # noqa: BLE001 - 回执绝不打断 fold 本身（宪法 11）
        return None


def load_recent(now: Optional[float] = None) -> list[dict]:
    """TTL 内的回执，按 ``at`` 降序、cap :data:`PROJECTION_CAP`。绝不 raise。

    坏文件/坏形状直接跳过（dashboard 投影的"损坏条目跳过"既有约定）。
    """
    out: list[dict] = []
    cutoff = (now if now is not None else time.time()) - TTL_S
    try:
        paths = list(_dir().glob("*.json"))
    except OSError:
        return out
    for p in paths:
        try:
            entry = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        try:
            at = int(entry.get("at") or 0)
        except (TypeError, ValueError):
            continue
        if at < cutoff or not str(entry.get("req") or ""):
            continue
        out.append({
            "id": str(entry.get("id") or p.stem),
            "req": str(entry.get("req")),
            "title": str(entry.get("title") or ""),
            "channel": str(entry.get("channel") or ""),
            "text": str(entry.get("text") or ""),
            "at": at,
        })
    out.sort(key=lambda e: e["at"], reverse=True)
    return out[:PROJECTION_CAP]


def _sweep_stale(qdir: Path, now: Optional[float] = None) -> int:
    """清掉过期条目（mtime 超 TTL）。best-effort，绝不 raise。"""
    removed = 0
    cutoff = (now if now is not None else time.time()) - TTL_S
    try:
        for f in qdir.iterdir():
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed
