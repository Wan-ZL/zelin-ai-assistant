"""server/security.py token 文件加固的失败路径判例（CONTRACT §49 auth model）。

test_server_auth 钉了三条主线（0600 重铸、坏内容重铸、symlink 拒跟随）；这里
补此前无判例的失败分支：fchmod 收不回 → 弃用重铸且 fd 亲手关；fstat 抛
OSError → None 且 fd 亲手关；fdopen 前抛错也不漏 fd；打不开 → -1。POSIX
only（Windows 合成 mode 位，_reharden 直接放行——单独钉一条）。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401

from server import security

_POSIX = os.name == "posix"


class _Home(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-tok-edge-"))
        self.p = security.token_path(self.home)
        self.p.parent.mkdir(parents=True)
        self.p.write_text("abcDEF_-123\n", encoding="utf-8")
        os.chmod(self.p, 0o644)


@unittest.skipUnless(_POSIX, "mode bits are a POSIX concern")
class RehardenFailureTestCase(_Home):
    def test_fchmod_failure_discards_and_closes_fd(self):
        closed = []
        real_close = os.close

        def spy_close(fd):
            closed.append(fd)
            real_close(fd)

        with mock.patch.object(os, "fchmod", side_effect=OSError("ro fs")), \
                mock.patch.object(os, "close", spy_close):
            self.assertIsNone(security._read_token_hardened(self.p))
        self.assertEqual(len(closed), 1)

    def test_fstat_oserror_is_none_and_closes_fd(self):
        closed = []
        real_close = os.close

        def spy_close(fd):
            closed.append(fd)
            real_close(fd)

        with mock.patch.object(os, "fstat", side_effect=OSError("gone")), \
                mock.patch.object(os, "close", spy_close):
            self.assertIsNone(security._read_token_hardened(self.p))
        self.assertEqual(len(closed), 1)

    def test_reminted_token_after_discard(self):
        with mock.patch.object(os, "fchmod", side_effect=OSError("ro fs")):
            tok = security.load_or_create_token(self.home)
        self.assertNotEqual(tok, "abcDEF_-123")
        self.assertTrue(security._valid_token(tok))

    def test_close_failure_is_swallowed(self):
        with mock.patch.object(os, "close", side_effect=OSError("already closed")):
            security._close_quietly(999)   # 不抛


class OpenNofollowTestCase(_Home):
    def test_missing_file_is_minus_one(self):
        self.assertEqual(security._open_nofollow(self.home / "nope"), -1)

    def test_symlink_is_minus_one(self):
        link = self.home / "link"
        try:
            link.symlink_to(self.p)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertEqual(security._open_nofollow(link), -1)

    def test_regular_file_opens_and_reads(self):
        fd = security._open_nofollow(self.p)
        self.assertGreaterEqual(fd, 0)
        self.assertEqual(security._read_owned_fd(fd), "abcDEF_-123")


class RehardenPlatformTestCase(unittest.TestCase):
    def test_non_posix_always_passes(self):
        with mock.patch.object(os, "name", "nt"):
            self.assertTrue(security._reharden(-1))


if __name__ == "__main__":
    unittest.main()
