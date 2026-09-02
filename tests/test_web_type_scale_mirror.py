"""web 看板字号/字重梯 ↔ 原生看板源行 的判例（CONTRACT §54.1 第 10 项）。

原生 mac/Sources 在 D3 下是冻结的只读外观规格。web 的 type scale 住在
web/src/styles/tokens.css（truth），可读镜像表 web/src/styles/typeScale.ts 给每个
--type-* token 标了它镜像的 Swift 源行（file:line + size/weight/design）。vitest
（typeScale.test.ts）钉 CSS ↔ 表；本文件钉 表 ↔ Swift——被引用的那一行必须真的写着
`.font(.system(size: N[, weight: .W][, design: .monospaced]))`，否则表在说谎。
web 侧不许 import node:*（@types/node 不在 dev 白名单），所以读 Swift 的这一半在这里。
P8 删 mac/ 时把本文件改成 tombstone（§6）。
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAC = REPO_ROOT / "mac" / "Sources"
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
_FONT = re.compile(r"^(\d{3}) (\d+)px/[\d.]+(?:px)? var\(--font-(sans|mono)\)$")


def _entries():
    text = TYPE_SCALE_TS.read_text(encoding="utf-8")
    found = [m.groupdict() for m in _ENTRY.finditer(text)]
    return found


def _swift_line(file: str, line: int) -> str:
    lines = (MAC / file).read_text(encoding="utf-8").splitlines()
    return lines[line - 1] if 0 < line <= len(lines) else ""


class TypeScaleMirrorTestCase(unittest.TestCase):
    def setUp(self):
        self.entries = _entries()

    def test_table_parses_and_is_non_trivial(self):
        self.assertGreaterEqual(len(self.entries), 20, "typeScale.ts 表项没解析出来（结构变了？）")
        tokens = [e["token"] for e in self.entries]
        self.assertEqual(len(tokens), len(set(tokens)), "token 名重复")

    def test_every_cited_swift_line_carries_the_declared_size_weight_design(self):
        for e in self.entries:
            with self.subTest(token=e["token"]):
                line = _swift_line(e["file"], int(e["line"]))
                where = "%s:%s" % (e["file"], e["line"])
                self.assertIn(".font(.system(size: ", line, "%s 不是 .font(.system(...)) 行" % where)
                self.assertIn("size: %s" % e["size"], line, where)
                if e["weight"] == "regular":
                    self.assertNotIn("weight:", line, "%s 声明 regular 但源行带 weight:" % where)
                else:
                    # 允许原生的三元写法（`weight: rework ? .semibold : .regular`）
                    self.assertRegex(line, r"weight: [^)]*\.%s\b" % e["weight"], where)
                if e["mono"]:
                    self.assertIn("design: .monospaced", line, where)
                else:
                    self.assertNotIn("design:", line, where)

    def test_font_shorthand_in_table_matches_the_declared_swift_face(self):
        for e in self.entries:
            with self.subTest(token=e["token"]):
                m = _FONT.match(e["font"])
                self.assertIsNotNone(m, "font 简写格式不对：%s" % e["font"])
                weight, size, family = m.groups()
                self.assertEqual(int(weight), WEIGHT_OF[e["weight"]])
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
