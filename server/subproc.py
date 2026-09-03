"""server/subproc.py — server 起 ``python -m act.<entrypoint>`` 子进程的唯一出口（§49 / §58.3）。

server 不准 import act 的 entrypoint 层（依赖方向门：server 只到 act.lib），所以
doctor / update_check / radar_claude_sessions 这类一次性命令一律以子进程运行：
解释器 = ``sys.executable``（server 自己跑在 §55 验过的守护解释器上，它有的
授权子进程原样继承）、cwd = repo 根（``act`` 包可 import）、env 带
``AIASSISTANT_HOME``。``runner`` 是注入缝——测试绝不真起子进程（仓规）。
所有调用都有超时与输出上限；任何失败都变成 ``(rc, stdout, stderr)`` 而不是异常。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from server import paths

Runner = Callable[[list, dict, Path, int], "tuple[int, str, str]"]

OUTPUT_CAP = 200_000   # 字符；doctor --json 几 KB，留足余量但不许无界


def default_runner(argv: list, env: dict, cwd: Path, timeout_s: int) -> "tuple[int, str, str]":
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=env, check=False,
                              capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return 124, "", "%s timed out after %ds" % (" ".join(argv[-2:]), timeout_s)
    except OSError as exc:
        return 127, "", str(exc)
    return proc.returncode, (proc.stdout or "")[-OUTPUT_CAP:], (proc.stderr or "")[-OUTPUT_CAP:]


def module_env(home: Path, extra: Optional[dict] = None) -> dict:
    env = dict(os.environ)
    env["AIASSISTANT_HOME"] = str(home)
    env.update(extra or {})
    return env


def run_module(home: Path, module: str, args: list, *, timeout_s: int,
               runner: Optional[Runner] = None,
               extra_env: Optional[dict] = None) -> "tuple[int, str, str]":
    """``sys.executable -m <module> <args>``，返回 (rc, stdout, stderr)。"""
    argv = [sys.executable, "-m", module] + list(args)
    return (runner or default_runner)(argv, module_env(home, extra_env), paths.repo_root(), timeout_s)


def _json_dict(text: str) -> Optional[dict]:
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def parse_json_output(stdout: str) -> Optional[dict]:
    """stdout 里的 JSON 对象：整段先试（doctor ``--json`` 是多行缩进 JSON），
    不行再从第一个 ``{`` 起试（前面可能夹着一行人话）。"""
    text = (stdout or "").strip()
    if not text:
        return None
    doc = _json_dict(text)
    brace = text.find("{")
    if doc is None and brace > 0:
        doc = _json_dict(text[brace:])
    return doc


def tail(text: str, n: int = 300) -> str:
    return (text or "").strip()[-n:]
