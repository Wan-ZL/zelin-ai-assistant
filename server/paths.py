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


def config_path(home: Path) -> Path:
    # 镜像 act/lib/config.py CONFIG_PATH（HOME / "config.yaml"）——server 只读
    # 一个键：`registry.backend` 回滚开关（§53.6，board_source 真源判定用）
    return home / "config.yaml"


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


def self_improve_lane_path(home: Path) -> Path:
    # §65：act/lib/self_improve.lane_state_path()——自动草稿 PR 通道的暂停状态；
    # server 只在 owner 点「恢复通道」时写 paused:false（server/self_improve_lane.py）
    return home / "state" / "self_improve" / "lane.json"


def settings_overrides_path(home: Path) -> Path:
    # §15.3：act/lib/config.SETTINGS_OVERRIDES_PATH（STATE_DIR / settings_overrides.json）
    return home / "state" / "settings_overrides.json"


def secrets_dir(home: Path) -> Path:
    # §19：act/lib/secrets.SECRETS_DIR（HOME / config / secrets；dir 0700 / file 0600）
    return home / "config" / "secrets"


def runtime_json_path(home: Path) -> Path:
    # §19 / §55：install.sh 钉住的守护解释器指针 config/runtime.json {"python": "<abs>"}
    return home / "config" / "runtime.json"


def radar_health_path(home: Path) -> Path:
    # §48：act/lib/health.HEALTH_PATH（radar 写、看板/诊断读）
    return home / "state" / "radar_health.json"


def install_report_path(home: Path) -> Path:
    # §23：install.sh 每步回执 state/install_report.json
    return home / "state" / "install_report.json"


def setup_done_path(home: Path) -> Path:
    # §68：首次运行向导完成标记（web 版替代原生 UserDefaults setupWizardCompleted）
    return home / "state" / "setup_done.json"


def update_check_path(home: Path) -> Path:
    # §26：act/lib/update_check 独占读写的缓存
    return home / "state" / "update_check.json"


def user_log_dir() -> Path:
    # §55：launchd 模板的 StandardOut/ErrorPath 目录（server/actd/radars 的 *.launchd.log）
    return Path.home() / "Library" / "Logs" / "zelin-ai-assistant"


def screenpipe_dir() -> Path:
    # §15.2：录制引擎的数据目录（db.sqlite 的 mtime = 「最近写入」；engine.log = 引擎日志，
    # 与 shell/Sources/Recording.swift engineLogPath 同一路径）
    return Path.home() / ".screenpipe"


def actd_log_path(home: Path) -> Path:
    # §15.2 / 原生 AppPaths.actdLogPath：录制与数据接入页「state/actd.log 更新于」读的文件
    return home / "state" / "actd.log"


def cron_probe_path(home: Path) -> Path:
    # §25：act/lib/checks/cron.CRON_PROBE_PATH（cron 链每轮写的 FDA 探针；依赖检查页「定时任务磁盘权限」行）
    return home / "state" / "cron_probe.json"


def vault_sync_mode_path(home: Path) -> Path:
    # ingest/vault-sync.sh 的模式文件（"mirror" = 链在 state/vault-mirror 里干活，不碰 ~/Documents）
    return home / "state" / "vault_sync_mode"


def vault_mirror_dir(home: Path) -> Path:
    # ingest/vault-sync.sh VAULT_MIRROR
    return home / "state" / "vault-mirror"


def repo_root() -> Path:
    """server/ 所在的 repo 根（``python -m act.*`` 子进程的 cwd；不随进程 cwd 变）。"""
    return Path(__file__).resolve().parent.parent


def web_dist_dir() -> Path:
    """web/dist 静态资源根（相对 repo 根，不随 cwd 变）。"""
    return repo_root() / "web" / "dist"
