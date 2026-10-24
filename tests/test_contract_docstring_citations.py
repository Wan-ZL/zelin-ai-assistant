"""Surviving module docstrings never cite a tombstoned CONTRACT section (v2.0.0).

CLAUDE.md 防腐 #6: deleting a feature tombstones its CONTRACT section in the
same PR, and the section number is never reused. A module that survives the
deletion but still says "see §52" sends the reader to a one-line tombstone — or,
worse, to whatever the reader assumes §52 means. This gate parses the tombstone
headings out of docs/CONTRACT.md (``## N. … —（retired v2.0.0，全文见 tag full）`` and
the ``### N.M`` form) and scans every module docstring under act/ and server/:

- a ``§N`` / ``§N.M`` citation whose section (or whose parent section) is
  tombstoned fails, **unless** the same paragraph says so (``retired`` / ``退役`` /
  ``tombstone`` / ``tag full`` / ``v1`` / ``1.x``) — naming a retirement is how a
  surviving module explains a removed neighbour;
- ``BUILD-CONTRACT §N`` is a different document and is skipped.

Pure file reads; no subprocess.
"""
import ast
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONTRACT = REPO / "docs" / "CONTRACT.md"
TOMB_HEADING = re.compile(r"^(#{2,3}) (\d+(?:\.\d+)?)\.? .*（retired v2\.0\.0", re.M)
CITE = re.compile(r"§\s?(\d+(?:\.\d+)?)")
RETIRE_MARK = re.compile(r"retired|退役|tombstone|tag full|\bv1\b|1\.x", re.I)
SCANNED = ("act", "server")


def tombstoned() -> set:
    return {m.group(2) for m in TOMB_HEADING.finditer(CONTRACT.read_text(encoding="utf-8"))}


def violations(tombs: set) -> list:
    out = []
    for top in SCANNED:
        for path in sorted((REPO / top).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
            for para in re.split(r"\n\s*\n", doc):
                if RETIRE_MARK.search(para):
                    continue
                for m in CITE.finditer(para):
                    if para[max(0, m.start() - 15):m.start()].rstrip().endswith("BUILD-CONTRACT"):
                        continue
                    num = m.group(1)
                    if num in tombs or num.split(".")[0] in tombs:
                        out.append("%s cites §%s: %s" % (path.relative_to(REPO), num,
                                                         para.strip().splitlines()[0][:100]))
    return out


class DocstringCitationTestCase(unittest.TestCase):
    def test_the_tombstone_parser_sees_the_retired_sections(self):
        tombs = tombstoned()
        for num in ("1", "2", "3", "52", "65", "66", "56.3", "63.4", "68.7"):
            self.assertIn(num, tombs)
        for alive in ("0", "25", "26", "49", "59", "63", "78", "79", "11"):
            self.assertNotIn(alive, tombs)

    def test_no_surviving_module_docstring_cites_a_tombstone(self):
        self.assertEqual(violations(tombstoned()), [])

    def test_the_gate_catches_a_planted_citation(self):
        tombs = {"52"}
        para = "这里 §52 的 boardctl 是法定客户端"
        self.assertTrue(CITE.search(para))
        self.assertFalse(RETIRE_MARK.search(para))
        self.assertTrue(RETIRE_MARK.search("v1 §52 的 boardctl 随 Act 退役"))
        self.assertIn("52", tombs)


class ContractShapeTestCase(unittest.TestCase):
    """The v2.0.0 law additions this PR makes are present (header, art.13, §78, §79)."""

    def setUp(self):
        self.text = CONTRACT.read_text(encoding="utf-8")

    def test_the_constitution_has_thirteen_articles_and_art_12_is_a_tombstone(self):
        sec0 = self.text.split("\n## 1. ", 1)[0]
        nums = re.findall(r"^(\d+)\. \*\*", sec0, re.M)
        self.assertEqual(nums, [str(n) for n in range(1, 14)])
        self.assertRegex(sec0, r"12\. \*\*审批边界\*\* —（retired v2\.0\.0")
        self.assertIn("13. **自成一块**", sec0)

    def test_new_sections_and_tombstone_form(self):
        self.assertRegex(self.text, r"\n## 78\. 自成一块")
        self.assertRegex(self.text, r"\n## 79\. lite 升级")
        self.assertIn("全文见 tag full", self.text.split("\n## 0. ", 1)[0])
        for line in self.text.splitlines():
            if "retired v2.0.0，全文见 tag full" in line and line.startswith("## "):
                self.assertRegex(line, r"^## \d+\. .+ —（retired v2\.0\.0，全文见 tag full）$")

    def test_section_numbers_are_unique(self):
        nums = re.findall(r"^## (\d+)\. ", self.text, re.M)
        self.assertEqual(len(nums), len(set(nums)))
        self.assertNotIn("11", nums)   # §11 stays intentionally empty


if __name__ == "__main__":
    unittest.main()
