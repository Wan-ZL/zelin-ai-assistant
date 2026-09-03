"""server/setup.py — 首次运行向导的 server 半边（§15 v0.14 初始设置向导 → §68.5 web 版）。

owner（2026-09-02）：「我能够在另一台电脑上起一个空白环境……就能够直接使用。」
新机器上 ``bash install.sh`` 之后看板打开的第一页不该是空看板，而是一条向导：
config.yaml（从 config.example.yaml 复制）→ 完全磁盘访问（权限体检）→ 可选的
Gmail / Slack 凭证 → 完成。

- ``GET /api/setup`` → ``{"needed": bool, "done": bool, "config_exists", "config_example_exists",
  "secrets": {name: present}, "home", "protected_location"}``。
  ``needed`` = 未写完成标记 ∧（config.yaml 缺席 ∨ 三把主凭证一把都没有）。
- ``POST /api/setup/config-from-example`` → 复制 ``config.example.yaml`` → ``config.yaml``
  （**已存在则 409，绝不覆盖**——install.sh 步 2 同一条纪律）。
- ``POST /api/setup/complete`` → 写 ``state/setup_done.json`` ``{"completed_at": iso}``
  （原生 UserDefaults ``setupWizardCompleted`` 的 server 侧替身：标记在 home 下，
  换壳 / 换浏览器不重问）。``POST /api/setup/reset`` 删标记 = 「重新运行初始设置」。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
from pathlib import Path

from server import paths, permissions, secrets_store
from server.errors import ConflictError, NotFoundError, UnknownFieldError

_PRIMARY_SECRETS = ("anthropic-api-key.txt", "slack-user-token.txt", "gmail-app-password.txt")


def _example_path(home: Path) -> Path:
    return home / "config.example.yaml"


def done(home: Path) -> bool:
    return paths.setup_done_path(home).is_file()


def snapshot(home: Path) -> dict:
    """``GET /api/setup``。"""
    config_exists = paths.config_path(home).is_file()
    secrets = {name: secrets_store.read_value(home, name) is not None for name in _PRIMARY_SECRETS}
    is_done = done(home)
    return {
        "needed": (not is_done) and (not config_exists or not any(secrets.values())),
        "done": is_done,
        "config_exists": config_exists,
        "config_example_exists": _example_path(home).is_file(),
        "secrets": secrets,
        "home": str(home),
        "protected_location": permissions.protected_location(home),
    }


def _reject_fields(payload: dict) -> None:
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})


def config_from_example(home: Path, payload: dict) -> dict:
    """``POST /api/setup/config-from-example``：复制模板；已存在 → 409 CONFLICT。"""
    _reject_fields(payload)
    target = paths.config_path(home)
    if target.exists():
        raise ConflictError("config.yaml already exists - not overwriting", {"path": str(target)})
    example = _example_path(home)
    if not example.is_file():
        raise NotFoundError("config.example.yaml not found", {"path": str(example)})
    tmp = target.with_suffix(".yaml.tmp")
    shutil.copyfile(example, tmp)
    os.replace(tmp, target)
    return {"ok": True, "path": str(target), "setup": snapshot(home)}


def complete(home: Path, payload: dict) -> dict:
    """``POST /api/setup/complete``：写完成标记（幂等）。"""
    _reject_fields(payload)
    p = paths.setup_done_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({"completed_at": stamp}) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return {"ok": True, "setup": snapshot(home)}


def reset(home: Path, payload: dict) -> dict:
    """``POST /api/setup/reset``：删完成标记（设置 → 通用「重新运行初始设置」）。"""
    _reject_fields(payload)
    try:
        paths.setup_done_path(home).unlink()
    except FileNotFoundError:
        pass
    return {"ok": True, "setup": snapshot(home)}
