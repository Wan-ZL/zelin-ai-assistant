"""server/sync_pairing.py — 设置「同步 / 配对」的 server 半边（CONTRACT §31 / §35 / §68.15；原生 SettingsSync.swift）。

原生 SyncSettingsModel 起 runtime python ``-m act.syncd --pair --json [--label X]`` / ``--disable``，
自己用 CoreImage 画二维码。server 同一条命令经 server/subproc（注入缝，测试绝不真起）：

- ``GET /api/sync`` → ``{enabled, channel_id, label, default_label, qr_png_base64}``：只读
  ``state/sync.json``（``mode == "cloud"`` 且有 channel_id = 开着）与 syncd 上次 ``--pair`` 落下的
  ``state/sync/pairing-qr.png``（act/lib/qr 纯 stdlib 画的；开着才带回，base64 进 JSON——server 不新开二进制路由）。
  ``default_label`` = 这台 Mac 的主机名（原生 Host.current().localizedName 的 server 版；从未命名时的预填）。
- ``POST /api/sync/pair {label?}`` → ``--pair --json``（label 非空才带 ``--label``，≤64 字符——§35 二维码容量；
  不带 = syncd 沿用 state/sync.json 里的名字，绝不把半截输入存进去）→ ``{ok: true, channel_id, label, registered,
  qr_png_base64}``；解释器起不来 → ``{ok:false, error:"no_python", message}``；其余失败 → ``{ok:false,
  error:"pair_failed", message}``（stderr 尾巴）。幂等：重跑 = 同一 channel、同一二维码（syncd 的 init_channel）。
- ``POST /api/sync/disable {}`` → ``--disable``（mode=off，密钥保留、重开不用重配对）→ ``{ok}`` + 快照。

syncd 是 ``state/sync/`` 与 ``state/sync.json`` 的唯一写者（§31）；server 只读它们、只起它。
"""
from __future__ import annotations

import base64
import json
import socket
from pathlib import Path
from typing import Optional

from server import subproc
from server.errors import InvalidFieldError, UnknownFieldError

PAIR_TIMEOUT_S = 90       # init_channel 里有一次 Supabase INSERT（联网失败也返回，registered:false）
DISABLE_TIMEOUT_S = 30
LABEL_MAX = 64            # §35：设备名随二维码尾字节走，原生 didSet 同上限


def sync_config_path(home: Path) -> Path:
    return home / "state" / "sync.json"


def qr_png_path(home: Path) -> Path:
    return home / "state" / "sync" / "pairing-qr.png"


def read_sync_config(home: Path) -> dict:
    try:
        doc = json.loads(sync_config_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def default_label() -> str:
    """原生 defaultDeviceName：电脑名；拿不到时回历史默认「这台 Mac」（syncd._pair_label 同一兜底）。"""
    try:
        name = socket.gethostname().split(".")[0].strip()
    except OSError:
        name = ""
    return name or "这台 Mac"


def _qr_base64(home: Path) -> Optional[str]:
    try:
        return base64.b64encode(qr_png_path(home).read_bytes()).decode("ascii")
    except OSError:
        return None


def snapshot(home: Path) -> dict:
    cfg = read_sync_config(home)
    channel_id = str(cfg.get("channel_id") or "")
    enabled = str(cfg.get("mode") or "").lower() == "cloud" and bool(channel_id)
    return {
        "enabled": enabled,
        "channel_id": channel_id,
        "label": str(cfg.get("label") or "").strip(),
        "default_label": default_label(),
        "qr_png_base64": _qr_base64(home) if enabled else None,
    }


def _normalize_label(label) -> Optional[str]:
    """空白归一、≤64；None / 空 → None（= 不带 --label）；非字串 400。"""
    if label is None:
        return None
    if not isinstance(label, str):
        raise InvalidFieldError("label must be a string", {"field": "label"})
    label = " ".join(label.split())
    if len(label) > LABEL_MAX:
        raise InvalidFieldError("label is too long", {"field": "label", "max": LABEL_MAX})
    return label or None


def _label_arg(payload: dict) -> Optional[str]:
    unknown = set(payload) - {"label"}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    return _normalize_label(payload.get("label"))


def _pair_failure(rc: int, out: str, err: str) -> dict:
    tail = subproc.tail(err or out) or ("syncd --pair exited %d" % rc)
    return {"ok": False, "error": "no_python" if rc == 127 else "pair_failed", "message": tail}


def _pair_ok(doc: Optional[dict]) -> bool:
    return bool(doc and doc.get("channel_id") and doc.get("qr_blob"))


def pair(home: Path, payload: dict, runner=None) -> dict:
    """``POST /api/sync/pair``：起 ``act.syncd --pair --json``，JSON 行透传 + 二维码 PNG（base64）。"""
    label = _label_arg(payload)
    args = ["--pair", "--json"] + (["--label", label] if label else [])
    rc, out, err = subproc.run_module(home, "act.syncd", args, timeout_s=PAIR_TIMEOUT_S, runner=runner)
    doc = subproc.parse_json_output(out)
    if not _pair_ok(doc):
        return _pair_failure(rc, out, err)
    return {
        "ok": True,
        "channel_id": str(doc["channel_id"]),
        "label": str(doc.get("label") or ""),
        "registered": bool(doc.get("registered")),
        "qr_png_base64": _qr_base64(home),
    }


def disable(home: Path, payload: dict, runner=None) -> dict:
    """``POST /api/sync/disable {}``：``act.syncd --disable``（mode=off；密钥保留）→ ``{ok}`` + 快照。"""
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    rc, _out, err = subproc.run_module(home, "act.syncd", ["--disable"], timeout_s=DISABLE_TIMEOUT_S, runner=runner)
    result = snapshot(home)
    result["ok"] = rc == 0
    if rc != 0:
        result["error"] = "no_python" if rc == 127 else "disable_failed"
        result["message"] = subproc.tail(err) or ("syncd --disable exited %d" % rc)
    return result
