"""server/radars.py — 设置页 Slack / Gmail 接入区的「后台雷达」行（CONTRACT §48.7 / §68.1）：
``GET /api/radars`` + ``POST /api/radars/reinstall {source}``。

原生 SettingsGmail / SettingsSlack 的 agentRow：「后台雷达 · 已安装，每 N 分钟自动运行 /
未安装」+「重新安装」。新架构里两个雷达仍是各自的 launchd agent
（``act/launchd/com.zelin.aiassistant.{gmail,slack}radar.plist``，install.sh 步 5 渲染 + 加载，
§48.5 源开关闸门），所以：

- **状态 = 问 launchd 本人**：``launchctl print gui/<uid>/<label>`` 退出 0 ⇔ 已加载
  （server/repair.py 同一探针）；``interval_s`` 从模板的 ``StartInterval`` 读（truth =
  模板，不手抄「每 5 分钟」）；``plist_installed`` = ``~/Library/LaunchAgents/<label>.plist``
  在不在。非 darwin → ``loaded: null``（没有 launchd 可问，如实说不知道）。
- **重新安装 = 渲染 + 加载走 install.sh 自己的渲染器**：``bash install.sh --reinstall-agent
  <label>``（同一个 ``render_launchd_plist`` / ``launchd_unload`` / ``launchd_load`` /
  ``verify_launchd_agent``，占位符替换只有一处实现——§55 路径纪律；server **绝不**自己写
  plist）。退出码：0 装好 → 200；3 源开关是关的（§48.5 闸门，装了也会被下次 install.sh
  退役）→ 409 CONFLICT「先开开关」；4 没有 pinned 守护解释器（从没跑过 install.sh）→ 409
  指向 ``bash install.sh``；其余非零 → 500 带输出尾巴。非 darwin → 501。

写路由与其它写面同过四闸（app.py）。``runner`` 注入缝：测试绝不真跑 launchctl / install.sh。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from server import paths, repair
from server.errors import (ApiError, ConflictError, InvalidFieldError,
                           NotImplementedError501, UnknownFieldError)

# source → launchd label（与 act/launchd/*.plist 文件名逐字一致；判例钉住模板存在）
RADAR_LABELS = {
    "gmail": "com.zelin.aiassistant.gmailradar",
    "slack": "com.zelin.aiassistant.slackradar",
}
_INTERVAL_RE = re.compile(r"<key>StartInterval</key>\s*<integer>(\d+)</integer>")
_REINSTALL_TIMEOUT_S = 120

Runner = Callable[[list], "tuple[int, str]"]


def template_path(label: str) -> Path:
    return paths.repo_root() / "act" / "launchd" / (label + ".plist")


def interval_s(label: str) -> Optional[int]:
    """模板 ``StartInterval``（秒）；模板缺席 / 无该键 → None。"""
    try:
        text = template_path(label).read_text(encoding="utf-8")
    except OSError:
        return None
    m = _INTERVAL_RE.search(text)
    return int(m.group(1)) if m else None


def _plist_installed(label: str) -> bool:
    return (Path.home() / "Library" / "LaunchAgents" / (label + ".plist")).is_file()


def _is_darwin(platform: Optional[str]) -> bool:
    return (platform or sys.platform) == "darwin"


def snapshot(home: Path, runner: Optional[Runner] = None,
             platform: Optional[str] = None) -> dict:
    """``GET /api/radars`` → ``{"radars": {"gmail": {...}, "slack": {...}}}``。"""
    run = runner or repair.default_runner
    darwin = _is_darwin(platform)
    out = {}
    for source, label in RADAR_LABELS.items():
        out[source] = {
            "label": label,
            "interval_s": interval_s(label),
            "loaded": repair.loaded(label, run) if darwin else None,
            "plist_installed": _plist_installed(label) if darwin else False,
        }
    return {"radars": out}


def _source_of(payload: dict) -> str:
    unknown = set(payload) - {"source"}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    source = payload.get("source")
    if source not in RADAR_LABELS:
        raise InvalidFieldError("source must be gmail or slack", {"field": "source"})
    return source


def _default_install_runner(argv: list) -> "tuple[int, str]":
    try:
        proc = subprocess.run(argv, check=False, capture_output=True, text=True,
                              timeout=_REINSTALL_TIMEOUT_S, cwd=str(paths.repo_root()))
    except subprocess.TimeoutExpired:
        return 124, "install.sh --reinstall-agent timed out after %ds" % _REINSTALL_TIMEOUT_S
    except OSError as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _raise_for_rc(rc: int, out: str, source: str, label: str) -> None:
    tail = out.strip()[-400:]
    if rc == 3:
        raise ConflictError(
            "the %s source is switched off - enable it first (a switched-off radar is not installed, §48.5)" % source,
            {"label": label, "source": source, "fix": "enable the source switch"})
    if rc == 4:
        raise ConflictError(
            "no pinned daemon interpreter - run `bash install.sh` once first",
            {"label": label, "fix": "bash install.sh"})
    raise ApiError("install.sh --reinstall-agent exited %d: %s" % (rc, tail),
                   {"label": label, "rc": rc})


def reinstall(home: Path, payload: dict, runner: Optional[Runner] = None,
              install_runner: Optional[Runner] = None,
              platform: Optional[str] = None) -> dict:
    """``POST /api/radars/reinstall {source}`` → ``{"ok", "source", "label", "loaded"}``。"""
    source = _source_of(payload)
    if not _is_darwin(platform):
        raise NotImplementedError501("launchd agents are macOS only")
    label = RADAR_LABELS[source]
    argv = ["bash", str(paths.repo_root() / "install.sh"), "--reinstall-agent", label]
    rc, out = (install_runner or _default_install_runner)(argv)
    if rc != 0:
        _raise_for_rc(rc, out, source, label)
    return {"ok": True, "source": source, "label": label,
            "loaded": repair.loaded(label, runner or repair.default_runner)}
