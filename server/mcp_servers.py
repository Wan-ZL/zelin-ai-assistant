"""server/mcp_servers.py — 设置页「MCP servers」只读列表（§15 v0.46 追记 / §68.9）。

原生 SettingsMCP.swift 的读侧 1:1 搬到 server（Skills 商店是 §67 的 ``GET/POST /api/skills``，
写者 act/lib/skills.py——不在本模块）：

- ``GET /api/mcp`` → 用户级 ``~/.claude.json`` 与项目级 ``<home>/.mcp.json`` 的
  ``mcpServers``。**隐私规则**（原生同款，load-bearing）：``~/.claude.json`` 还装着
  无关的 Claude Code 状态，**只**取 ``mcpServers`` 子树；env 只给个数；args / URL
  过密钥掩码（sk-ant- / xox* / AKIA / gh*_ / Bearer），URL 的 query 整段打码。
- 作用域行另带 add-only ``path_display``（``$HOME`` 缩成 ``~``，原生 abbrevHome——server 知道
  HOME、客户端不知道）；两个固定路径由 ``scope_paths`` 单点计算，``POST /api/reveal
  {target:"mcp_user"|"mcp_project"}``（server/files）的「在 Finder 显示」复用同一处（§68.9 追记）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

# act/lib/sanitize._SECRET_PATTERNS 的子集（server 不 import act.lib.sanitize：它带
# config 读取）；掩码只为展示，不是安全边界（本机 loopback，owner 自己的文件）
_SECRET_RE = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{8,}|xox[abprs]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{12,}|"
    r"gh[pousr]_[A-Za-z0-9]{8,}|Bearer\s+[A-Za-z0-9._-]{8,})")


def mask_secrets(text: str) -> str:
    return _SECRET_RE.sub("●●●", text)


# --------------------------------------------------------------------------- #
# MCP servers
# --------------------------------------------------------------------------- #
def _masked_url(raw: str) -> str:
    cut = raw.find("?")
    if cut < 0:
        return mask_secrets(raw)
    return mask_secrets(raw[:cut]) + "?●●●"


def _transport(v: dict) -> str:
    t = str(v.get("type") or "").lower()
    if t:
        for word in ("sse", "http", "stdio"):
            if word in t:
                return word
    return "http" if v.get("url") is not None else "stdio"


def _stdio_summary(v: dict) -> str:
    args = v.get("args") if isinstance(v.get("args"), list) else []
    parts = [str(v.get("command") or "")] + [str(a) for a in args]
    return mask_secrets(" ".join(x for x in parts if x))


def _server_entry(name: str, v: dict, scope: str) -> dict:
    transport = _transport(v)
    summary = _stdio_summary(v) if transport == "stdio" else _masked_url(str(v.get("url") or ""))
    env = v.get("env")
    return {"name": name, "scope": scope, "transport": transport, "summary": summary,
            "env_count": len(env) if isinstance(env, dict) else 0}


def abbrev_home(path: Path, user_home: Path) -> str:
    """原生 SettingsMCP.abbrevHome：``$HOME`` 前缀缩成 ``~``（``/Users/me/.claude.json`` →
    ``~/.claude.json``），不在 HOME 下的原样返回。纯字串前缀匹配，不 resolve（展示用，不是安全边界）。"""
    text, home = str(path), str(user_home).rstrip("/")
    if not home:            # HOME="/"：什么都不缩（否则所有路径都成 ~/…）
        return text
    if text == home:
        return "~"
    if text.startswith(home + "/"):
        return "~" + text[len(home):]
    return text


def scope_paths(home: Path, user_home: Optional[Path] = None) -> "dict[str, Path]":
    """两个作用域的固定配置文件路径（唯一计算点：列表与 reveal 共用）。"""
    return {"user": (user_home or Path.home()) / ".claude.json", "project": home / ".mcp.json"}


def _read_scope(path: Path, scope: str, user_home: Path) -> dict:
    """一个作用域：``{"path", "path_display", "exists", "parseable", "servers": [...]}``。"""
    out = {"scope": scope, "path": str(path), "path_display": abbrev_home(path, user_home),
           "exists": path.is_file(), "parseable": True, "servers": []}
    if not out["exists"]:
        return out
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out["parseable"] = False
        return out
    mcp = doc.get("mcpServers") if isinstance(doc, dict) else None
    if isinstance(mcp, dict):
        out["servers"] = [_server_entry(n, v, scope) for n, v in sorted(mcp.items()) if isinstance(v, dict)]
    return out


def mcp(home: Path, user_home: Optional[Path] = None) -> dict:
    """``GET /api/mcp``：``{"scopes": [user, project]}``（只读；增删改在终端 ``claude mcp``）。"""
    user_home = user_home or Path.home()
    paths = scope_paths(home, user_home)
    return {"scopes": [_read_scope(paths[scope], scope, user_home) for scope in ("user", "project")]}
