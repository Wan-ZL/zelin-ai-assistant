"""server/folders.py — 设置页目录字段的「打开」/「创建」（CONTRACT §68.1 目录字段；§54.4）：
``POST /api/folders/open {key}`` · ``POST /api/folders/create {key}``。

原生 Settings.swift 的 obsidianGroup（选择… / 打开 / 创建）与 approvalGroup（选择… / 创建文件夹）
的 server 落点。「选择…」是文件对话框——壳里走 §61.1 桥的 ``chooseFolder``（NSOpenPanel），
浏览器里退化成路径文本框，server 不参与；「打开」与「创建」作用在**已保存的 effective 值**上：
路径由 server 从设置目录读（``settings_catalog.effective``），**绝不接受客户端原始路径**
（reveal / ai-fix / terminal 同一纪律）——web 在草稿未保存时禁用这两颗按钮并提示先保存。

- ``key`` ∈ :data:`FOLDER_FIELDS`（``obsidian_raw`` / ``default_target_repo``；未知 400）。
- open：值为空 400；不是目录 404；darwin → ``/usr/bin/open <dir>``（访达），其它 501。
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


def open_folder(home: Path, payload: dict, opener: Optional[Opener] = None,
                platform: Optional[str] = None) -> dict:
    """``POST /api/folders/open {key}`` → ``{"ok": true, "path": "<dir>"}``。"""
    key = _key_of(payload)
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("opening a folder in Finder is macOS only")
    path = resolve(home, key)
    if not path.is_dir():
        raise NotFoundError("folder does not exist", {"key": key, "path": str(path)})
    (opener or _default_opener)(path)
    return {"ok": True, "key": key, "path": str(path)}


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
