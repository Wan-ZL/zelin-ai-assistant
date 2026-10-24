"""web 看板字号/字重梯 ↔ 原生看板源行 的判例（CONTRACT §54.1 第 10 项）。

web 的 type scale 住在 web/src/styles/tokens.css（truth），可读镜像表
web/src/styles/typeScale.ts 给每个 --type-* token 标了它镜像的原生 Swift 源行（file:line +
size/weight/design）。vitest（typeScale.test.ts）钉 CSS ↔ 表；本文件钉表自身的形状与
表 ↔ CSS。**tombstone（v2.0.0）**：「表 ↔ Swift 源行」那一半随 mac/ 删除退役（全文见 tag
full）——表里的 file:line 自此是历史出处，不再有文件可对。
token 值的固定写法（§54.1 第 12 项，显示偏好）：
``var(--w-<weight>) calc(<size>px * var(--text-scale))/<lh> var(--font-<sans|mono>)``——
字重经 --w-* 吃 --weight-shift、字号乘 --text-scale；原生 px 与 SF 字重名仍逐字可读，本文件
钉的就是那两个字面量。
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TYPE_SCALE_TS = REPO_ROOT / "web" / "src" / "styles" / "typeScale.ts"
TOKENS_CSS = REPO_ROOT / "web" / "src" / "styles" / "tokens.css"

WEIGHT_OF = {"regular": 400, "medium": 500, "semibold": 600, "bold": 700}

# 一条表项：token / font / swift{file,line,size,weight[,mono]}（typeScale.ts 的字面结构）
_ENTRY = re.compile(
    r'token:\s*"(?P<token>--type-[a-z-]+)",\s*font:\s*"(?P<font>[^"]+)",\s*'
    r'swift:\s*\{\s*file:\s*"(?P<file>[A-Za-z]+\.swift)",\s*line:\s*(?P<line>\d+),\s*'
    r'size:\s*(?P<size>\d+),\s*weight:\s*"(?P<weight>regular|medium|semibold|bold)"'
    r'(?P<mono>,\s*mono:\s*true)?\s*\}',
    re.S,
)
_FONT = re.compile(r"^var\(--w-(regular|medium|semibold|bold)\) calc\((\d+)px \* var\(--text-scale\)\)"
    r"/(?:[\d.]+|calc\(\d+px \* var\(--text-scale\)\)) var\(--font-(sans|mono)\)$")


def _entries():
    text = TYPE_SCALE_TS.read_text(encoding="utf-8")
    found = [m.groupdict() for m in _ENTRY.finditer(text)]
    return found


class TypeScaleMirrorTestCase(unittest.TestCase):
    def setUp(self):
        self.entries = _entries()

    def test_table_parses_and_is_non_trivial(self):
        self.assertGreaterEqual(len(self.entries), 10, "typeScale.ts 表项没解析出来（结构变了？）")
        tokens = [e["token"] for e in self.entries]
        self.assertEqual(len(tokens), len(set(tokens)), "token 名重复")

    def test_the_native_swift_reference_is_retired(self):
        self.assertFalse((REPO_ROOT / "mac").exists(), "mac/ retired in v2.0.0; the swift{} cells are history")

    def test_font_shorthand_in_table_matches_the_declared_swift_face(self):
        for e in self.entries:
            with self.subTest(token=e["token"]):
                m = _FONT.match(e["font"])
                self.assertIsNotNone(m, "font 简写格式不对：%s" % e["font"])
                weight, size, family = m.groups()
                self.assertEqual(weight, e["weight"])
                self.assertIn(weight, WEIGHT_OF)
                self.assertEqual(int(size), int(e["size"]))
                self.assertEqual(family, "mono" if e["mono"] else "sans")

    def test_tokens_css_carries_every_table_value_verbatim(self):
        css = re.sub(r"/\*.*?\*/", "", TOKENS_CSS.read_text(encoding="utf-8"), flags=re.S)
        declared = dict(re.findall(r"(--type-[a-z-]+)\s*:\s*([^;]+);", css))
        for e in self.entries:
            with self.subTest(token=e["token"]):
                self.assertEqual(declared.get(e["token"], "").strip(), e["font"])
        self.assertEqual(sorted(declared), sorted(e["token"] for e in self.entries),
                         "tokens.css 与 typeScale.ts 的 token 集合不一致")


if __name__ == "__main__":
    unittest.main()
