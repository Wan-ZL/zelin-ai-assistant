"""server/repair.py — 管线横幅的「一键修复」：``POST /api/repair/actd``（§47.4 / §54.2 / §68.8）。

原生 PipelineRepair（Doctor.swift）= 重渲 actd plist + launchctl + 轮询。web 版走
**最小诚实版**：actd 的 launchd agent ``com.zelin.aiassistant.actd`` **已加载**
（``launchctl print gui/<uid>/<label>`` 退出 0）→ ``launchctl kickstart -k`` 一次
（重启进程，§47.4 horizontal 的修法原句）；**未加载** → 409 CONFLICT，修法 =
``bash install.sh``（渲染 + 加载模板是安装器的活，server 不重造 install.sh 的
占位符替换——§55 路径纪律只有一处实现）。非 darwin → 501。

``runner`` 注入缝（测试绝不真跑 launchctl）；返回 ``{"ok", "label", "action": "kickstart"}``。
server 不 import act.doctor（entrypoint 层）：label 常量在此镜像，判例
tests/test_server_repair.py 钉住与 act/doctor.ACTD_LABEL 逐字一致。
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Optional

from server.errors import ApiError, ConflictError, NotImplementedError501, UnknownFieldError

ACTD_LABEL = "com.zelin.aiassistant.actd"   # mirrors act/doctor.ACTD_LABEL
_TIMEOUT_S = 30

Runner = Callable[[list], "tuple[int, str]"]


def default_runner(argv: list) -> "tuple[int, str]":
    try:
        proc = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def domain() -> str:
    """launchd GUI domain of the current user（``gui/<uid>``）；os.getuid 只在 POSIX 存在
    （Windows 腿的判例注入 platform）。server/radars.py 同用（§48.7）。"""
    getuid = getattr(os, "getuid", None)
    return "gui/%d" % (getuid() if getuid else 0)


def loaded(label: str, run: Runner) -> bool:
    """``launchctl print gui/<uid>/<label>`` 退出 0 ⇔ 已加载（radars.py 的状态探针也走这里）。"""
    rc, _out = run(["/bin/launchctl", "print", "%s/%s" % (domain(), label)])
    return rc == 0


def _gate(payload: dict, platform: Optional[str]) -> None:
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("launchd repair is macOS only")


def kickstart_actd(payload: dict, runner: Optional[Runner] = None,
                   platform: Optional[str] = None) -> dict:
    """``POST /api/repair/actd``。"""
    _gate(payload, platform)
    run = runner or default_runner
    if not loaded(ACTD_LABEL, run):
        raise ConflictError(
            "%s is not loaded in launchd - run `bash install.sh` to render and load it" % ACTD_LABEL,
            {"label": ACTD_LABEL, "fix": "bash install.sh"})
    rc, out = run(["/bin/launchctl", "kickstart", "-k", "%s/%s" % (domain(), ACTD_LABEL)])
    if rc != 0:
        raise ApiError("launchctl kickstart exited %d: %s" % (rc, out.strip()[-300:]),
                       {"label": ACTD_LABEL, "rc": rc})
    return {"ok": True, "label": ACTD_LABEL, "action": "kickstart"}
