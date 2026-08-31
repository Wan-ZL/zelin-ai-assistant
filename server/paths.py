"""路径推导——镜像 act/lib/config.py 的布局常量，但绝不 import act。

act.lib.config 在 import 时读 env 并携带写路径（ensure_state_dirs 等）；
server 侧只需要五个只读路径，自己算，零依赖（registry 单写者原则 §44）。
"""
from __future__ import annotations

import os
from pathlib import Path

# 与 act/lib/config.py:_home() 逐字同一默认值
DEFAULT_HOME = "~/Projects/zelin-ai-assistant"


def home_dir(explicit: "str | Path | None" = None) -> Path:
    """解析 AIASSISTANT_HOME（测试注入缝：显式传参优先于 env）。"""
    raw = explicit or os.environ.get("AIASSISTANT_HOME", DEFAULT_HOME)
    return Path(raw).expanduser()


def dashboard_path(home: Path) -> Path:
    return home / "state" / "dashboard.json"


def registry_dir(home: Path) -> Path:
    return home / "act" / "registry"


def archive_dir(home: Path) -> Path:
    # v0.20.0 §4：归档卡 RELOCATE 到 archive/ 子目录（registry.py ARCHIVE_DIR）
    return registry_dir(home) / "archive"


def inbox_dir(home: Path) -> Path:
    return home / "state" / "inbox"


def web_dist_dir() -> Path:
    """web/dist 静态资源根（相对 repo 根，不随 cwd 变）。"""
    return Path(__file__).resolve().parent.parent / "web" / "dist"
