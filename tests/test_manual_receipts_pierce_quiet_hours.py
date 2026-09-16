"""§28 追记（issue #29）：你按下的按钮一定会回话——手动运行的回执穿透安静时段。

设置 · 每周摘要的「现在生成一份」按下去是**分离**的（actd `_spawn_weekly_digest`），
按钮自己的回执句逐字镜像原生：「已请求生成——完成后会弹通知，摘要出现在「待验收」。」。
`act/weekly_digest --now` 的三条出口（已生成 / 没有数据 / 生成失败）就是那条承诺的兑现，
而**失败**那条路上一张卡都不铸——安静时段把它吃掉，人只会以为按钮坏了。

钉的行为：
  - 三条手动回执打 `notify.KIND_RECEIPT`，安静窗正中照样入队；
  - `receipt` 没有分类开关（不在 `CATEGORY_PREFERENCE` 里），`notify_failures`
    关掉也静不了它——那把开关管的是守护进程自己发起的告警；
  - **排定**的那次成功通知仍无 kind，安静窗里照旧被吃掉（凌晨没人在等它）；
  - 新 kind 同 PR 登记进 `server/notify_catalog.KINDS`（§66.2 探针的判卷面）；
  - 目录 help 是纯文本：设置页把它渲成一个文本节点，Markdown `**` 会被逐字看见。
"""
import contextlib
import io
import time
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import weekly_digest
from act.lib import config, notify
from server import notify_catalog, settings_catalog


def _cfg(**kw) -> config.Config:
    cfg = config.Config()
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


def _at(hour: int, minute: int = 0) -> time.struct_time:
    return time.struct_time((2026, 9, 12, hour, minute, 0, 5, 255, -1))


# 出厂安静窗（22:00 → 08:00）正中的一次按键。
_QUIET = _cfg(quiet_hours_enabled=True)


class ReceiptPiercesQuietHoursTestCase(unittest.TestCase):
    def test_receipt_is_exempt_from_quiet_hours_at_any_hour_in_the_window(self):
        for hour in (22, 23, 0, 3, 7):
            with self.subTest(hour=hour):
                self.assertIsNone(notify.suppression_reason(
                    notify.KIND_RECEIPT, _QUIET, now=_at(hour)))
        # 对照：无 kind 的同一时刻是被吃掉的——豁免是 receipt 自己挣来的
        self.assertEqual(notify.suppression_reason(None, _QUIET, now=_at(3)),
                         "quiet_hours")

    def test_receipt_has_no_category_switch_of_its_own(self):
        """按了就一定响：`notify_failures` 关掉也静不了一次按键的回音。"""
        self.assertNotIn(notify.KIND_RECEIPT, notify.CATEGORY_PREFERENCE)
        cfg = _cfg(quiet_hours_enabled=True, notify_failures=False,
                   notify_proposals=False, notify_needs_input=False)
        self.assertIsNone(notify.suppression_reason(
            notify.KIND_RECEIPT, cfg, now=_at(3)))


class WeeklyDigestReceiptsTestCase(unittest.TestCase):
    """三条出口真打上了那个 kind（写方半边；抑制逻辑的格子住 test_notify_preferences）。"""

    def setUp(self):
        patcher = mock.patch.object(weekly_digest.notify, "notify", return_value=True)
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)
        # 两条路都写一行日志（_skip）；判的是 kind，别把它吐进测试输出
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.cfg = config.Config()

    def _kinds(self):
        return [call.kwargs.get("kind") for call in self.notify.call_args_list]

    def test_no_data_receipt_is_tagged(self):
        weekly_digest._no_data(self.cfg, True, {})
        self.assertEqual(self._kinds(), [notify.KIND_RECEIPT])

    def test_failure_receipt_is_tagged(self):
        """失败那条路**不铸卡**——回执被静音等于按下去什么都没发生。"""
        summary = weekly_digest._fail({}, self.cfg, True, "claude_failed",
                                      "OSError: boom", "AI 调用失败",
                                      "the AI call failed")
        self.assertIs(summary["ok"], False)
        self.assertEqual(self._kinds(), [notify.KIND_RECEIPT])

    def test_a_scheduled_run_notifies_nothing(self):
        """没按按钮就没有回执（force=False 两条路一条都不弹）。"""
        weekly_digest._no_data(self.cfg, False, {})
        weekly_digest._fail({}, self.cfg, False, "unparseable", "x", "坏", "bad")
        self.assertEqual(self.notify.call_args_list, [])


class CatalogRegistrationTestCase(unittest.TestCase):
    def test_receipt_is_in_the_kind_vocabulary_without_a_preference(self):
        entry = next(k for k in notify_catalog.KINDS
                     if k["kind"] == notify.KIND_RECEIPT)
        self.assertIsNone(entry["preference"])
        for half in ("title", "help"):
            self.assertTrue(entry[half]["zh"] and entry[half]["en"], half)

    def test_catalog_help_is_plain_text_not_markdown(self):
        """设置页把 help 渲成一个文本节点（FieldControl 的 settings-helper），
        Markdown 的 `**加粗**` 会被用户逐字看见——目录里一个星号都不许有。"""
        for section in settings_catalog.SECTIONS:
            for lang, text in section["help"].items():
                with self.subTest(section=section["id"], lang=lang):
                    self.assertNotIn("**", text)
            for field in section["fields"]:
                for lang, text in field["help"].items():
                    with self.subTest(field=field["key"], lang=lang):
                        self.assertNotIn("**", text)
        for kind in notify_catalog.KINDS:
            for lang, text in kind["help"].items():
                with self.subTest(kind=kind["kind"], lang=lang):
                    self.assertNotIn("**", text)


if __name__ == "__main__":
    unittest.main()
