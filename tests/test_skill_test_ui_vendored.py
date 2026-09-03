"""test-ui skill · 自包含判例：skills/test-ui/scripts/ladder_common_vendored.py 必须与
skills/test-code/scripts/ladder_common.py 逐字节相同（vendored copy——skill 经 ~/.claude/skills 软链接跨项目运行，
不能 import 姐妹 skill）；skill 脚本不 import act / server / scripts.qa；references/rules/*.json 可解析且形状对。

法典：docs/CONTRACT.md §58；设计 vnext2-plan R2.7.4 / R2.8。drift → 这里先红。
"""
import json
import os
import re
import unittest

from tests import skill_test_ui_testkit as kit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_UI = os.path.join(ROOT, "skills", "test-ui")


class VendoredTestCase(unittest.TestCase):
    def test_ladder_common_is_byte_identical(self):
        with open(os.path.join(ROOT, "skills", "test-code", "scripts", "ladder_common.py"), "rb") as fh:
            upstream = fh.read()
        with open(os.path.join(kit.SKILL_SCRIPTS, "ladder_common_vendored.py"), "rb") as fh:
            vendored = fh.read()
        self.assertEqual(vendored, upstream, "re-vendor: cp skills/test-code/scripts/ladder_common.py skills/test-ui/scripts/ladder_common_vendored.py")

    def test_scripts_are_self_contained(self):
        banned = re.compile(r"^\s*(?:from|import)\s+(act|server|scripts|qa_common|ui_common|checks|detect|run_ladder)\b", re.M)
        for name in sorted(os.listdir(kit.SKILL_SCRIPTS)):
            if name.endswith(".py"):
                with open(os.path.join(kit.SKILL_SCRIPTS, name), encoding="utf-8") as fh:
                    text = fh.read()
                self.assertIsNone(banned.search(text), "%s imports outside the skill" % name)
                self.assertIn("法典", text.split('"""')[1] if '"""' in text else "", "%s docstring lacks a law pointer" % name)

    def test_rule_tables_parse(self):
        for name in ("wcag.json", "tokens.json"):
            with open(os.path.join(TEST_UI, "references", "rules", name), encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertTrue(doc["rules"])
            for rule_id, rule in doc["rules"].items():
                self.assertIn(rule["severity"], ("critical", "serious", "moderate", "minor"), rule_id)

    def test_skill_md_frontmatter_and_length(self):
        with open(os.path.join(TEST_UI, "SKILL.md"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines[0], "---")
        head = "\n".join(lines[:12])
        self.assertIn("name: test-ui", head)
        self.assertIn("version: %s" % kit.tc.SKILL_VERSION, head)
        self.assertLessEqual(len(lines), 150, "SKILL.md must stay ≤ 150 lines")
        first60 = "\n".join(lines[:60])
        self.assertIn("ASK", first60)
        self.assertIn("Anti-gaming", first60)


if __name__ == "__main__":
    unittest.main()
