"""server/folders.py — 设置页目录字段的「打开」/「创建」与词表 reveal（CONTRACT §68.1 目录字段；§49）：
``POST /api/folders/open {key}`` · ``POST /api/folders/create {key}`` · ``POST /api/reveal {target, mode?}``。

原生 Settings.swift 的 obsidianGroup（选择… / 打开 / 创建）的 server 落点。「选择…」是文件对话框——壳里走 §61.1 桥的 ``chooseFolder``（NSOpenPanel），
浏览器里退化成路径文本框，server 不参与；「打开」与「创建」作用在**已保存的 effective 值**上：
路径由 server 从设置目录读（``settings_catalog.effective``），**绝不接受客户端原始路径**
（reveal / ai-fix / terminal 同一纪律）——web 在草稿未保存时禁用这两颗按钮并提示先保存。

- ``key`` ∈ :data:`FOLDER_FIELDS`（``obsidian_raw``；未知 400。v2.0.0：``default_target_repo`` 随
  executor 退役）。
- open：值为空 400；darwin → ``/usr/bin/open <dir>``（访达），其它 501。
  ``obsidian_raw`` 开的是 **vault 根**（= raw 目录的父目录；原生 Settings.swift:768 ``openInFinder(vaultRoot)``，
  §68.1 追记「vault 根」——web 框里显示的就是根，打开落到同一处）；create 仍 ``mkdir -p`` raw 目录本身（含根）。
  落点**不是目录**（还没建 / 被挪走）→ 打开**最近的既有祖先目录**并回 add-only ``opened: <祖先>, missing: true``
  （§68.4 追记；原生 Pages.swift ``.reveal``：``fileExists ? p : deletingLastPathComponent``——用户至少能看见它
  本该在哪、顺手建出来）；**相对路径**（字段没有绝对路径校验、config.yaml 也能存）没有可开的祖先
  （``parents`` 尽头是 ``.`` = server 的 cwd，不是用户的树）→ 仍 404，绝不 ``open .``。
- create：``mkdir -p``（已在 = ``created:false`` 幂等）；回执的 add-only 键 ``git_init`` 恒为 null
  （v2.0.0：唯一要 ``git init`` 的键 ``default_target_repo`` 随 executor 退役，runner 一并删除）；mkdir 失败 →
  500 ``could not create the folder: <why>``（web 前缀原生句「创建目录失败：」）。
- reveal（v2.0.0 从退役的 server/files.py 搬来，只留词表半边）：``target`` ∈ :data:`REVEAL_TARGETS`
  ——``config``（``config.yaml``，缺席则模板；doctor ``config_invalid`` 行的「显示文件」）与
  ``voice_profile``（recap 读的语气档案：``state/voice-profile.md``，否则 ``config/voice-profile.default.md``；
  ``mode:"open"`` = 默认编辑器打开）。路径永远由 server 推导，客户端只点名词表项；非 darwin 501。
  交付物 reveal（``card_id``）随卡片退役。
目录字段的 ``path_exists`` 投影在 settings_catalog（web 据此渲染「目录不存在」警告 + 创建键）。
``opener`` / ``runner`` 注入缝：测试绝不真 open / 真 git。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from server import settings_catalog
from server.errors import (ApiError, InvalidFieldError, NotFoundError,
                           NotImplementedError501, UnknownFieldError)

# key → (section id, git init on create)
FOLDER_FIELDS = {
    "obsidian_raw": ("obsidian", False),
}
# 「打开」落到 vault 根而不是存值本身的那一把键（web `vaultPaths.VAULT_RAW_KEY` 同名）
VAULT_RAW_KEY = "obsidian_raw"

Opener = Callable[[Path], None]
Runner = Callable[[list], int]


def _default_opener(path: Path) -> None:
    subprocess.run(["/usr/bin/open", str(path)], check=False, timeout=20)


def _key_of(payload: dict) -> str:
    unknown = set(payload) - {"key"}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    key = payload.get("key")
    if key not in FOLDER_FIELDS:
        raise InvalidFieldError("key must be one of %s" % ", ".join(sorted(FOLDER_FIELDS)),
                                {"field": "key"})
    return key


def resolve(home: Path, key: str) -> Path:
    """已保存的 effective 值 → 展开 ``~`` 的绝对路径；空值 400（原生：空路径按钮无事发生）。"""
    section = settings_catalog.lookup(FOLDER_FIELDS[key][0])
    field = settings_catalog.field_index(section)[key]
    value, _src = settings_catalog.effective(field, settings_catalog.read_overrides(home),
                                            settings_catalog.load_config_doc(home))
    raw = value.strip() if isinstance(value, str) else ""
    if not raw:
        raise InvalidFieldError("path is not set - save a path first", {"field": key})
    return Path(raw).expanduser()


def open_target(key: str, path: Path) -> Path:
    """「打开」的落点：``obsidian_raw`` 开 vault 根（raw 的父目录，原生 ``openInFinder(vaultRoot)`` = ``loadVault`` 的
    ``deletingLastPathComponent``，不管叶子叫什么）；其它键开存值本身。raw 没有目录部分（相对的一段名）就没有根可开 → 原样。"""
    if key != VAULT_RAW_KEY:
        return path
    parent = path.parent
    return path if str(parent) == "." else parent


def nearest_existing_ancestor(path: Path) -> Optional[Path]:
    """``path`` 不是目录时往上找第一个还在的目录（原生 ``deletingLastPathComponent`` 的多级版：
    ``~/Documents/Vault`` 整棵没建时开到 ``~/Documents``，不是开一个同样不存在的父目录）。
    相对路径没有可开的祖先 → None：``Path("a/b").parents`` 的尽头是 ``.`` = server 进程的 cwd（launchd 下是 repo 根），
    与用户存的值毫无关系——开它只会让访达亮出一个不相干的目录，还回执「已打开上级目录」。"""
    if not path.is_absolute():
        return None
    for parent in path.parents:
        if parent.is_dir():
            return parent
    return None


def _existing_or_ancestor(key: str, path: Path) -> Path:
    """实际要开的目录：``path`` 自己在就是它；不在 → 最近的既有祖先；连祖先都没有 → 404。"""
    if path.is_dir():
        return path
    ancestor = nearest_existing_ancestor(path)
    if ancestor is None:
        raise NotFoundError("folder does not exist", {"key": key, "path": str(path)})
    return ancestor


def open_folder(home: Path, payload: dict, opener: Optional[Opener] = None,
                platform: Optional[str] = None) -> dict:
    """``POST /api/folders/open {key}`` → ``{"ok": true, "key", "path": "<dir>"}``（``obsidian_raw`` 的 path = vault 根）；
    落点不在 → ``+ {"opened": "<最近的既有祖先>", "missing": true}``（add-only；在的时候两键不出现，老客户端零改动）。"""
    key = _key_of(payload)
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("opening a folder in Finder is macOS only")
    path = open_target(key, resolve(home, key))
    target = _existing_or_ancestor(key, path)
    (opener or _default_opener)(target)
    receipt = {"ok": True, "key": key, "path": str(path)}
    if target != path:
        receipt.update({"opened": str(target), "missing": True})
    return receipt


def create_folder(home: Path, payload: dict, runner: Optional[Runner] = None) -> dict:
    """``POST /api/folders/create {key}`` → ``{"ok", "key", "path", "created", "git_init"}``。"""
    key = _key_of(payload)
    path = resolve(home, key)
    existed = path.is_dir()
    if not existed:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ApiError("could not create the folder: %s" % exc, {"key": key, "path": str(path)})
    del runner   # v2.0.0: no folder key asks for `git init` any more (add-only receipt key stays, null)
    return {"ok": True, "key": key, "path": str(path), "created": not existed, "git_init": None}


# --------------------------------------------------------------------------- #
# POST /api/reveal {target, mode?} — vocabulary items only, path derived here
# --------------------------------------------------------------------------- #
REVEAL_TARGETS = ("config", "voice_profile")
REVEAL_MODES = ("reveal", "open")
_OPENABLE_TARGETS = frozenset({"voice_profile"})


def _config_file(home: Path) -> Path:
    for path in (home / "config.yaml", home / "config.example.yaml"):
        if path.is_file():
            return path
    raise NotFoundError("neither config.yaml nor config.example.yaml exists", {"target": "config"})


def _voice_profile_file(home: Path) -> Path:
    # mirrors act/recap.voice_profile_text's two-level fallback (docs/VOICE.md)
    for path in (home / "state" / "voice-profile.md", home / "config" / "voice-profile.default.md"):
        if path.is_file():
            return path
    raise NotFoundError("no voice profile exists yet (state/voice-profile.md or "
                        "config/voice-profile.default.md)", {"target": "voice_profile"})


_REVEAL_PATHS = {"config": _config_file, "voice_profile": _voice_profile_file}


def _reveal_mode(mode, target: str) -> str:
    if mode is None:
        return "reveal"
    if not isinstance(mode, str) or mode not in REVEAL_MODES:
        raise InvalidFieldError("unknown reveal mode", {"field": "mode", "choices": list(REVEAL_MODES)})
    if mode == "open" and target not in _OPENABLE_TARGETS:
        raise InvalidFieldError('mode "open" is only honoured for target voice_profile',
                                {"field": "mode", "target": target})
    return mode


def _reveal_request(payload: dict) -> "tuple[str, str]":
    """Validated ``(target, mode)`` of a POST /api/reveal body."""
    unknown = set(payload) - {"target", "mode"}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    target = payload.get("target")
    if target not in REVEAL_TARGETS:
        raise InvalidFieldError("unknown reveal target",
                                {"field": "target", "choices": list(REVEAL_TARGETS)})
    return target, _reveal_mode(payload.get("mode"), target)


def _default_revealer(argv: list) -> None:
    subprocess.run(argv, check=False, timeout=10)


def reveal(home: Path, payload: dict, revealer: Optional[Callable[[list], None]] = None,
           platform: Optional[str] = None) -> dict:
    """``POST /api/reveal {target, mode?}`` → ``{"ok": true, "revealed"|"opened": "<path>"}``."""
    target, mode = _reveal_request(payload)
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("reveal is only available on macOS")
    path = _REVEAL_PATHS[target](home)
    _run_reveal(revealer or _default_revealer, mode, path, target)
    return {"ok": True, ("opened" if mode == "open" else "revealed"): str(path)}


def _run_reveal(run: Callable[[list], None], mode: str, path: Path, target: str) -> None:
    """``open -R <path>`` (Finder) or ``open <path>`` (default editor); failure → 404."""
    argv = ["open", str(path)] if mode == "open" else ["open", "-R", str(path)]
    try:
        run(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NotFoundError("could not " + mode, {"target": target, "reason": str(exc)})
