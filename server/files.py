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

词表 reveal（``POST /api/reveal {target, name?, mode?}``，客户端只点名词表项、路径由 server 推导）：
``config``（§68.4）/ ``skill``（§67.5）/ ``voice_profile``（§68.1 追记，``mode:"open"`` = 默认编辑器打开）/
``mcp_user`` / ``mcp_project``（§68.9 追记）。
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


def _open_reveal(target: Path, ident: dict, mode: str = "reveal") -> dict:
    """``reveal`` = ``open -R``（访达定位，原生 activateFileViewerSelecting）；``open`` = 裸 ``open``
    （系统默认处理者打开文件，原生 NSWorkspace.open）。回执键随 mode：``revealed`` / ``opened``（add-only）。"""
    verb = "open" if mode == "open" else "reveal"
    argv = ["open", str(target)] if mode == "open" else ["open", "-R", str(target)]
    try:
        subprocess.run(argv, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # 报错动词随 mode：页面把 message 原样显示，编辑器打不开不该念成「could not reveal」
        raise NotFoundError("could not " + verb, dict(ident, reason=str(exc)))
    return {"ok": True, ("opened" if mode == "open" else "revealed"): str(target)}


# POST /api/reveal 的 add-only ``mode``（缺省 reveal）。``open`` 只对 ``voice_profile`` 放行——原生「打开档案」
# 是 NSWorkspace.open（在默认编辑器里打开 .md），其余词表项与交付物都是访达定位（原生 reveal 语义）。
REVEAL_MODES = ("reveal", "open")
_OPENABLE_TARGETS = frozenset({"voice_profile"})


def resolve_mode(mode: object, target: Optional[str]) -> str:
    """缺省 → ``reveal``；词表外 400；``open`` 用在 voice_profile 之外（含交付物 reveal，target=None）→ 400。"""
    if mode is None:
        return "reveal"
    if not isinstance(mode, str) or mode not in REVEAL_MODES:
        raise InvalidFieldError("unknown reveal mode", {"field": "mode", "choices": list(REVEAL_MODES)})
    if mode == "open" and target not in _OPENABLE_TARGETS:
        raise InvalidFieldError('mode "open" is only honoured for target voice_profile',
                                {"field": "mode", "target": target, "openable": sorted(_OPENABLE_TARGETS)})
    return mode


def reveal(home: Path, card_id: str, mode: object = None) -> dict:
    """POST /api/reveal {card_id} → ``open -R``（访达定位，分享=拖拽起点）。
    定位目标 = 最新交付物文件；目录空则定位目录本身。非 darwin → 501。``mode`` 只认 ``reveal``。"""
    _validate_card_id(card_id)
    resolve_mode(mode, None)
    if sys.platform != "darwin":
        raise NotImplementedError501("reveal is only available on macOS")
    base = deliverables_dir(home, card_id)
    target = _newest_deliverable(base)
    if target is None:
        if not base.is_dir():
            raise NotFoundError("no deliverables for this card", {"id": card_id})
        target = base
    return _open_reveal(target, {"id": card_id})


# 客户端只能点名一个词表项，路径仍由 server 推导（同一条「绝不接受客户端路径」红线）。
# ``skill`` 另带 add-only ``name``（清单里的 skill 名，仍不是路径）——§67.5 Skills 区「在 Finder 显示」；
# ``voice_profile`` = 语气档案区「打开档案」：此刻生效（或重开后会生效）的档案文件（server/voice_profile）。
# ``mcp_user`` / ``mcp_project`` = MCP 区每个作用域的「在 Finder 显示」：``~/.claude.json`` / ``<home>/.mcp.json``
# （server/mcp_servers.scope_paths 的同一处计算；文件不在 → 404，页面据 ``exists`` 先把按钮禁掉）。§68.9 追记。
REVEAL_TARGETS = ("config", "skill", "voice_profile", "mcp_user", "mcp_project")


def reveal_target(home: Path, target: str, name: Optional[str] = None, mode: object = None) -> dict:
    """POST /api/reveal {target:"config"} → 访达定位 ``config.yaml``（缺席则模板
    ``config.example.yaml``）——原生 FailureCatalog ``config_invalid`` 的「显示文件」。
    {target:"skill", name} → 定位该 skill 的 ``SKILL.md``（原生 SettingsSkills.reveal：选中要编辑的
    那个文件）：本机已链 / 已拷的副本优先，否则仓库里的商店原件；清单里没有这个名字 → 404。
    {target:"voice_profile"} → 此刻生效（或重开后会生效）的语气档案（server/voice_profile）；都不在 → 404；
    带 ``mode:"open"`` 时在默认编辑器里打开它（原生「打开档案」= NSWorkspace.open）而不是访达定位。
    {target:"mcp_user"|"mcp_project"} → 访达定位该作用域的 MCP 配置文件（原生 SettingsMCP.reveal）；不在 → 404。"""
    if target not in REVEAL_TARGETS:
        raise InvalidFieldError("unknown reveal target", {"field": "target", "choices": list(REVEAL_TARGETS)})
    resolved = resolve_mode(mode, target)
    if sys.platform != "darwin":
        raise NotImplementedError501("reveal is only available on macOS")
    ident = {"target": target} if target != "skill" else {"target": target, "name": name}
    return _open_reveal(_REVEAL_PATHS[target](home, name), ident, resolved)


def _config_file(home: Path, _name: Optional[str]) -> Path:
    path = home / "config.yaml"
    if not path.is_file():
        path = home / "config.example.yaml"
    if not path.is_file():
        raise NotFoundError("neither config.yaml nor config.example.yaml exists", {"target": "config"})
    return path


def _voice_profile_file(home: Path, _name: Optional[str]) -> Path:
    from server import voice_profile
    path = voice_profile.effective_path(home)
    if path is None:
        raise NotFoundError("no voice profile exists yet (state/voice-profile.md or config/voice-profile.default.md)",
                            {"target": "voice_profile"})
    return path


def _mcp_scope_file(scope: str):
    """``mcp_user`` / ``mcp_project`` → 该作用域的配置文件（路径算法只在 mcp_servers.scope_paths 一处）；不在 → 404。"""
    def resolve(home: Path, _name: Optional[str]) -> Path:
        from server import mcp_servers
        path = mcp_servers.scope_paths(home)[scope]
        if not path.is_file():
            raise NotFoundError("no MCP config file in this scope yet", {"target": "mcp_" + scope, "scope": scope})
        return path
    return resolve


def _skill_row(home: Path, name: Optional[str]) -> dict:
    """skill 名 → 清单行（名字不是非空字串 400、清单里没有 404）。"""
    if not isinstance(name, str) or not name.strip():
        raise InvalidFieldError("name must be a non-empty string", {"field": "name"})
    from server import settings  # 惰性：settings 不 import files，避免环
    rows = {row["name"]: row for row in settings.skills_snapshot(home)["skills"]}
    row = rows.get(name.strip())
    if row is None:
        raise NotFoundError("no such skill", {"name": name.strip()[:100]})
    return row


def _skill_file(home: Path, name: Optional[str]) -> Path:
    """要在访达里选中的 SKILL.md：副本 ``row.path`` 优先，再商店原件 ``row.target``；都缺 404。"""
    row = _skill_row(home, name)
    for base in (row.get("path"), row.get("target")):
        if base and (Path(base) / "SKILL.md").is_file():
            return Path(base) / "SKILL.md"
    raise NotFoundError("SKILL.md not found for this skill", {"name": str(name).strip()})


_REVEAL_PATHS = {"config": _config_file, "skill": _skill_file, "voice_profile": _voice_profile_file,
                 "mcp_user": _mcp_scope_file("user"), "mcp_project": _mcp_scope_file("project")}
