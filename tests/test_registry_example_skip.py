"""Registry loader robustness.

- The shipped R-000-example.yaml is documentation, never a real card (it used
  to surface in the backlog lane on every fresh install).
- A single unreadable/corrupt card file is skipped with a log — one bad file
  must never take down load_all and every consumer behind it (dashboard,
  inbox, radars, quick capture). 单点损坏不倒全局。
- Hand-written YAML with unquoted numeric id/title/tier normalizes to str.

registry reads config.REGISTRY_DIR at call time, so a patch.object is enough —
no env mutation, no module reload (an earlier version reloaded act.lib.config
and leaked a dead HOME into every later test module in the suite).
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config, registry


class ExampleCardNeverLoadsTestCase(unittest.TestCase):
    def test_example_file_excluded_from_load_all(self):
        with tempfile.TemporaryDirectory(dir=TMP_HOME) as tmp:
            reg = Path(tmp) / "registry"
            reg.mkdir()
            (reg / "R-000-example.yaml").write_text(
                "id: R-000\ntitle: example\nstatus: detected\n", encoding="utf-8")
            (reg / "R-001.yaml").write_text(
                "id: R-001\ntitle: real\nstatus: detected\n", encoding="utf-8")
            with mock.patch.object(config, "REGISTRY_DIR", reg):
                ids = [r.id for r in registry.load_all()]
            self.assertIn("R-001", ids)
            self.assertNotIn("R-000", ids)


class UnreadableCardSkippedTestCase(unittest.TestCase):
    def test_permission_denied_file_skipped_rest_survive(self):
        if os.geteuid() == 0:
            self.skipTest("chmod 000 is not effective when running as root")
        with tempfile.TemporaryDirectory(dir=TMP_HOME) as tmp:
            reg = Path(tmp) / "registry"
            reg.mkdir()
            (reg / "R-001.yaml").write_text(
                "id: R-001\ntitle: readable\nstatus: detected\n",
                encoding="utf-8")
            locked = reg / "R-105.yaml"
            locked.write_text(
                "id: R-105\ntitle: locked\nstatus: detected\n",
                encoding="utf-8")
            locked.chmod(0)
            try:
                with mock.patch.object(config, "REGISTRY_DIR", reg):
                    ids = [r.id for r in registry.load_all()]
            finally:
                locked.chmod(0o644)   # so the tempdir cleanup can delete it
            # 不可读文件 = 跳过 + log（同坏 YAML 的既有处理），其余卡照常
            self.assertEqual(ids, ["R-001"])


class NumericYamlFieldsNormalizeTestCase(unittest.TestCase):
    def test_from_dict_coerces_id_title_tier_to_str(self):
        r = registry.Requirement.from_dict({"id": 4, "title": 456, "tier": 7})
        self.assertEqual((r.id, r.title, r.tier), ("4", "456", "7"))

    def test_next_id_survives_legacy_int_id_card(self):
        with tempfile.TemporaryDirectory(dir=TMP_HOME) as tmp:
            reg = Path(tmp) / "registry"
            reg.mkdir()
            # 手写卡的无引号数字：PyYAML 会解析成 int
            (reg / "R-004.yaml").write_text(
                "id: 4\ntitle: 456\ntier: 7\nstatus: card_sent\n",
                encoding="utf-8")
            # ARCHIVE_DIR 是 import 时算好的常量，得一起指进沙箱，否则
            # next_id(include_archived=True) 会捡到别的测试留下的归档卡
            with mock.patch.object(config, "REGISTRY_DIR", reg), \
                    mock.patch.object(registry, "ARCHIVE_DIR", reg / "archive"):
                # 曾经：_ID_RE.match(int) -> TypeError，所有新建卡的入口全瘫。
                # audit finding 3: 文件名 R-004.yaml 占号 —— 重发 R-004 会让
                # save() 覆盖这张 legacy 卡（静默数据丢失），所以是 R-005。
                self.assertEqual(registry.next_id(), "R-005")
                loaded = registry.load_all()
            self.assertEqual([r.id for r in loaded], ["4"])


if __name__ == "__main__":
    unittest.main()
