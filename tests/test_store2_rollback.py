"""store2 回滚开关判例（CONTRACT §53.6 / R2.1.3；docs/TROUBLESHOOTING.md）。

回滚 = 停守护 → 恢复 state/backups/registry-<ts>/ → config `registry.backend:
yaml`（或 env）→ 重启。开关保留一个版本：强制 yaml 时激活标记被无视、tick
永不迁移、写回 YAML 文件——整条链在此钉死。
"""
import os
import shutil
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports
from tests import store2_testkit

from act.lib import config, registry
from act.lib.store2 import activate
from tests.test_store2_activation import _seed


class RollbackSwitchTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "auto")

    def _activate(self):
        _seed("R-001")
        _seed("R-002", title="第二张")
        activate.tick()
        self.assertEqual(registry.backend(), registry.BACKEND_SQLITE)

    def test_env_switch_returns_to_yaml_despite_marker(self):
        self._activate()
        os.environ["ZAI_REGISTRY_BACKEND"] = "yaml"
        registry.reset_store_cache()
        try:
            self.assertEqual(registry.backend(), registry.BACKEND_YAML)
            self.assertEqual(activate.status()["state"], "yaml_forced")
            # 读写回到 YAML 文件（live 目录在激活时未被清空——回滚可用）
            self.assertEqual(sorted(r.id for r in registry.load_all()),
                             ["R-001", "R-002"])
            with registry.acting_as("user"):
                registry.trash(registry.load("R-001"), "deleted")
            self.assertEqual(registry.load("R-001").status, "trashed")
            # 强制期 tick 永不迁移/导出
            self.assertEqual(activate.tick(), [])
        finally:
            os.environ.pop("ZAI_REGISTRY_BACKEND", None)
            registry.reset_store_cache()

    def test_config_switch_returns_to_yaml(self):
        self._activate()
        os.environ.pop("ZAI_REGISTRY_BACKEND", None)
        config.CONFIG_PATH.write_text("registry:\n  backend: yaml\n",
                                      encoding="utf-8")
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        registry.reset_store_cache()   # rollback 口径：改 config 后重启守护
        self.assertEqual(registry.backend(), registry.BACKEND_YAML)
        self.assertEqual(activate.status()["state"], "yaml_forced")

    def test_restore_from_backup_round_trips(self):
        self._activate()
        # 模拟文档步骤：把备份目录恢复回 act/registry/（覆盖式）
        backup = sorted(d for d in registry.registry_backups_dir()
                        .glob("registry-*") if d.is_dir())[0]
        shutil.rmtree(config.REGISTRY_DIR)
        shutil.copytree(backup, config.REGISTRY_DIR)
        os.environ["ZAI_REGISTRY_BACKEND"] = "yaml"
        registry.reset_store_cache()
        try:
            self.assertEqual(sorted(r.id for r in registry.load_all()),
                             ["R-001", "R-002"])
        finally:
            os.environ.pop("ZAI_REGISTRY_BACKEND", None)
            registry.reset_store_cache()


if __name__ == "__main__":
    unittest.main()
