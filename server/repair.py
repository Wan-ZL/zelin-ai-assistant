"""server/repair.py — 管线横幅的「一键修复」：``POST /api/repair/actd``（§47.4 / §54.2 / §68.8）。

原生 PipelineRepair（Doctor.swift）= 重渲 actd plist + launchctl + 轮询。web 版按 actd 的
launchd agent ``com.zelin.aiassistant.actd`` 在不在 launchd 里分两条路（§68.8 + D50 追记）：

- **已加载**（``launchctl print gui/<uid>/<label>`` 退出 0）→ ``launchctl kickstart -k`` 一次
  （重启进程，§47.4 horizontal 的修法原句）→ ``{"action": "kickstart"}``；
- **未加载** → ``bash install.sh --reinstall-agent com.zelin.aiassistant.actd``（§48.7 雷达
  「重新安装」同一条路：渲染 + 加载走 install.sh 自己的渲染器，占位符替换只有一处实现——§55
  路径纪律；server **绝不**自己写 plist）→ ``{"action": "reinstall", "loaded"}``。install.sh
  退出 4（没有 pinned 守护解释器 = 从没跑过完整安装）或 install.sh 本身不在 → 409 CONFLICT，
  ``details.command`` 给可复制的手动命令 ``bash <repo>/install.sh``；超时 / 其余非零 → 500 带
  输出尾巴。label 与 argv 全是 server 常量，客户端只发 ``{}``。

非 darwin → 501。``runner``（launchctl）与 ``install_runner``（install.sh）两个注入缝——测试
绝不真跑 launchctl / install.sh；``default_install_runner`` 是 §48.7 radars.py 的同一把。
server 不 import act.doctor（entrypoint 层）：label 常量在此镜像，判例 tests/test_server_board_tools.py
钉住与 act/doctor.ACTD_LABEL 逐字一致；未加载那条路的判例 = tests/test_server_repair_actd_reinstall.py。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from server import paths
from server.errors import ApiError, ConflictError, NotImplementedError501, UnknownFieldError

ACTD_LABEL = "com.zelin.aiassistant.actd"   # mirrors act/doctor.ACTD_LABEL
_TIMEOUT_S = 30
# install.sh --reinstall-agent：unload + 渲染 + load + sleep 2 + verify（radars.py 同一上限）
INSTALL_TIMEOUT_S = 120

Runner = Callable[[list], "tuple[int, str]"]


def default_runner(argv: list) -> "tuple[int, str]":
    try:
        proc = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def default_install_runner(argv: list, timeout_s: Optional[int] = None) -> "tuple[int, str]":
    """``bash install.sh --reinstall-agent <label>`` 的默认 runner：cwd = repo 根、两流合并；
    超时 → rc 124 + 人话；bash 不在 → rc 127。§48.7 radars.py 与本模块共用。"""
    limit = INSTALL_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        proc = subprocess.run(argv, check=False, capture_output=True, text=True,
                              timeout=limit, cwd=str(paths.repo_root()))
    except subprocess.TimeoutExpired:
        return 124, "install.sh --reinstall-agent timed out after %ds" % limit
    except OSError as exc:
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


def install_sh_path() -> Path:
    """本 checkout 的 install.sh（§55：唯一的 plist 渲染器）。"""
    return paths.repo_root() / "install.sh"


def manual_command() -> str:
    """409 / 500 envelope 里可复制的手动命令：完整安装（渲染 + pin 解释器 + 加载全部 agent）。"""
    return "bash %s" % install_sh_path()


def _gate(payload: dict, platform: Optional[str]) -> None:
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    if (platform or sys.platform) != "darwin":
        raise NotImplementedError501("launchd repair is macOS only")


def _reinstall_actd(run: Runner, install_run: Runner) -> dict:
    """未加载的 actd：渲染 + 加载走 install.sh（D50，§68.8 追记）。"""
    script = install_sh_path()
    command = manual_command()
    if not script.is_file():
        raise ConflictError(
            "%s is not loaded in launchd and install.sh is missing at %s - run `bash install.sh` from a full checkout"
            % (ACTD_LABEL, script),
            {"label": ACTD_LABEL, "fix": "bash install.sh", "command": command})
    rc, out = install_run(["bash", str(script), "--reinstall-agent", ACTD_LABEL])
    if rc == 0:
        return {"ok": True, "label": ACTD_LABEL, "action": "reinstall", "loaded": loaded(ACTD_LABEL, run)}
    tail = out.strip()[-400:]
    if rc == 4:
        raise ConflictError(
            "%s is not loaded in launchd and no daemon interpreter is pinned - run `bash install.sh` once first"
            % ACTD_LABEL,
            {"label": ACTD_LABEL, "fix": "bash install.sh", "command": command, "rc": rc})
    if rc == 124:
        raise ApiError("install.sh --reinstall-agent timed out after %ds: %s" % (INSTALL_TIMEOUT_S, tail),
                       {"label": ACTD_LABEL, "rc": rc, "command": command})
    raise ApiError("install.sh --reinstall-agent exited %d: %s" % (rc, tail),
                   {"label": ACTD_LABEL, "rc": rc, "command": command})


def kickstart_actd(payload: dict, runner: Optional[Runner] = None,
                   install_runner: Optional[Runner] = None,
                   platform: Optional[str] = None) -> dict:
    """``POST /api/repair/actd``：已加载 → kickstart；未加载 → install.sh --reinstall-agent。"""
    _gate(payload, platform)
    run = runner or default_runner
    if not loaded(ACTD_LABEL, run):
        return _reinstall_actd(run, install_runner or default_install_runner)
    rc, out = run(["/bin/launchctl", "kickstart", "-k", "%s/%s" % (domain(), ACTD_LABEL)])
    if rc != 0:
        raise ApiError("launchctl kickstart exited %d: %s" % (rc, out.strip()[-300:]),
                       {"label": ACTD_LABEL, "rc": rc})
    return {"ok": True, "label": ACTD_LABEL, "action": "kickstart"}
