"""skip_reason 词表的跨层镜像（CONTRACT §48.4 / §48.7 追记 / §14bis）。

act/lib/radar_health.SKIP_REASON_CODES 是 skip_reason 的闭集真源（§48.4：词表外任意串出机前折叠为
一个兜底码）；web/src/components/settings/sourceHealth.tsx 的 ``skipReasonLabel`` 是同一词表在设置页
运行状态行 / 接入页 HealthLine 上的人话（§48.7 追记：覆盖词表**全员**；§14bis：抓取命令的失败在设置页
说成大白话）。两层各自的判例只看自己那层——Python 加一个码、web 不加句，所有门照旧全绿而机器码原样
上屏。本文件把「全员」钉成机器可查（先例 tests/test_shell_engine_mirror.py 读 shellBridge.ts 钉 wire
词表，防腐 #10）：

- Python 每个码 + ``public_skip_reason`` 的折叠码，tsx 词表里都要有一行；
- tsx 词表不许有 Python 不产出的码（发明的码 = 永远不会亮的死句）；
- 每行是 ``[zh, en]`` 两句：非空、彼此不同、也不等于码本身（只有词表外的未知码才原样透传）。
"""
import re
import unittest
from pathlib import Path

from act.lib import radar_health

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_HEALTH_TSX = REPO_ROOT / "web" / "src" / "components" / "settings" / "sourceHealth.tsx"

# 折叠码从函数本身推出（§48.4：词表外任意串 → 这一个码），不手抄字面量
FOLD_CODE = radar_health.public_skip_reason("not-a-vocabulary-code")
WIRE_CODES = frozenset(radar_health.SKIP_REASON_CODES) | {FOLD_CODE}

# 一行一码：`    code: ["zh", "en"],`
_ENTRY = re.compile(r'^\s+([a-z_]+): \["((?:[^"\\]|\\.)*)", "((?:[^"\\]|\\.)*)"\],?$', re.MULTILINE)


def _web_table() -> dict:
    src = SOURCE_HEALTH_TSX.read_text(encoding="utf-8")
    start = src.index("export function skipReasonLabel(")
    body = src[start:src.index("\n}\n", start)]
    return {m.group(1): (m.group(2).replace('\\"', '"'), m.group(3).replace('\\"', '"'))
            for m in _ENTRY.finditer(body)}


class SkipReasonVocabularyMirrorTestCase(unittest.TestCase):
    def setUp(self):
        self.table = _web_table()

    def test_parser_finds_the_web_table(self):
        self.assertTrue(self.table,
                        "no `code: [\"zh\", \"en\"]` rows found in skipReasonLabel — the tsx table "
                        "format drifted from what this mirror parses; fix the regex, not the invariant")

    def test_fold_code_is_a_bare_vocabulary_word(self):
        self.assertIsInstance(FOLD_CODE, str)
        self.assertRegex(FOLD_CODE, r"^[a-z_]+$")
        self.assertNotIn(FOLD_CODE, radar_health.SKIP_REASON_CODES)

    def test_every_wire_code_has_a_web_sentence(self):
        for code in sorted(WIRE_CODES):
            with self.subTest(code=code):
                self.assertIn(
                    code, self.table,
                    "act/lib/radar_health emits skip_reason %r but web sourceHealth.skipReasonLabel has no "
                    "sentence for it — the raw code would land on the settings run-status line (§48.7 追记: "
                    "the table covers SKIP_REASON_CODES in full)" % code)

    def test_web_table_has_no_invented_codes(self):
        for code in sorted(self.table):
            with self.subTest(code=code):
                self.assertIn(code, WIRE_CODES,
                              "web sourceHealth.skipReasonLabel knows %r but no Python path emits it "
                              "(§48.4: anything outside SKIP_REASON_CODES folds to %r)" % (code, FOLD_CODE))

    def test_each_sentence_is_bilingual_and_not_the_raw_code(self):
        for code, (zh, en) in sorted(self.table.items()):
            with self.subTest(code=code):
                self.assertTrue(zh and en, "empty sentence for %r" % code)
                self.assertNotEqual(zh, en)
                self.assertNotEqual(zh, code)
                self.assertNotEqual(en, code)

    def test_mcp_failed_detail_tail_is_stripped_before_the_web_sees_it(self):
        # 原生 humanSkip 的 mcp_failed 尾随错误摘录；§48.4 出机清洗只留裸码，所以 web 那句不带尾巴
        self.assertEqual(radar_health.public_skip_reason("mcp_failed: some detail"), "mcp_failed")
        self.assertIn("mcp_failed", self.table)


if __name__ == "__main__":
    unittest.main()
