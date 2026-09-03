"""test-ui skill · 配对判官判例：同一控件被改名 / 移动 / 删除各是什么状态；元组配对、pin、aliases 的优先序；
近似只给建议不自动配对；hidden 永不 PRESENT；count 与 #n 序号；owner ∉ web = N-A；dynamic 名 = N-A；
extras 只是信息。零子进程。

法典：docs/CONTRACT.md §UI-parity（配对 = parity 契约 id 语法）；设计 vnext2-plan R2.8。
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

    def test_informational_reference_items_are_na(self):
        """原生清单 `gated: false` 的 copy / help 文案（§66.1 只列不判）→ N-A，不进 MISSING；project_gated 缺席 = 判。"""
        ref = [dict(kit.make_item("about", "static", "Long explanatory copy"), project_gated=False),
               dict(kit.make_item("about", "button", "Check now"), project_gated=True), kit.make_item("about", "button", "Quit")]
        rows = _by_id(_compare(ref, []))
        self.assertEqual(rows["control:about:static:long-explanatory-copy"]["status"], "N-A")
        self.assertTrue(rows["control:about:static:long-explanatory-copy"]["detail"]["informational"])
        self.assertEqual(rows["control:about:button:check-now"]["status"], "MISSING")
        self.assertEqual(rows["control:about:button:quit"]["status"], "MISSING")

    def test_union_fallback_only_when_family_absent(self):
        """§66.2「web 尚无的页面在全部面的并集里找」：参照 about 页的 Cancel，被测没有 about 家族 → 并集里的 Cancel
        配上（matched_by tuple:union）；board 家族存在但 board 上没有 Put back → MISSING，不去别的页借。"""
        ref = [kit.make_item("about", "button", "Cancel"), kit.make_item("board", "button", "Put back")]
        sub = [kit.make_item("settings", "button", "Cancel"), kit.make_item("board", "button", "Approve"),
               kit.make_item("trash", "button", "Put back")]
        rows = _by_id(_compare(ref, sub))
        self.assertEqual((rows["control:about:button:cancel"]["status"], rows["control:about:button:cancel"]["matched_by"]), ("PRESENT", "tuple:union"))
        self.assertEqual(rows["control:board:button:put-back"]["status"], "MISSING")

    def test_chrome_screens_share_the_window_family(self):
        """原生 `window`（侧栏 / 顶栏）↔ web `shell` / `app` 同属 window 家族：顶栏的 Settings 链接配得上。"""
        ref = [kit.make_item("window", "link", "Settings")]
        rows = _by_id(_compare(ref, [kit.make_item("shell", "link", "Settings")]))
        self.assertEqual(rows["control:window:link:settings"]["status"], "PRESENT")
        self.assertEqual(kit.tc.screen_family("app"), "window")
        self.assertEqual(kit.tc.screen_family("board.card"), "board")

    def test_more_instances_than_reference_is_not_a_change(self):
        """被测有 5 个 Cancel、参照 1 个 → PRESENT（多出来的是 extras，永不是 parity）；少了才 CHANGED count。"""
        ref = [kit.make_item("board", "button", "Cancel", count=1)]
        sub = [kit.make_item("board", "button", "Cancel", count=5)] + [kit.make_item("board", "button", "Cancel", count=5, ordinal=n) for n in range(2, 6)]
        self.assertEqual(_by_id(_compare(ref, sub))["control:board:button:cancel"]["status"], "PRESENT")

    def test_navigation_child_moved_into_banner_is_changed_parent(self):
        """owner (a)：侧栏 link 搬进顶栏 → CHANGED topology:parent（navigation → banner）；main 里的按钮换父地标不算。"""
        ref = [kit.make_item("window", "link", "Trash", parent="window>navigation:rail"),
               kit.make_item("board", "button", "Approve", parent="window>main:board")]
        sub = [kit.make_item("shell", "link", "Trash", parent="window>banner:shell-header"),
               kit.make_item("board", "button", "Approve", parent="window>banner:shell-header")]
        rows = _by_id(_compare(ref, sub))
        self.assertEqual((rows["control:window:link:trash"]["status"], rows["control:window:link:trash"]["fields_changed"]), ("CHANGED", ["topology:parent"]))
        self.assertEqual(rows["control:board:button:approve"]["status"], "PRESENT")

    def test_landmark_name_is_advisory(self):
        """无名原生侧栏 vs 带 aria-label 的 <nav>：不算 CHANGED name（地标按角色 + 拓扑配对）。"""
        ref = [kit.make_item("window", "navigation", None, parent="window", side_="left")]
        sub = [kit.make_item("shell", "navigation", None, parent="window", side_="left")]
        sub[0]["name"] = {"raw": "Main navigation", "zh": None, "en": None, "alt": []}
        self.assertEqual(_by_id(_compare(ref, sub))["landmark:window:navigation:navigation"]["status"], "PRESENT")

    def test_shortcut_and_setting_rows_adopt_the_project_verdict(self):
        """快捷键字形 / 设置键名不是可达树能量的：项目门判过的采它的（PENDING → MISSING[pending]，STALE → PRESENT[stale]），
        control 行不受影响；没判过的 id 原样。"""
        rows = [{"id": "shortcut:board:cmd-f", "status": "MISSING", "ledger": None, "detail": {}, "fields_changed": [], "matched_by": None},
                {"id": "setting:prefs:cardSortOrder", "status": "MISSING", "ledger": "pending", "detail": {}, "fields_changed": [], "matched_by": None},
                {"id": "setting:overrides:x", "status": "MISSING", "ledger": None, "detail": {}, "fields_changed": [], "matched_by": None},
                {"id": "control:board:button:approve", "status": "MISSING", "ledger": None, "detail": {}, "fields_changed": [], "matched_by": None}]
        parity.adopt_project_verdicts(rows, {"shortcut:board:cmd-f": "PRESENT", "setting:prefs:cardSortOrder": "STALE",
                                             "control:board:button:approve": "PRESENT"})
        by = {r["id"]: r for r in rows}
        self.assertEqual((by["shortcut:board:cmd-f"]["status"], by["shortcut:board:cmd-f"]["matched_by"]), ("PRESENT", "project:parity_check"))
        self.assertEqual((by["setting:prefs:cardSortOrder"]["status"], by["setting:prefs:cardSortOrder"]["ledger"]), ("PRESENT", "stale"))
        self.assertEqual((by["setting:overrides:x"]["status"], by["setting:overrides:x"]["matched_by"]), ("MISSING", None))
        self.assertEqual(by["control:board:button:approve"]["status"], "MISSING")  # controls keep the skill's own verdict

    def test_string_probe_without_project_gate(self):
        """无项目门：字形 `⌘F` / 键名 "cardSortOrder" 出现在 subject 源码文本 → PRESENT(source-string)；没出现仍 MISSING。"""
        ref = [dict(kit.make_item("board", "menuitem", "Find"), id="shortcut:board:cmd-f", shortcut="⌘F"),
               dict(kit.make_item("settings", "switch", "cardSortOrder"), id="setting:prefs:cardSortOrder"),
               dict(kit.make_item("settings", "switch", "nope"), id="setting:prefs:nope")]
        result = _compare(ref, [])
        text = 'const KEY = "cardSortOrder"; title="⌘F"'
        parity.string_probe(result["rows"], ref, text, EMPTY, result["problems"])
        by = _by_id(result)
        self.assertEqual((by["shortcut:board:cmd-f"]["status"], by["shortcut:board:cmd-f"]["matched_by"]), ("PRESENT", "source-string"))
        self.assertEqual(by["setting:prefs:cardSortOrder"]["detail"]["evidence"], '"cardSortOrder"')
        self.assertEqual(by["setting:prefs:nope"]["status"], "MISSING")

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
