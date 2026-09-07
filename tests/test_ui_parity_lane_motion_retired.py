"""§68.14 1.16 tombstone（决策 D46，2026-09-06）：看板换列飞行 / deal-in / 书立条脉冲动画正式退役。

钉三件事，缺一即红：
  1. §66 清单：`control:board:label:card`（原生 BoardMotion.titlesFor 给飞行标签用的兜底词
     「卡片 / Card」）经 `CONTROL_OWNER` 标 retired、理由带 D46 与 §68.14，JSON 里 gated=False；
  2. 账本：`ui/parity/pending.txt` 不再挂这一行（退役条目留在 pending 上会被 §66.2 判 STALE）；
  3. 死 CSS：`web/src/styles/animations.css` 不再定义 `.is-moving` / `.is-settling` /
     `task-card-settle`，且 web/src 没有任何 TS/TSX/CSS/HTML 在注释之外再引用这三个名字——飞行层
     没被移植，这些规则从出生起就没人挂，删了就不许再长回来（长回来 = 先修 §68.14 的 tombstone）；
     注释里引 tombstone 提到名字不算。
纯文件读，无 subprocess、无网络（防腐 #7）。
"""
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UI_DIR = os.path.join(_ROOT, "scripts", "ui")
if _UI_DIR not in sys.path:
    sys.path.insert(0, _UI_DIR)

import extract_native_inventory as inv  # noqa: E402
import ui_common as uc  # noqa: E402

CARD_LABEL_ID = "control:board:label:card"
ANIMATIONS_CSS = os.path.join(_ROOT, "web", "src", "styles", "animations.css")
WEB_SRC = os.path.join(_ROOT, "web", "src")
RETIRED_NAMES = ("is-moving", "is-settling", "task-card-settle")


def _web_src_files():
    for dirpath, _dirs, files in os.walk(WEB_SRC):
        for name in files:
            if name.endswith((".ts", ".tsx", ".css", ".html")):
                yield os.path.join(dirpath, name)


def _strip_comments(body):
    """去掉三种注释：/* */ 块注释、// 行注释（整行或行尾，`://` 这种 URL 不算）、<!-- --> HTML 注释。
    注释里提退役名字是合法的（引用 D46 tombstone 的自然位置，animations.css 头部就这么做）；
    剩下的代码 / 字符串 / 选择器里出现才算长回来。"""
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    return re.sub(r"(?m)(?:^|(?<=\s))//.*$", "", body)


class LaneMotionRetiredTestCase(unittest.TestCase):
    def test_control_owner_retires_the_flight_label_fallback_with_d46_and_law(self):
        entry = inv.CONTROL_OWNER.get(CARD_LABEL_ID)
        self.assertIsNotNone(entry, "%s must be retired via CONTROL_OWNER (D46)" % CARD_LABEL_ID)
        self.assertEqual(entry["owner"], "retired")
        self.assertIn("D46", entry["reason"])
        self.assertIn("§68.14", entry["reason"])

    def test_committed_inventory_carries_the_retirement(self):
        doc = uc.load_json(uc.INVENTORY_PATH)
        item = next(c for c in doc["controls"] if c["id"] == CARD_LABEL_ID)
        self.assertEqual((item["owner"], item["gated"]), ("retired", False))
        self.assertEqual(item["source"].split(":")[0], "BoardMotion.swift")
        self.assertEqual(doc["attribution"]["control_owner"][CARD_LABEL_ID], inv.CONTROL_OWNER[CARD_LABEL_ID])

    def test_pending_ledger_no_longer_lists_the_retired_label(self):
        self.assertNotIn(CARD_LABEL_ID, uc.load_ledger(uc.PENDING_PATH))
        self.assertNotIn(CARD_LABEL_ID, uc.load_ledger(uc.WAIVERS_PATH))   # 退役走归属表，不走 waivers（§66.2 末句）

    def test_animations_css_dropped_the_moving_and_settling_rules(self):
        css = uc.read_text(ANIMATIONS_CSS)
        # 注释里允许提到名字（头部差异清单 ③ 记着删了什么）；规则本身不许在
        rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        self.assertNotRegex(rules, r"\.is-moving\b")
        self.assertNotRegex(rules, r"\.is-settling\b")
        self.assertNotIn("@keyframes task-card-settle", rules)
        # 留下的动效与两重降级仍在：hover 抬升、sheen、reduced-motion、「看板动画」开关
        self.assertIn(".task-card:hover", rules)
        self.assertIn("@keyframes task-processing-sheen", rules)
        self.assertIn("prefers-reduced-motion: reduce", rules)
        self.assertIn('data-board-animations="off"', rules)

    def test_comment_stripper_spares_comments_but_not_code(self):
        # 行注释 / 块注释 / HTML 注释里提名字不算复活；className 字串与选择器才算
        spared = "\n".join((
            "// legacy is-moving note (D46)",
            "const url = 'https://x.test/a'; // see task-card-settle tombstone",
            "/* .is-settling was here */",
            "<!-- is-moving -->",
        ))
        self.assertNotRegex(_strip_comments(spared), r"is-moving|is-settling|task-card-settle")
        self.assertIn("https://x.test/a", _strip_comments(spared))
        self.assertIn("is-moving", _strip_comments('<div className="task-card is-moving" />'))
        self.assertIn(".is-settling", _strip_comments(".task-card.is-settling { opacity: 1 }"))

    def test_nothing_in_web_src_references_the_retired_class_names(self):
        offenders = []
        for path in _web_src_files():
            body = _strip_comments(uc.read_text(path))
            for name in RETIRED_NAMES:
                if re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(name), body):
                    offenders.append("%s: %s" % (uc.display_path(path), name))
        self.assertEqual(offenders, [], "retired lane-motion names resurfaced (D46 / §68.14 1.16 tombstone)")


if __name__ == "__main__":
    unittest.main()
