"""B 档场景 fixture 包（CONTRACT §58 QA 闸门的证据面）。

每个同级脚本是一个可单跑的场景：``python3 scripts/qa/fixtures_b/<slug>.py``，
``main()`` 回 0/1、最后一行 stdout 是一句证据（≤200 字符）。清单真源 =
``qa/coverage_fixtures_b.json``（proof ``fixture:B-<nn>-<slug>``）。

本 ``__init__`` 只做一件事：**单跑时**（argv[0] 就在本包目录里）把
``AIASSISTANT_HOME`` 钉进 /tmp 的一次性目录，保证 ``act.*`` 的模块级路径常量
在 import 那一刻就落在沙箱里，绝不指向 owner 的 live checkout。in-process 跑
（tests/test_fixtures_b_smoke.py）时套件自己的沙箱已经生效，这里什么都不做；
两条路径最终都由 ``_harness.sandbox()`` 把路径常量重新指到本场景的临时 home。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent

# 单跑 bootstrap 建的那个 home（收尾要删；in-process 时是 None）
BOOT_HOME = None


def _standalone() -> bool:
    try:
        return Path(sys.argv[0]).resolve().parent == _PKG_DIR
    except (IndexError, OSError, ValueError):
        return False


if _standalone() and "act.lib.config" not in sys.modules:
    BOOT_HOME = tempfile.mkdtemp(prefix="zaa-cov-boot-", dir="/tmp")
    os.environ["AIASSISTANT_HOME"] = BOOT_HOME
