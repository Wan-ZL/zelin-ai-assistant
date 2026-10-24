"""§26 追记（2026-10-23，lite 2.0 之前的桥接版本 PR #480；v2.0.0 保留）：``manual_upgrade`` 标记。

最新 release 的 MAJOR 大于运行版本 → ``manual_upgrade: true``（add-only），出现在
``check()`` 的答案、``update_available`` 投影（v2.0.0 起只剩 server/about.py 现算的那份，判例在
tests/test_server_about_upgrade.py）与
CLI payload 里——升级永不自己跨大版本（v2.0.0 起由 §79 升级器与 ``POST /api/update/install``
的 409 执行），页面要说的是「手动升级」而不是「一键更新」。同 major 的新版 / 旧版 /
垃圾版本号 → false。fetch 全部注入，不出网。
"""
import datetime as dt
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config, update_check as uc


def _cfg(enabled: bool = True) -> config.Config:
    c = config.Config()
    c.updates_check_enabled = enabled
    return c


def _fetch(tag):
    def fetch(_etag):
        return 200, 'W/"e1"', {"tag_name": tag, "html_url": "https://example.com/" + tag, "assets": []}
    return fetch


class NeedsManualUpgradeTestCase(unittest.TestCase):
    def test_major_greater_is_manual(self):
        self.assertTrue(uc.needs_manual_upgrade("2.0.0", "1.0.117"))
        self.assertTrue(uc.needs_manual_upgrade("v2.0.0", "1.0.117+3"))
        self.assertTrue(uc.needs_manual_upgrade("2.0.0-rc.1", "1.9.9"))
        self.assertTrue(uc.needs_manual_upgrade("10.0.0", "9.99.99"))

    def test_same_or_lower_major_is_not(self):
        self.assertFalse(uc.needs_manual_upgrade("1.0.118", "1.0.117"))
        self.assertFalse(uc.needs_manual_upgrade("1.5.0", "1.0.117"))
        self.assertFalse(uc.needs_manual_upgrade("2.0.1", "2.0.0"))
        self.assertFalse(uc.needs_manual_upgrade("1.0.0", "2.0.0"))

    def test_garbage_is_not(self):
        for latest, current in (("", "1.0.0"), (None, "1.0.0"), ("2.0.0", "unknown"), ("main", "1.0.0")):
            self.assertFalse(uc.needs_manual_upgrade(latest, current), (latest, current))


class PayloadTestCase(unittest.TestCase):
    def setUp(self):
        if uc.STATE_PATH.exists():
            uc.STATE_PATH.unlink()
        self.addCleanup(lambda: uc.STATE_PATH.exists() and uc.STATE_PATH.unlink())
        self.t0 = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc)
        patcher = mock.patch.object(uc, "__version__", "1.0.117")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_new_major_flags_every_payload(self):
        info = uc.check(_cfg(), fetch=_fetch("v2.0.0"), now=self.t0)
        self.assertIs(info["manual_upgrade"], True)
        self.assertEqual(info["latest"], "2.0.0")
        out = uc.cli_status(cfg=_cfg(), now=self.t0)
        self.assertIs(out["update_available"], True)
        self.assertIs(out["manual_upgrade"], True)

    def test_patch_release_is_a_plain_update(self):
        info = uc.check(_cfg(), fetch=_fetch("v1.0.118"), now=self.t0)
        self.assertIs(info["manual_upgrade"], False)
        self.assertIs(uc.cli_status(cfg=_cfg(), now=self.t0)["manual_upgrade"], False)

    def test_disabled_check_never_claims_a_manual_upgrade(self):
        uc.check(_cfg(), fetch=_fetch("v2.0.0"), now=self.t0)
        out = uc.cli_status(cfg=_cfg(enabled=False), now=self.t0)
        self.assertIs(out["manual_upgrade"], False)
        self.assertIs(out["update_available"], False)


if __name__ == "__main__":
    unittest.main()
