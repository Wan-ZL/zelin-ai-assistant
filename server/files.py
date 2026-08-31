"""交付物静态服务 + 访达定位（BUILD-CONTRACT §2.1 files/reveal）。

安全红线（抄 dashi 校验纪律）：
- 路径一律由 server 端从卡片记录推导，**绝不接受客户端原始路径**；
- 客户端只提供 card_id + 文件名（纯 basename）；目录穿越 / NUL / 分隔符 /
  点号伪装全拒，最后再加 realpath 包含性双保险。

交付物根目录推导（CONTRACT §33：文件型交付物写入 workbench 下
``deliverables/`` 的绝对路径文件）：
    card 的 target_repo（registry 增补后投影行也可能带 cwd）→ 展开 ``~`` →
    ``<root>/deliverables/``
// TODO(contract): §33 只在散文里约定了 deliverables/ 落点，没有把「交付物
// 目录」钉成结构化字段；这里选最保守的 target_repo/cwd 推导。若日后卡片记录
// 新增显式交付物清单字段，应改读该字段。
"""
from __future__ import annotations

import mimetypes
import subprocess
import sys
from pathlib import Path
from typing import Optional

from server.board_source import SAFE_ID_RE, card_detail
from server.errors import (InvalidFieldError, NotFoundError,
                           NotImplementedError501)

_NAME_MAX = 255


def _validate_name(name: str) -> None:
    """交付物文件名 = 纯 basename。拒绝：空 / 超长 / NUL / 任何路径分隔符 /
    ``.``、``..`` 与一切点号开头（dotfile 永不外发）。"""
    if (not name or len(name) > _NAME_MAX or "\x00" in name
            or "/" in name or "\\" in name or name.startswith(".")):
        raise InvalidFieldError("invalid deliverable name", {"name": name})


def _validate_card_id(card_id: str) -> None:
    if not SAFE_ID_RE.match(card_id or ""):
        raise InvalidFieldError("invalid card id", {"id": card_id})


def deliverables_dir(home: Path, card_id: str) -> Path:
    """server 端推导交付物目录（见模块注释）；卡不存在 → 404 直接抛。"""
    detail = card_detail(home, card_id)
    root = detail.get("target_repo") or detail.get("cwd")
    if not isinstance(root, str) or not root.strip():
        raise NotFoundError("card has no deliverable root",
                            {"id": card_id})
    return Path(root).expanduser() / "deliverables"


def serve_deliverable(home: Path, card_id: str, name: str) -> "tuple[bytes, str]":
    """返回 (body, content_type)。找不到 / 越界 一律 404（不泄露目录结构）。"""
    _validate_card_id(card_id)
    _validate_name(name)
    base = deliverables_dir(home, card_id)
    try:
        real_base = base.resolve(strict=True)
        target = (base / name).resolve(strict=True)
    except OSError:
        raise NotFoundError("deliverable not found", {"id": card_id, "name": name})
    # realpath 包含性双保险：symlink 把文件指出 deliverables/ 也照拒
    if target.parent != real_base or not target.is_file():
        raise NotFoundError("deliverable not found", {"id": card_id, "name": name})
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    try:
        return target.read_bytes(), ctype
    except OSError:
        raise NotFoundError("deliverable not found", {"id": card_id, "name": name})


def _newest_deliverable(base: Path) -> Optional[Path]:
    try:
        files = [p for p in base.iterdir()
                 if p.is_file() and not p.name.startswith(".")]
    except OSError:
        return None
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def reveal(home: Path, card_id: str) -> dict:
    """POST /api/reveal {card_id} → ``open -R``（访达定位，分享=拖拽起点）。
    定位目标 = 最新交付物文件；目录空则定位目录本身。非 darwin → 501。"""
    _validate_card_id(card_id)
    if sys.platform != "darwin":
        raise NotImplementedError501("reveal is only available on macOS")
    base = deliverables_dir(home, card_id)
    target = _newest_deliverable(base)
    if target is None:
        if not base.is_dir():
            raise NotFoundError("no deliverables for this card", {"id": card_id})
        target = base
    try:
        subprocess.run(["open", "-R", str(target)], check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NotFoundError("could not reveal deliverable",
                            {"id": card_id, "reason": str(exc)})
    return {"ok": True, "revealed": str(target)}
