"""server/paths.py 的布局镜像 drift-pin（BUILD-CONTRACT §2.3；CONTRACT §44）。

server/ **绝不 import act**（单写者纪律：act.lib.config import 期就带写路径），
所以 paths.py 手抄了 act/lib/config.py 的默认 HOME 与五个只读路径布局。生产侧
不能 import，**测试侧可以**——这里就是那道 pin：任何一方改了默认路径或目录
布局而另一方没跟上，本文件立刻红（否则症状是 server 静默读一个空目录/读不到
看板，而两边代码各自看着都对）。
"""
import os
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env 先于任何 act.* import

from act.lib import config, registry
from server import paths

HOME = Path("/tmp/zai-paths-pin")


class DefaultHomeMirrorTestCase(unittest.TestCase):
    """AIASSISTANT_HOME 缺省时两侧必须落在同一个目录。"""

    def test_default_home_literal_matches_config(self):
        env = {k: v for k, v in os.environ.items() if k != "AIASSISTANT_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(paths.home_dir(), config._home())
            self.assertEqual(Path(paths.DEFAULT_HOME).expanduser(),
                             config._home())

    def test_env_override_agrees_with_config(self):
        with mock.patch.dict(os.environ, {"AIASSISTANT_HOME": str(HOME)}):
            self.assertEqual(paths.home_dir(), config._home())


class LayoutMirrorTestCase(unittest.TestCase):
    """五个只读路径的 HOME-相对布局与 config/registry 逐字一致。"""

    def _rel(self, absolute: Path) -> Path:
        """config 的常量是 import 期算的（HOME = 沙箱 TMP_HOME）——取相对形
        才能与 paths.* 的显式 home 参数比。"""
        return absolute.relative_to(config.HOME)

    def test_dashboard_registry_inbox_layout_matches_config(self):
        cases = (
            (paths.dashboard_path(HOME), config.DASHBOARD_PATH),
            (paths.registry_dir(HOME), config.REGISTRY_DIR),
            (paths.inbox_dir(HOME), config.INBOX_DIR),
        )
        for got, expected in cases:
            self.assertEqual(got, HOME / self._rel(expected))

    def test_archive_dir_matches_registry_constant(self):
        self.assertEqual(paths.archive_dir(HOME),
                         HOME / self._rel(registry.ARCHIVE_DIR))


if __name__ == "__main__":
    unittest.main()
