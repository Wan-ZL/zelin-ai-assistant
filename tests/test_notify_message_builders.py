"""§5 notification builders — every ``notify.msg_*`` returns a real
``(title, body)`` pair in both UI languages, the copy names the card / source / count
it is about, and the auth classifier's negative answer is a real ``False``.

Nightly mutants (2026-09-02) had seven builders whose ``return`` could be
swapped for ``return None`` unnoticed because only a handful of builders were
ever called by a test; the §5 promise is uniform, so the pin is uniform: one
table, both languages, structural assertions per builder. ``detect_auth_failure``
on empty text is pinned with ``assertIs`` — actd branches on it.
"""
import inspect
import re
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import failures, notify

_CJK = re.compile(r"[一-鿿]")

# builder → sample args; the marker is the card/source/count the copy must carry
_SAMPLES = {
    "msg_new_card": (("整理推荐信",), "整理推荐信"),
    "msg_new_cards_batch": ((4,), "4"),
    "msg_registry_guard": (("整理推荐信", "R-001.yaml, R-002.yaml"), "R-002.yaml"),
    "msg_done": (("整理推荐信",), "整理推荐信"),
    "msg_review_interrupted": (("整理推荐信",), "整理推荐信"),
    "msg_radar_dead": (("gmail", 26), "26"),
    "msg_auth": (("Slack",), "Slack"),
    "msg_reraised": (("整理推荐信", "新邮件"), "新邮件"),
    "msg_review_ready": (("整理推荐信",), "整理推荐信"),
    "msg_dispatch_failed": (("整理推荐信", "claude 没装好"), "claude 没装好"),
    "msg_dispatch_halted": (("整理推荐信", 5, "claude 没装好"), "5"),
    "msg_resuming": (("整理推荐信",), "整理推荐信"),
    "msg_auto_resume_exhausted": (("整理推荐信",), "整理推荐信"),
    "msg_resume_storm": (("整理推荐信", 3), "3"),
    "msg_stop_failed": (("整理推荐信",), "整理推荐信"),
}


class BuilderTableTestCase(unittest.TestCase):
    def test_every_builder_is_in_the_table(self):
        builders = {n for n, f in inspect.getmembers(notify, inspect.isfunction)
                    if n.startswith("msg_")}
        self.assertEqual(builders, set(_SAMPLES))

    def _check_lang(self, lang):
        with mock.patch.object(failures, "ui_lang", return_value=lang):
            for name, (args, marker) in _SAMPLES.items():
                with self.subTest(builder=name, lang=lang):
                    out = getattr(notify, name)(*args)
                    self.assertIsInstance(out, tuple)
                    self.assertEqual(len(out), 2)
                    title, body = out
                    self.assertTrue(title.strip() and body.strip())
                    self.assertIn(str(marker), title + body)
                    has_cjk = bool(_CJK.search(title))
                    self.assertEqual(has_cjk, lang == "zh", (name, title))

    def test_builders_answer_in_zh(self):
        self._check_lang("zh")

    def test_builders_answer_in_en(self):
        self._check_lang("en")

    def test_optional_reason_variants(self):
        with mock.patch.object(failures, "ui_lang", return_value="zh"):
            _t, plain = notify.msg_dispatch_failed("卡")
            self.assertIn("错误提示", plain)
            _t, with_reason = notify.msg_dispatch_failed("卡", "原因句")
            self.assertIn("卡：原因句", with_reason)
            _t, halted = notify.msg_dispatch_halted("卡", 5)
            self.assertNotIn("：", halted.split(" —— ")[0])
            _t, plain_reraise = notify.msg_reraised("卡")
            self.assertTrue(plain_reraise.startswith("卡 —— "))
            _t, unknown_source = notify.msg_radar_dead("imessage", 3)
            self.assertIn("imessage", unknown_source)
        with mock.patch.object(failures, "ui_lang", return_value="en"):
            _t, halted_en = notify.msg_dispatch_halted("card", 5, "why")
            self.assertTrue(halted_en.startswith("card: why — "))


class AuthClassifierTestCase(unittest.TestCase):
    def test_empty_text_is_a_real_false(self):
        self.assertIs(notify.detect_auth_failure(""), False)
        self.assertIs(notify.detect_auth_failure(None), False)

    def test_verdicts_are_booleans(self):
        self.assertIs(notify.detect_auth_failure("please run /login"), True)
        self.assertIs(notify.detect_auth_failure("all good"), False)


if __name__ == "__main__":
    unittest.main()
