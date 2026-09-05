"""server/folders.py — 设置页目录字段的「打开」/「创建」（CONTRACT §68.1 目录字段；§54.4）：
``POST /api/folders/open {key}`` · ``POST /api/folders/create {key}``。

原生 Settings.swift 的 obsidianGroup（选择… / 打开 / 创建）与 approvalGroup（选择… / 创建文件夹）
的 server 落点。「选择…」是文件对话框——壳里走 §61.1 桥的 ``chooseFolder``（NSOpenPanel），
浏览器里退化成路径文本框，server 不参与；「打开」与「创建」作用在**已保存的 effective 值**上：
路径由 server 从设置目录读（``settings_catalog.effective``），**绝不接受客户端原始路径**
（reveal / ai-fix / terminal 同一纪律）——web 在草稿未保存时禁用这两颗按钮并提示先保存。

- ``key`` ∈ :data:`FOLDER_FIELDS`（``obsidian_raw`` / ``default_target_repo``；未知 400）。
- open：值为空 400；darwin → ``/usr/bin/open <dir>``（访达），其它 501。
  ``obsidian_raw`` 开的是 **vault 根**（= raw 目录的父目录；原生 Settings.swift:768 ``openInFinder(vaultRoot)``，
  §68.1 追记「vault 根」——web 框里显示的就是根，打开落到同一处）；create 仍 ``mkdir -p`` raw 目录本身（含根）。
  落点**不是目录**（还没建 / 被挪走）→ 打开**最近的既有祖先目录**并回 add-only ``opened: <祖先>, missing: true``
  （§68.4 追记；原生 Pages.swift ``.reveal``：``fileExists ? p : deletingLastPathComponent``——用户至少能看见它
  本该在哪、顺手建出来）；连根都不在（相对路径的怪值）才 404。
- create：``mkdir -p``（已在 = ``created:false`` 幂等）；``default_target_repo`` 另 ``git init -q``
  （原生 createTargetRepoDir 同款，best-effort：失败只回执 ``git_init:"failed"``）；mkdir 失败 →
  500 ``could not create the folder: <why>``（web 前缀原生句「创建目录失败：」）。
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
    "default_target_repo": ("approval", True),
}
# 「打开」落到 vault 根而不是存值本身的那一把键（web `vaultPaths.VAULT_RAW_KEY` 同名）
VAULT_RAW_KEY = "obsidian_raw"

Opener = Callable[[Path], None]
Runner = Callable[[list], int]


def _default_opener(path: Path) -> None:
    subprocess.run(["/usr/bin/open", str(path)], check=False, timeout=20)


def _default_runner(argv: list) -> int:
    try:
        return subprocess.run(argv, check=False, capture_output=True, timeout=30).returncode
    except (OSError, subprocess.SubprocessError):
        return 127


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
    ``~/Documents/Vault`` 整棵没建时开到 ``~/Documents``，不是开一个同样不存在的父目录）。"""
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


def _git_init(path: Path, run: Runner) -> str:
    if (path / ".git").exists():
        return "skipped"
    return "done" if run(["git", "-C", str(path), "init", "-q"]) == 0 else "failed"


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
    git_init = _git_init(path, runner or _default_runner) if FOLDER_FIELDS[key][1] else None
    return {"ok": True, "key": key, "path": str(path), "created": not existed, "git_init": git_init}
