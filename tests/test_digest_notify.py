"""act/digest.py — 周一 digest 通知走 §5 v0.14 契约：python 侧全部通知经
act/lib/failures.pick 走 §15 的 UI 语言设置，且每条 body 必带下一步动作。
（回归：English UI 用户每周一收到中文标题 + 纯路径 body 的通知。）

Everything lives under the sandbox AIASSISTANT_HOME set in tests/__init__.py;
build_digest / oneonone 被 mock 掉，不触 registry / workbench / analytics 报告。
"""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import digest
from act.lib import config

_CJK = re.compile(r"[一-鿿]")


class DigestNotifyTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self._cleanup()
        self.addCleanup(self._cleanup)
        self.tmp = tempfile.TemporaryDirectory(prefix="digest-out-")
        self.addCleanup(self.tmp.cleanup)
        patchers = [
            mock.patch.object(digest, "DIGESTS_DIR",
                              Path(self.tmp.name) / "digests"),
            mock.patch.object(digest, "build_digest", return_value="# d\n"),
            mock.patch.object(digest.oneonone, "write_prep",
                              side_effect=RuntimeError("skip prep")),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        notify_patch = mock.patch.object(digest.notify, "notify",
                                         return_value=True)
        self.notify = notify_patch.start()
        self.addCleanup(notify_patch.stop)

    @staticmethod
    def _cleanup():
        if config.SETTINGS_OVERRIDES_PATH.exists():
            config.SETTINGS_OVERRIDES_PATH.unlink()

    def _set_lang(self, lang: str) -> None:
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"language": lang}), encoding="utf-8")

    def test_en_ui_gets_english_title_and_next_step(self):
        self._set_lang("en")
        path = digest.write_digest()
        (title, body) = self.notify.call_args[0][:2]
        self.assertEqual(title, "Monday digest ready")
        self.assertIsNone(_CJK.search(title))   # 英文 UI 不夹中文标题
        self.assertIsNone(_CJK.search(body))
        self.assertIn(str(path), body)          # 指到 digest 文件…
        self.assertIn("Open", body)             # …并带下一步动作

    def test_zh_default_keeps_chinese_copy_with_next_step(self):
        path = digest.write_digest()
        (title, body) = self.notify.call_args[0][:2]
        self.assertEqual(title, "周一 digest 已生成")
        self.assertIn(str(path), body)
        self.assertIn("打开", body)             # body 不再是纯路径


if __name__ == "__main__":
    unittest.main()
