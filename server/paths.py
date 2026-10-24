"""路径推导（CONTRACT §49 / §19）——镜像 act/lib/config.py 的布局常量，但绝不 import act.lib.config。

act.lib.config 在 import 时读 env 并携带写路径（ensure_state_dirs 等）；server 侧
从自己的 home 算路径（测试注入 home 不失真）。镜像 drift-pin：tests/test_server_paths_mirror.py。
v2.0.0：dashboard / registry / store2 / inbox / attachments / heartbeat / loop_health /
self_improve / telemetry / terminal_queue / shell heartbeat / radar_health / actd.log /
voice_gen 这些路径随 Act 管线退役（全文见 tag full）。
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
    return home / "config.yaml"


def settings_overrides_path(home: Path) -> Path:
    # §15.3：act/lib/config.SETTINGS_OVERRIDES_PATH（STATE_DIR / settings_overrides.json）
    return home / "state" / "settings_overrides.json"


def secrets_dir(home: Path) -> Path:
    # §19：act/lib/secrets.SECRETS_DIR（HOME / config / secrets；dir 0700 / file 0600）
    return home / "config" / "secrets"


def runtime_json_path(home: Path) -> Path:
    # §19 / §55：install.sh 钉住的 server 解释器指针 config/runtime.json {"python": "<abs>"}
    return home / "config" / "runtime.json"


def install_report_path(home: Path) -> Path:
    # §23：install.sh 每步回执 state/install_report.json
    return home / "state" / "install_report.json"


def setup_done_path(home: Path) -> Path:
    # §68：首次运行向导完成标记（web 版替代原生 UserDefaults setupWizardCompleted）
    return home / "state" / "setup_done.json"


def update_check_path(home: Path) -> Path:
    # §26：act/lib/update_check 独占读写的缓存
    return home / "state" / "update_check.json"


def recap_log_path(home: Path) -> Path:
    # §63：act/lib/recap_store.log_path（cron 与 server 派生的 act.recap 运行都 append 到这里）
    return home / "state" / "recap.log"


def user_log_dir() -> Path:
    # §55：launchd 模板的 StandardOut/ErrorPath 目录（server 的 *.launchd.log）
    return Path.home() / "Library" / "Logs" / "zelin-ai-assistant"


def screenpipe_dir() -> Path:
    # §15.2：录制引擎的数据目录（db.sqlite 的 mtime = 「最近写入」；engine.log = 引擎日志，
    # 与 shell/Sources/Recording.swift engineLogPath 同一路径）
    return Path.home() / ".screenpipe"


def ingest_log_path() -> Path:
    # §15.2 / §68.4：ingest/process-screenpipe.sh 的 LOGFILE（`$PROCESS_SCREENPIPE_LOG`，默认 /tmp/screenpipe-auto.log）——
    # 手动 ingest 失败后完整的 claude 输出都在这里（server/ingest_run 只回 400 字尾巴）；原生 IngestModel.revealIngestLog
    # 同一路径。server 起脚本时 env 原样继承（subproc.module_env），所以这里读同一个 env 两边永远一致。
    return Path(os.environ.get("PROCESS_SCREENPIPE_LOG") or "/tmp/screenpipe-auto.log")


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
