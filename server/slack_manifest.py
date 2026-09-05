"""server/slack_manifest.py — Slack 接入区的「复制 App Manifest」（§15.3 v0.14 / §54.4）：``GET /api/slack/manifest``。

模块名按对象「App Manifest」取 ``slack_manifest``——与真源 ``act/lib/slack_setup.py`` 同名会撞
防腐 #9「同一 basename 禁止出现在两个目录层级」；路由不变。

原生 SettingsSlack.swift 的 copyManifest 读 repo 的 ``config/slack-app-manifest.json``
（真源 = act/lib/slack_setup.manifest_json，drift-guard 钉住两者一致）写进剪贴板；web 没有
读 repo 文件的能力，server 把同一份文件原文交给页面，页面再写剪贴板。文件缺席 = 404
（repo 不完整），不 500；原生 guard 的后半句「读出来只剩空白」同样算缺席 → 同一个 404，
否则页面会把一串空白写进剪贴板还报「已复制 ✓」。
"""
from __future__ import annotations

from pathlib import Path

from server import paths
from server.errors import NotFoundError

MANIFEST_REL = Path("config") / "slack-app-manifest.json"


def manifest_path() -> Path:
    return paths.repo_root() / MANIFEST_REL


def manifest(_home: Path) -> dict:
    """``{"manifest": <json 原文>, "path": <repo 相对路径>}``。"""
    try:
        text = manifest_path().read_text(encoding="utf-8")
    except OSError:
        text = ""
    if not text.strip():
        # 缺席与空白同罪（原生 copyManifest 的 guard：try? String(contentsOfFile:) 失败 || trimmed.isEmpty）
        raise NotFoundError("slack app manifest not found", {"path": str(MANIFEST_REL)})
    return {"manifest": text, "path": str(MANIFEST_REL)}
