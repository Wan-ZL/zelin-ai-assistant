"""test-ui skill · 配对判官判例：同一控件被改名 / 移动 / 删除各是什么状态；元组配对、pin、aliases 的优先序；
近似只给建议不自动配对；hidden 永不 PRESENT；count 与 #n 序号；owner ∉ web = N-A；dynamic 名 = N-A；
extras 只是信息。零子进程。

法典：docs/CONTRACT.md §62（配对 = parity 契约 id 语法）；设计 vnext2-plan R2.8。
负控制：参照有 control:board:button:approve、被测没有 → MISSING 且 fix-first 级别材料齐全。
"""
import unittest

from tests import skill_test_ui_testkit as kit

import parity  # noqa: E402

EMPTY = parity.load_ledgers(None)
THR = dict(parity.DEFAULT_THRESHOLDS)


def _compare(ref_items, sub_items, ledgers=None, **kw):
    return parity.compare_items(kit.make_inventory(sub_items), kit.make_inventory(ref_items, role="reference"),
                                ledgers or EMPTY, THR, **kw)


def _by_id(result):
    return {r["id"]: r for r in result["rows"]}


class PairingTestCase(unittest.TestCase):
    def test_removed_control_is_missing(self):
        """负控制：参照的 approve 按钮在被测里没了 → MISSING（rank-1 材料：kind interactive）。"""
        ref = [kit.make_item("board", "button", "Approve"), kit.make_item("board", "button", "Later")]
        sub = [kit.make_item("board", "button", "Later")]
        rows = _by_id(_compare(ref, sub))
        self.assertEqual(rows["control:board:button:approve"]["status"], "MISSING")
        self.assertEqual(rows["control:board:button:approve"]["kind"], "interactive")
        self.assertEqual(rows["control:board:button:later"]["status"], "PRESENT")
        self.assertEqual(rows["control:board:button:later"]["matched_by"], "tuple")

    def test_renamed_control_missing_with_suggestion_only(self):
        """Settings → Setting：MISSING + 建议（相似度 ≥ 0.8），绝不自动配对；Trash → Bin 连建议都没有。"""
        ref = [kit.make_item("board", "link", "Settings"), kit.make_item("board", "link", "Trash")]
        sub = [kit.make_item("board", "link", "Setting"), kit.make_item("board", "link", "Bin")]
        result = _compare(ref, sub)
        rows = _by_id(result)
        self.assertEqual(rows["control:board:link:settings"]["status"], "MISSING")
        self.assertEqual(rows["control:board:link:settings"]["detail"]["suggestions"][0]["subject_id"], "control:board:link:setting")
        self.assertEqual(rows["control:board:link:trash"]["detail"]["suggestions"], [])
        self.assertEqual(sorted(result["extras"]), ["control:board:link:bin", "control:board:link:setting"])

    def test_alias_beats_pin_beats_tuple(self):
        ref = [kit.make_item("board", "link", "Settings")]
        sub = [kit.make_item("board", "link", "Setting"), kit.make_item("board", "link", "Other", pin="control:board:link:settings")]
        ledgers = dict(EMPTY, aliases={"control:board:link:settings": {"subject": "control:board:link:setting", "reason": "rename"}})
        rows = _by_id(_compare(ref, sub, ledgers))
        self.assertEqual((rows["control:board:link:settings"]["status"], rows["control:board:link:settings"]["matched_by"]), ("PRESENT", "alias"))
        rows = _by_id(_compare(ref, sub))
        self.assertEqual(rows["control:board:link:settings"]["matched_by"], "pin")

    def test_dangling_alias_is_a_problem(self):
        ref = [kit.make_item("board", "link", "Settings")]
        ledgers = dict(EMPTY, aliases={"control:board:link:settings": {"subject": "control:board:link:nope", "reason": ""}})
        result = _compare(ref, [], ledgers)
        self.assertIn("dangling_alias", {p["kind"] for p in result["problems"]})

    def test_moved_control_keeps_present_but_landmark_reports_topology(self):
        """按钮换了父地标不算 CHANGED（非 topology 角色）；navigation 换 side/parent 算 CHANGED topology。"""
        ref = [kit.make_item("board", "button", "Approve", parent="window>main:main"),
               kit.make_item("board", "navigation", "Rail", parent="window", order=0, side_="left")]
        sub = [kit.make_item("board", "button", "Approve", parent="window>banner:banner"),
               kit.make_item("board", "navigation", "Rail", parent="window>banner:banner", order=1, side_="top")]
        rows = _by_id(_compare(ref, sub))
        self.assertEqual(rows["control:board:button:approve"]["status"], "PRESENT")
        rail = rows["landmark:board:navigation:rail"]
        self.assertEqual(rail["status"], "CHANGED")
        self.assertEqual(rail["fields_changed"], ["topology:side", "topology:parent", "topology:order"])
        self.assertEqual(rail["detail"]["topology"]["reference"]["side"], "left")

    def test_hidden_is_missing_never_present(self):
        ref = [kit.make_item("board", "button", "Steer")]
        for hidden in ("display:none", "hidden", "aria-hidden"):
            sub = [kit.make_item("board", "button", "Steer", visible=False, hidden_by=hidden)]
            row = _by_id(_compare(ref, sub))["control:board:button:steer"]
            self.assertEqual((row["status"], row["detail"]["hidden_by"]), ("MISSING", hidden))

    def test_count_and_ordinals(self):
        ref = [kit.make_item("board", "list", "lane", count=3), kit.make_item("board", "list", "lane", count=3, ordinal=2),
               kit.make_item("board", "list", "lane", count=3, ordinal=3)]
        sub = [kit.make_item("board", "list", "lane", count=2), kit.make_item("board", "list", "lane", count=2, ordinal=2)]
        rows = _by_id(_compare(ref, sub))
        self.assertIn("count", rows["control:board:list:lane"]["fields_changed"])
        self.assertEqual(rows["control:board:list:lane#3"]["status"], "MISSING")

    def test_owner_and_dynamic_are_na(self):
        ref = [kit.make_item("about", "button", "Quit", owner="shell"), kit.make_item("board", "button", "{dynamic}")]
        rows = _by_id(_compare(ref, []))
        self.assertEqual(rows["control:about:button:quit"]["status"], "N-A")
        self.assertEqual(rows["control:board:button:dynamic"]["status"], "N-A")

    def test_name_change_through_pin(self):
        """pin 命中同角色、名字相近但不同 → CHANGED name（不是 spoof）。"""
        ref = [kit.make_item("board", "button", "Approve")]
        sub = [kit.make_item("board", "button", "Approved", pin="control:board:button:approve")]
        row = _by_id(_compare(ref, sub))["control:board:button:approve"]
        self.assertEqual((row["status"], row["fields_changed"]), ("CHANGED", ["name"]))
        self.assertEqual(row["detail"]["name"], {"reference": "Approve", "subject": "Approved"})

    def test_states_focusable_and_gated(self):
        ref = [kit.make_item("board", "button", "Go"), kit.make_item("board", "button", "Flagged", gated=False)]
        sub = [kit.make_item("board", "button", "Go", focusable=False), kit.make_item("board", "button", "Flagged", gated=True)]
        rows = _by_id(_compare(ref, sub))
        self.assertEqual(rows["control:board:button:go"]["fields_changed"], ["states"])
        self.assertEqual(rows["control:board:button:flagged"]["fields_changed"], ["gated"])
        # both disabled → not a change
        ref2 = [kit.make_item("board", "button", "Go", focusable=False)]
        self.assertEqual(_by_id(_compare(ref2, sub))["control:board:button:go"]["status"], "PRESENT")

    def test_unreachable_with_focus_walk(self):
        ref = [kit.make_item("board", "button", "Go"), kit.make_item("board", "button", "Stay")]
        sub = [kit.make_item("board", "button", "Go"), kit.make_item("board", "button", "Stay")]
        rows = _by_id(_compare(ref, sub, focus_walk={"control:board:button:go"}))
        self.assertEqual(rows["control:board:button:stay"]["fields_changed"], ["unreachable"])
        self.assertEqual(rows["control:board:button:go"]["status"], "PRESENT")


class SimilarityTestCase(unittest.TestCase):
    def test_similarity_floor_is_inclusive(self):
        """parity._suggest: `score >= floor`——恰在地板上的候选要列（>= → > 会漏）。"""
        ref = kit.make_item("board", "link", "abcde")
        sub = kit.make_item("board", "link", "abcdx")  # ratio 0.8
        self.assertEqual(parity.similarity("abcde", "abcdx"), 0.8)
        self.assertEqual(len(parity._suggest(ref, [sub], 0.8)), 1)
        self.assertEqual(len(parity._suggest(ref, [sub], 0.81)), 0)


if __name__ == "__main__":
    unittest.main()
