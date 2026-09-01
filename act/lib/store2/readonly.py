"""store2 只读小面 — server/board_source 的卡详情读取（CONTRACT §53 / §49）。

server 侧的 registry 纪律是「只读 + 永不落卡」（§44 单写者）；本模块把
「读一张卡的 payload」做成物理只读（sqlite URI ``mode=ro``），server 不必
import 带写路径的 store.py/registry.py。任何失败（库锁着/半态/坏 JSON）都
返回 None——读方降级，绝不崩 request（宪法 11）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional


def read_card(db_path: Path, card_id: str) -> Optional[dict]:
    """按 id 取卡的 canonical payload dict；tombstone/缺席/任何失败 = None。"""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "SELECT payload, tombstone FROM cards WHERE id = ?",
            (str(card_id),)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if row is None or row[1]:
        return None
    try:
        obj = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None
