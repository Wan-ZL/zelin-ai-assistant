"""server/about.py — 「关于」与更新检查的 server 半边（§26 / §56.1 / §68.6）。

- ``GET /api/about`` → ``{"version", "home", "repo", "update_available", "update_check"}``：
  版本真源 = ``act.__version__``（§56.1：act/_version.py 盖章 → git describe →
  回落值）；``update_available`` 原样透传 dashboard.json 的同名顶层键（§26，
  actd 每 pass 投影；缺席 = 没有已知新版）；``update_check`` = ``state/update_check.json``
  的公开子集（checked_at / latest / url；ETag 不外发）。
- ``POST /api/update/check`` → ``python -m act.lib.update_check --force``（§26 手动
  「立即检查」CLI；``updates.check_enabled: false`` 时它自己拒发网络请求），stdout
  那一行 JSON 原样透出；子进程失败 → ``{"ok": false, "error": ...}``。
  自动部署（§56，D17）让 owner 机器不再需要 Sparkle——合并即上岗；这里只负责
  「有没有新版」的诚实告知与 release 页链接（原生关于页同款：绝不自动下载执行）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from server import paths, subproc
from server.errors import UnknownFieldError

_UPDATE_TIMEOUT_S = 40


def version() -> str:
    try:
        from act import __version__  # act 包本身在依赖方向门的白名单内（server → act）
        return str(__version__)
    except Exception:  # noqa: BLE001 - 版本答不上也不许 500，诚实报 unknown
        return "unknown"


def _read_json(p: Path) -> Optional[dict]:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def update_check_public(home: Path) -> Optional[dict]:
    doc = _read_json(paths.update_check_path(home))
    if doc is None:
        return None
    return {k: doc.get(k) for k in ("checked_at", "latest", "url", "pkg_asset_url")}


def snapshot(home: Path) -> dict:
    """``GET /api/about``。"""
    board = _read_json(paths.dashboard_path(home)) or {}
    update = board.get("update_available")
    return {
        "version": version(),
        "home": str(home),
        "repo": str(paths.repo_root()),
        "update_available": update if isinstance(update, dict) else None,
        "update_check": update_check_public(home),
    }


def check_now(home: Path, payload: dict, runner=None) -> dict:
    """``POST /api/update/check``：§26 ``--force`` 一次；返回 CLI 的 JSON 行。"""
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    rc, out, err = subproc.run_module(home, "act.lib.update_check", ["--force"],
                                      timeout_s=_UPDATE_TIMEOUT_S, runner=runner)
    doc = subproc.parse_json_output(out)
    if doc is None:
        return {"ok": False, "error": subproc.tail(err or out) or ("update_check exited %d" % rc)}
    return doc
