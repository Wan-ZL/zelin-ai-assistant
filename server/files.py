"""交付物静态服务 + 访达定位（BUILD-CONTRACT §2.1 files/reveal）。

安全红线（抄 dashi 校验纪律）：
- 路径一律由 server 端从卡片记录推导，**绝不接受客户端原始路径**；
- 客户端只提供 card_id + 文件名（纯 basename）；目录穿越 / NUL / 分隔符 /
  点号伪装全拒，最后再加 realpath 包含性双保险。

交付物根目录推导（CONTRACT §33：文件型交付物写入 workbench 下
``deliverables/`` 的绝对路径文件）：
    card 的 target_repo（registry 增补后投影行也可能带 cwd）→ 展开 ``~`` →
    ``<root>/deliverables/``
// CONTRACT §53 T-19（v0.48 裁决）：结构化清单字段 ``execution.deliverables``
// 预留 add-only、随接线 PR 落法；在那之前本模块的「目录约定推导 + 穿越防护」
// 追认为过渡合法（§49）。字段落地后应改读该字段。
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

# 内嵌预览允许集——对齐 web DeliverableViewer 实际渲染面（html iframe、
# md/txt fetch 文本、位图 <img>）。svg 故意不在列：SVG 可携带脚本，一律
# attachment（<img> 子资源加载不受 disposition 影响，防的是直接导航执行）。
_INLINE_EXTS = frozenset({
    "html", "htm", "md", "markdown", "txt",
    "png", "jpg", "jpeg", "gif", "webp",
})


def _deliverable_headers(name: str) -> dict:
    """/files/deliverables 响应的安全头：同源交付物绝不裸发。

    - ``Content-Security-Policy: sandbox``：直接导航到交付物 URL 时文档落进
      opaque origin，拿不到 /api 同源面；html 额外 allow-scripts——与
      DeliverableViewer 的 ``<iframe sandbox="allow-scripts">`` 同一约束面，
      预览行为不变。
    - 非预览类型加 ``Content-Disposition: attachment``：浏览器只下载不渲染。
    """
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    csp = "sandbox allow-scripts" if ext in ("html", "htm") else "sandbox"
    headers = {"Content-Security-Policy": csp}
    if ext not in _INLINE_EXTS:
        headers["Content-Disposition"] = "attachment"
    return headers


# 文件名里永不放行的字节：NUL 与两种路径分隔符（穿越的原料）
_FORBIDDEN_NAME_CHARS = ("\x00", "/", "\\")


def _validate_name(name: str) -> None:
    """交付物文件名 = 纯 basename。拒绝：空 / 超长 / NUL / 任何路径分隔符 /
    ``.``、``..`` 与一切点号开头（dotfile 永不外发）。"""
    if not name or len(name) > _NAME_MAX or name.startswith("."):
        raise InvalidFieldError("invalid deliverable name", {"name": name})
    if any(ch in name for ch in _FORBIDDEN_NAME_CHARS):
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


def serve_deliverable(home: Path, card_id: str,
                      name: str) -> "tuple[bytes, str, dict]":
    """返回 (body, content_type, 安全响应头)。找不到 / 越界 一律 404
    （不泄露目录结构）。"""
    _validate_card_id(card_id)
    _validate_name(name)
    not_found = NotFoundError("deliverable not found", {"id": card_id, "name": name})
    target = _resolve_inside(deliverables_dir(home, card_id), name, not_found)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    try:
        return target.read_bytes(), ctype, _deliverable_headers(name)
    except OSError:
        raise not_found


def _resolve_inside(base: Path, name: str, not_found: NotFoundError) -> Path:
    """``base/name`` 的 realpath，且必须是 ``base`` 直系子文件——symlink 把
    文件指出 deliverables/ 也照拒（realpath 包含性双保险）。"""
    try:
        real_base = base.resolve(strict=True)
        target = (base / name).resolve(strict=True)
    except OSError:
        raise not_found
    if target.parent != real_base or not target.is_file():
        raise not_found
    return target


def _contained_file(p: Path, real_base: Path) -> bool:
    """非 dotfile、realpath 仍在 base 直系之下、且是普通文件。"""
    if p.name.startswith("."):
        return False
    try:
        real = p.resolve(strict=True)
    except OSError:
        return False
    return real.parent == real_base and real.is_file()


def _newest_deliverable(base: Path) -> Optional[Path]:
    """挑最新交付物；serve_deliverable 同款 realpath 包含性——symlink 把文件
    指出 deliverables/ 的一律跳过（reveal 绝不定位到目录外）。"""
    try:
        real_base = base.resolve(strict=True)
        files = [p for p in base.iterdir() if _contained_file(p, real_base)]
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
