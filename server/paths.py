"""路径推导——镜像 act/lib/config.py 的布局常量，但绝不 import act。

act.lib.config 在 import 时读 env 并携带写路径（ensure_state_dirs 等）；
server 侧只需要几个只读路径，自己算，零依赖（registry 单写者原则 §44）。
镜像 drift-pin：tests/test_server_paths_mirror.py。
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


def store2_db_path(home: Path) -> Path:
    # §53：store2 SQLite 真源（registry.store2_db_path 镜像）
    return home / "state" / "store2.db"


def store2_truth_path(home: Path) -> Path:
    # §53：激活标记——在 = SQLite 是真源，YAML 目录只是冻结工件/导出镜像
    return home / "state" / "store2_truth.json"


def inbox_dir(home: Path) -> Path:
    return home / "state" / "inbox"


def heartbeat_path(home: Path) -> Path:
    # §47.4：act/lib/heartbeat.HEARTBEAT_PATH（actd 每阶段 touch；mtime 为真源）
    return home / "state" / "actd.heartbeat"


def loop_health_path(home: Path) -> Path:
    # §47.3：act/actd.LOOP_HEALTH_NAME（连续 pass 崩溃计数）
    return home / "state" / "loop_health.json"


def web_dist_dir() -> Path:
    """web/dist 静态资源根（相对 repo 根，不随 cwd 变）。"""
    return Path(__file__).resolve().parent.parent / "web" / "dist"
