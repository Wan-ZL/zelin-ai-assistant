"""GET /api/slack/manifest 把「文件读出来只剩空白」当缺席（§15.3 v0.14 / §54.4；parity 批 catalog-help-copy）。

原生 SettingsSlack.swift copyManifest 的 guard 有两半：``try? String(contentsOfFile:)`` 失败 **或**
trimmed 为空 → 都报「找不到 <path>——repo 不完整？重装一次即可。」。server/slack_manifest.py 原本只映射了前半
（OSError → 404）；空白文件会 200 回一串空白，页面写进剪贴板还报「已复制 ✓」。这里钉住后半：空白 → 同一个
404 envelope（details.path 仍是 repo 相对路径，web 端 manifestErrorMessage 因此出同一句）。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from server import slack_manifest
from server.errors import NotFoundError


class SlackManifestEmptyFileTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-manifest-")
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "slack-app-manifest.json"
        patcher = mock.patch.object(slack_manifest, "manifest_path", lambda: self.file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _assert_missing(self):
        with self.assertRaises(NotFoundError) as ctx:
            slack_manifest.manifest(Path(self.tmp.name))
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.code, "NOT_FOUND")
        self.assertEqual(ctx.exception.details, {"path": str(slack_manifest.MANIFEST_REL)})

    def test_whitespace_only_file_is_404_like_a_missing_one(self):
        for body in ("", "   \n", "\n\t \r\n"):
            with self.subTest(body=repr(body)):
                self.file.write_text(body, encoding="utf-8")
                self._assert_missing()

    def test_missing_file_still_404_with_the_repo_relative_path(self):
        self.assertFalse(self.file.exists())
        self._assert_missing()

    def test_non_blank_file_is_returned_verbatim(self):
        self.file.write_text('{"display_information": {"name": "x"}}\n', encoding="utf-8")
        obj = slack_manifest.manifest(Path(self.tmp.name))
        self.assertEqual(obj, {"manifest": '{"display_information": {"name": "x"}}\n',
                               "path": str(slack_manifest.MANIFEST_REL)})


if __name__ == "__main__":
    unittest.main()
