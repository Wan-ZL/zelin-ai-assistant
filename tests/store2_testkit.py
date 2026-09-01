"""store2 行为测试的共享夹具（CONTRACT §53）——不是测试文件。

套件级沙箱（tests/__init__.py）把整个进程钉在一个 AIASSISTANT_HOME 里并强制
ZAI_REGISTRY_BACKEND=yaml；本 kit 提供「切后端 + 清数据层 + 用完复原」的统一
姿势，让 store2 行为测试互不串味、也不污染其余 2400+ 条 yaml 测试。
"""
from __future__ import annotations

import os
import shutil

from act.lib import config, registry


def wipe_data_layer() -> None:
    """清空两个后端的全部数据面（卡文件 / DB / 标记 / 备份 / 导出 / 台账）。

    先 reset_store_cache 再删文件——Windows 对**打开中的** DB 文件 unlink 抛
    PermissionError（POSIX 才允许删打开的文件），缓存连接必须先关。"""
    registry.reset_store_cache()
    config.ensure_state_dirs()
    if config.REGISTRY_DIR.exists():
        shutil.rmtree(config.REGISTRY_DIR)
    config.REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    for name in (registry.STORE2_DB_NAME, registry.STORE2_DB_NAME + "-wal",
                 registry.STORE2_DB_NAME + "-shm", registry.STORE2_TRUTH_NAME,
                 registry.STORE2_ACTIVATION_NAME, "registry_export.json",
                 "registry_writes.jsonl"):
        p = config.STATE_DIR / name
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    for d in (registry.registry_backups_dir(), registry.registry_export_dir()):
        if d.exists():
            shutil.rmtree(d)
    registry.reset_store_cache()


def use_backend(testcase, backend: str) -> None:
    """本条测试用指定后端（yaml|sqlite|auto）；addCleanup 复原套件默认。

    sqlite = 强制 + 预建空库（绕过激活协议——激活协议自有专门判例）；
    auto = 摘掉套件的强制 env，走真实的「标记在=sqlite」判定（激活测试用）。
    """
    prev = os.environ.get("ZAI_REGISTRY_BACKEND")

    def _restore():
        if prev is None:
            os.environ.pop("ZAI_REGISTRY_BACKEND", None)
        else:
            os.environ["ZAI_REGISTRY_BACKEND"] = prev
        wipe_data_layer()

    testcase.addCleanup(_restore)
    wipe_data_layer()
    if backend == "auto":
        os.environ.pop("ZAI_REGISTRY_BACKEND", None)
    else:
        os.environ["ZAI_REGISTRY_BACKEND"] = backend
    registry.reset_store_cache()
    if backend == "sqlite":
        from act.lib.store2.store import Store
        Store(registry.store2_db_path()).close()
