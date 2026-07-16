"""act/digest.py — 周一 digest 通知走 §5 v0.14 契约：python 侧全部通知经
act/lib/failures.pick 走 §15 的 UI 语言设置，且每条 body 必带下一步动作。
（回归：English UI 用户每周一收到中文标题 + 纯路径 body 的通知。）

§40.7 起 digest 落卡而非落盘——通知不再携带文件路径，body 指向待验收列。

Everything lives under the sandbox AIASSISTANT_HOME set in tests/__init__.py;
build_digest / oneonone / _file_digest_card 被 mock 掉，不触 registry /
workbench / analytics 报告。
"""
import json
import os
import re
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import digest
from act.lib import config
from act.lib.registry import Requirement

_CJK = re.compile(r"[一-鿿]")


class DigestNotifyTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self._cleanup()
        self.addCleanup(self._cleanup)
        dummy = Requirement(id="R-999", title="digest", status="review")
        patchers = [
            mock.patch.object(digest, "build_digest", return_value="# d\n"),
            mock.patch.object(digest, "_file_digest_card", return_value=dummy),
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
        card = digest.publish_digest()
        self.assertEqual(card.id, "R-999")
        (title, body) = self.notify.call_args[0][:2]
        self.assertEqual(title, "Monday digest ready")
        self.assertIsNone(_CJK.search(title))   # 英文 UI 不夹中文标题
        self.assertIsNone(_CJK.search(body))
        self.assertIn("Review", body)           # 下一步：去待验收列
        self.assertNotIn("/", body)             # §40.7 不再携带文件路径

    def test_zh_locale_fallback_keeps_chinese_copy_with_next_step(self):
        # v0.42 §15: with nothing persisted the language follows the system
        # locale (was: hardcoded zh) — pin a zh locale to exercise that path.
        env = {k: v for k, v in os.environ.items()
               if k not in ("AIASSISTANT_UI_LANG", "LC_ALL")}
        env["LANG"] = "zh_CN.UTF-8"
        with mock.patch.dict(os.environ, env, clear=True):
            digest.publish_digest()
        (title, body) = self.notify.call_args[0][:2]
        self.assertEqual(title, "周一 digest 已生成")
        self.assertIn("待验收", body)           # body 带下一步动作
        self.assertNotIn("/", body)             # 不再是路径


if __name__ == "__main__":
    unittest.main()
