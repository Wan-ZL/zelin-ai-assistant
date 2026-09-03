"""test-ui skill · TOPOLOGY 判例：「左侧栏搬进 header」这一类改动——角色和名字都没变、只有 side / parent /
order 变了——必须被抓住（今日四漏之一）。源提取器从 data-side 取 side、从地标嵌套取 parent；判官只对
landmark / list / region / heading / tablist 比拓扑；缺一侧的值（源模式无 bbox）不裁。零子进程。

法典：docs/CONTRACT.md §UI-parity；设计 vnext2-plan R2.8。负控制：rail side left → top、parent window → banner。
"""
import unittest

from tests import skill_test_ui_testkit as kit

import inventory_a11y as inv  # noqa: E402
import parity  # noqa: E402

REF_HTML = """<html lang="zh"><body>
<nav aria-label="Rail" data-side="left"><a href="a">Workbench</a><a href="b">Settings</a></nav>
<header><h1>Title</h1></header>
<main><ul aria-label="Proposals"><li><button>批准</button></li></ul></main>
</body></html>"""
SUBJECT_HTML = """<html lang="zh"><body>
<header><h1>Title</h1><nav aria-label="Rail" data-side="top"><a href="a">Workbench</a><a href="b">Settings</a></nav></header>
<main><ul aria-label="Proposals"><li><button>批准</button></li></ul></main>
</body></html>"""


def _inv(html):
    items, marks = inv.SourceExtractor(html, "board", "board.html").run()
    return inv.finish_inventory(kit.make_inventory(items, landmarks=marks))


class TopologyExtractionTestCase(unittest.TestCase):
    def test_source_topology_fields(self):
        ref = {i["id"]: i for i in _inv(REF_HTML)["items"]}
        rail = ref["landmark:board:navigation:rail"]
        self.assertEqual(rail["topology"], {"parent": "window", "order": 0, "side": "left"})
        self.assertEqual(ref["control:board:link:workbench"]["topology"]["parent"], "window>navigation:rail")
        self.assertEqual(ref["control:board:button:批准"]["topology"]["parent"], "window>main:main>list:proposals>listitem:listitem")
        marks = {m["id"]: m for m in _inv(REF_HTML)["landmarks"]}
        self.assertEqual(marks["landmark:board:navigation:rail"]["children_order"], ["control:board:link:workbench", "control:board:link:settings"])

    def test_rail_moved_into_header_is_changed_topology(self):
        """负控制（今日漏项 #1）：名字全同，只搬了位置 → navigation CHANGED side + parent + order；链接本身 PRESENT。"""
        result = parity.compare_items(_inv(SUBJECT_HTML), _inv(REF_HTML), parity.load_ledgers(None), dict(parity.DEFAULT_THRESHOLDS))
        rows = {r["id"]: r for r in result["rows"]}
        rail = rows["landmark:board:navigation:rail"]
        self.assertEqual(rail["status"], "CHANGED")
        self.assertEqual(rail["fields_changed"], ["topology:side", "topology:parent", "topology:order"])
        self.assertEqual(rail["detail"]["topology"], {"reference": {"parent": "window", "order": 0, "side": "left"},
                                                      "subject": {"parent": "window>banner:banner", "order": 1, "side": "top"}})
        self.assertEqual(rows["control:board:link:workbench"]["status"], "PRESENT")
        self.assertEqual(rows["control:board:button:批准"]["status"], "PRESENT")

    def test_unchanged_pair_is_green(self):
        result = parity.compare_items(_inv(REF_HTML), _inv(REF_HTML), parity.load_ledgers(None), dict(parity.DEFAULT_THRESHOLDS))
        self.assertEqual({r["status"] for r in result["rows"]}, {"PRESENT"})


class TopologyCompareRulesTestCase(unittest.TestCase):
    def test_missing_side_on_one_side_is_not_a_change(self):
        """源模式参照没有 side（None）→ 不裁 side；order 同理。"""
        ref = kit.make_item("board", "navigation", "Rail", side_=None, order=0)
        sub = kit.make_item("board", "navigation", "Rail", side_="top", order=0)
        self.assertEqual(parity._topology_changed(ref, sub), [])
        self.assertEqual(parity._topology_changed(kit.make_item("board", "navigation", "Rail", side_="left"), sub), ["side"])

    def test_non_topology_roles_are_not_compared(self):
        ref = kit.make_item("board", "button", "Go", parent="window>main:main", side_="left")
        sub = kit.make_item("board", "button", "Go", parent="window>banner:banner", side_="top")
        self.assertEqual(parity._topology_changed(ref, sub), [])

    def test_parent_role_extraction(self):
        self.assertEqual(parity._parent_role("window"), "window")
        self.assertEqual(parity._parent_role(None), "window")
        self.assertEqual(parity._parent_role("window>banner:x>navigation:rail"), "navigation")

    def test_topology_check_source_substitute(self):
        """topology_runtime 在源模式 = substituted（无 CHANGED）或 fail（有 CHANGED），永不 pass。"""
        import checks_ui
        import sensors
        ctx = checks_ui.make_ctx("/r", kit.fake_det(["board.html"]))
        ctx["state"]["pair_source"] = parity.compare_items(_inv(SUBJECT_HTML), _inv(REF_HTML), parity.load_ledgers(None), dict(parity.DEFAULT_THRESHOLDS))
        self.assertEqual(sensors.check_topology_runtime(ctx)["status"], "fail")
        ctx["state"]["pair_source"] = parity.compare_items(_inv(REF_HTML), _inv(REF_HTML), parity.load_ledgers(None), dict(parity.DEFAULT_THRESHOLDS))
        self.assertEqual(sensors.check_topology_runtime(ctx)["status"], "substituted")
        self.assertEqual(sensors.check_topology_runtime(checks_ui.make_ctx("/r", kit.fake_det(["board.html"])))["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
