"""场景表生成器 scripts/qa/coverage_inventory.py 的判例（CONTRACT §58 QA 门 / §66.2 UI 清单；§77.1 全覆盖清点）。

只喂小字符串与临时文件：不起子进程、不碰网络、不写仓里任何账本。最后两条用真仓
跑一遍生成器（纯文件读），把「committed 的 qa/coverage_inventory.json 陈旧了」钉成红灯。
"""

import importlib.util
import json
import os
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "_zai_coverage_inventory", os.path.join(_ROOT, "scripts", "qa", "coverage_inventory.py"))
ci = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ci)


CONTRACT_SAMPLE = """# 契约

## 12. 命名

正文一句。

### 12.1 子小节标题

正文。

## 13. 已经没用的一节

**v0.48 退役（#119）**：本节的产品面整体退役，行为并入 §12。

### 13.1 提及 tombstone 但自己还活着

导出时 tombstone 的卡随之删除——这一句只是提及，不是墓碑。
"""


class ParseContractTestCase(unittest.TestCase):
    """`## N.` / `### N.M` 逐条变成一行，tombstone 只认「说本节自己退役」的写法。"""

    def setUp(self):
        self.rows = {row["num"]: row for row in ci.parse_contract(CONTRACT_SAMPLE)}

    def test_sections_and_subsections_are_rows(self):
        self.assertEqual(sorted(self.rows), ["12", "12.1", "13", "13.1"])
        self.assertEqual(self.rows["12"]["id"], "contract:§12")
        self.assertEqual(self.rows["12.1"]["scenario"], "子小节标题")

    def test_only_self_retirement_is_a_tombstone(self):
        self.assertTrue(self.rows["13"]["tombstone"])
        self.assertFalse(self.rows["12"]["tombstone"])
        # 正文里提一句 tombstone 不等于这一节退役了（§53.4 / §66.1 曾被误判）
        self.assertFalse(self.rows["13.1"]["tombstone"])


class ProofAssemblyTestCase(unittest.TestCase):
    """proof 串联、模块上限与 flow 挂接。"""

    def test_join_dedupes_and_keeps_order(self):
        self.assertEqual(ci.join_proofs("a", "", "b && a", "c"), "a && b && c")

    def test_unittest_proof_sorts_and_caps(self):
        modules = {"tests.test_e", "tests.test_a", "tests.test_c", "tests.test_b", "tests.test_d"}
        proof = ci.unittest_proof(modules)
        self.assertEqual(proof, "unittest:" + ",".join(
            ["tests.test_a", "tests.test_b", "tests.test_c", "tests.test_d"]))
        self.assertEqual(ci.unittest_proof(set()), "")

    def test_flows_follow_the_brief_table(self):
        self.assertIn("flow:card_lifecycle", ci.contract_flows("1", "注册表（卡片账本）"))
        self.assertIn("flow:doctor_clean", ci.contract_flows("25", "doctor 失败分类"))
        self.assertIn("flow:recaps", ci.contract_flows("63", "会议 recap"))
        self.assertIn("flow:pwa", ci.contract_flows("73", "装成 app：PWA 安装清单"))
        install = ci.contract_flows("48", "后台雷达 agent")
        self.assertEqual(["flow:install_fresh", "flow:uninstall_reinstall"], install[:2])

    def test_citation_index_reads_section_and_subsection(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "test_x.py"), "w", encoding="utf-8") as fh:
                fh.write('"""钉 §53.5 的写者墙。"""\n')
            with open(os.path.join(tmp, "helper.py"), "w", encoding="utf-8") as fh:
                fh.write('"""§53.5 但不是 test_ 开头，不算判例。"""\n')
            index = ci.citation_index(tmp)
        self.assertEqual(index["53.5"], {"tests.test_x"})
        self.assertEqual(index["53"], {"tests.test_x"})


class MergeFixturesTestCase(unittest.TestCase):
    """B 档 fixture 行（另一个 builder 产的 qa/coverage_fixtures_b.json）合并进来。"""

    def _write(self, tmp, doc):
        path = os.path.join(tmp, "coverage_fixtures_b.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        return path

    def test_missing_file_is_empty(self):
        self.assertEqual(ci.fixtures_b_rows(os.path.join(os.sep, "nope", "absent.json")), [])

    def test_rows_are_normalised_and_merged_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {"scenarios": [
                {"id": "B-03-gmail-fold", "source": "fixtures", "scenario": "gmail fold",
                 "proof": "fixture:B-03-gmail-fold", "tier": "B"},
                {"id": "", "scenario": "no id — dropped"},
            ]})
            extra = ci.fixtures_b_rows(path)
        self.assertEqual([row["id"] for row in extra], ["B-03-gmail-fold"])
        self.assertEqual(extra[0]["status"], "todo")
        self.assertIsNone(extra[0]["waive_reason"])
        base = [ci.make_row("route:GET /api/board", "routes", "board", "http:GET /api/board expect=200")]
        merged = ci.merge_rows(base, extra)
        self.assertEqual([row["id"] for row in merged], ["B-03-gmail-fold", "route:GET /api/board"])

    def test_bare_list_shape_also_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, [{"id": "B-01-slack", "proof": "fixture:B-01-slack"}])
            rows = ci.fixtures_b_rows(path)
        self.assertEqual(rows[0]["tier"], "B")
        self.assertEqual(rows[0]["source"], "fixtures")

    def test_inventory_row_wins_over_a_fixture_with_the_same_id(self):
        base = [ci.make_row("route:GET /api/board", "routes", "board", "http:GET /api/board expect=200")]
        clash = [ci.make_row("route:GET /api/board", "fixtures", "hijack", "fixture:x", tier="B")]
        self.assertEqual(ci.merge_rows(base, clash)[0]["source"], "routes")


class SummaryTestCase(unittest.TestCase):
    """--summary 那一行是被 goal grep 的，逐字钉住。"""

    def test_summary_line_is_verbatim(self):
        rows = [ci.make_row("a", "routes", "s", "http:GET /a expect=200"),
                ci.make_row("b", "routes", "s", "")]
        self.assertEqual(ci.summary_line(rows),
                         "INVENTORY scenarios=2 missing_proof=1 sources=contract,parity,routes,settings,shell")


class MenuSpecTestCase(unittest.TestCase):
    """壳菜单表从 Swift 源码逐项读出来（喂字符串，不编译 Swift）。"""

    SOURCE = '''
    static func menus(lang: String, appName: String) -> [Menu] {
        return [
            Menu("", items: [
                Item(t("关于 \\(appName)", "About \\(appName)"), action: .shell(.about)),
                Item(t("设置…", "Settings…"), key: ",", action: .shell(.settings)),
                Item(t("隐藏其他", "Hide Others"), key: "h", option: true,
                     action: .responder("hideOtherApplications:")),
            ]),
        ]
    }

    /// 把表逐项装成 NSMenu（这之后的内容不算菜单项）
    static func build() {}
'''

    def test_items_titles_and_key_equivalents(self):
        items = ci.menu_items(self.SOURCE)
        self.assertEqual(items[0], ("关于", "About", "", False))
        self.assertEqual(items[1], ("设置…", "Settings…", ",", False))
        self.assertEqual(items[2], ("隐藏其他", "Hide Others", "h", True))

    def test_slugify_is_ascii_and_stable(self):
        self.assertEqual(ci.slugify("Close Window"), "close-window")
        self.assertEqual(ci.slugify("Settings…"), "settings")


class PrefKeyProofTestCase(unittest.TestCase):
    """清单 settings_keys 里不在 server 目录的键：web 自有的认 parity.test.tsx 的同名 it()，
    搬到 server 的（probe=server_source）认落点文件对应的快照 GET（issues #387 / #389）。"""

    def _pref(self, **extra):
        item = {"id": "setting:prefs:sidebarWidth", "key": "sidebarWidth", "store": "prefs", "owner": "web"}
        item.update(extra)
        return item

    def test_web_pref_with_a_vitest_it_gets_a_parity_proof(self):
        proof, note = ci._parity_setting_proof(
            self._pref(), {}, {}, frozenset({"setting:prefs:sidebarWidth"}))
        self.assertEqual(proof, "parity:setting:prefs:sidebarWidth")
        self.assertIn("vitest", note)

    def test_web_pref_without_a_vitest_it_stays_empty(self):
        proof, note = ci._parity_setting_proof(self._pref(), {}, {}, frozenset())
        self.assertEqual(proof, "")
        self.assertIn("no server/vitest probe", note)

    def test_server_landing_gets_the_snapshot_http_proof(self):
        proof, note = ci._parity_setting_proof(
            self._pref(id="setting:prefs:hasCompletedFirstRun", key="hasCompletedFirstRun",
                       owner="server", probe="server_source", landing="setup_done.json"),
            {}, {}, frozenset())
        self.assertEqual(proof, 'http:GET /api/setup expect=200 contains="done"')
        self.assertIn("setup_done.json", note)

    def test_an_unknown_server_landing_still_leaves_the_proof_empty(self):
        proof, _note = ci._parity_setting_proof(
            self._pref(probe="server_source", landing="nowhere.json"), {}, {}, frozenset())
        self.assertEqual(proof, "")

    def test_the_repo_parity_test_declares_the_six_web_prefs(self):
        """真仓的 web/src/parity.test.tsx 里这六把键各有一条 it()（只读文件，不跑 vitest）。"""
        found = ci.parity_vitest_ids(_ROOT)
        for key in ("boardAnimations", "captureHistory", "cardSortOrder",
                    "mainSection", "sidebarCollapsed", "sidebarWidth"):
            self.assertIn("setting:prefs:" + key, found)


_API = "/api/"   # 路由字面量分段拼（见 RouteProofDialectTestCase 的 docstring）

WAIVERS_SAMPLE = """# ui/parity/waivers.txt —— 注释行，不是条目。
control:a:label:one  #119 retired (CONTRACT §NN tombstone); no web landing  #119 / #126
control:b:label:two  sheet hint — retired with #119 answer_input  #119 / #126
control:c:label:three  D34 卡片详情只留侧栏一面  #217
control:d:label:four  owner 还没拍过板  #999
"""


class ParityWaiverTestCase(unittest.TestCase):
    """§66.2 的 ui/parity/waivers.txt：判卷面发 it.skip，清单必须记 waived 而不是假 MISSING。"""

    def _ledger(self, tmp):
        os.makedirs(os.path.join(tmp, "ui", "parity"))
        with open(os.path.join(tmp, ci.WAIVERS_REL), "w", encoding="utf-8") as fh:
            fh.write(WAIVERS_SAMPLE)
        return ci.waiver_reasons(tmp)

    def test_reason_vocabulary_is_the_allowed_three(self):
        self.assertEqual(ci.waive_reason_for("D34 详情只留侧栏一面"), "design-not-carried D34")
        self.assertEqual(ci.waive_reason_for("retired (CONTRACT §NN tombstone)"), ci.WAIVE_TOMBSTONE)
        # 认不出出处 = None：发明一个 D 号比挂一条 MISSING 更坏
        self.assertIsNone(ci.waive_reason_for("owner 还没拍过板"))
        self.assertIsNone(ci.waive_reason_for(""))

    def test_ledger_rows_map_and_inherit_by_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            reasons = self._ledger(tmp)
        self.assertEqual(reasons["control:a:label:one"], ci.WAIVE_TOMBSTONE)
        # 同一个 #119 决策的第二个面：理由只写「retired with #119」，继承第一行的出处
        self.assertEqual(reasons["control:b:label:two"], ci.WAIVE_TOMBSTONE)
        self.assertEqual(reasons["control:c:label:three"], "design-not-carried D34")
        # 谁都没写出处的 id 不入表 → 仍是 todo
        self.assertNotIn("control:d:label:four", reasons)

    def test_waived_control_row_keeps_its_parity_proof(self):
        item = {"id": "control:a:label:one", "owner": "web", "gated": True, "zh": "一", "en": "one"}
        rows = ci._control_rows({"controls": [item]}, {},
                                {"control:a:label:one": ci.WAIVE_TOMBSTONE})
        self.assertEqual(rows[0]["status"], "waived")
        self.assertEqual(rows[0]["waive_reason"], ci.WAIVE_TOMBSTONE)
        self.assertEqual(rows[0]["proof"], "parity:control:a:label:one")
        self.assertIn("waivers.txt", rows[0]["note"])

    def test_an_id_with_no_waiver_stays_todo(self):
        item = {"id": "control:z:label:zed", "owner": "web", "gated": True, "zh": "", "en": ""}
        rows = ci._control_rows({"controls": [item]}, {}, {})
        self.assertEqual(rows[0]["status"], "todo")
        self.assertIsNone(rows[0]["waive_reason"])

    def test_the_repo_ledger_is_read_verbatim(self):
        """真仓的 waivers.txt 四行都认得出出处（读文件，不跑 vitest）。"""
        reasons = ci.waiver_reasons(_ROOT)
        self.assertEqual(sorted(reasons), sorted(ci.parity_waivers(_ROOT)))
        self.assertTrue(reasons)


class RouteProofDialectTestCase(unittest.TestCase):
    """route 行的两条口径：占位解析不出对象 → 打 404 那一支；写面 401 要带 Content-Type。

    路由字面量在本文件里一律分段拼（`_API + "logs/"`）：整条路径出现在判例正文里，生成器的
    `modules_citing` 会把这个模块当成「钉着那条路由的判例」，从而挤掉真正钉它的那几个模块。"""

    def _absent(self, tail, shown, expect):
        return "http:GET %s%s expect=%s" % (_API + tail, "__absent__" + shown, expect)

    def test_placeholder_tails_prove_the_branch_the_demo_can_reach(self):
        # demo 种子里没有这样的 section / log / job：快乐路径归 unittest，http 只留 404
        self.assertEqual(ci._get_http_proof(_API + "settings/", _API + "settings/{section}"),
                         self._absent("settings/", "", "404"))
        # LOG_NAME_RE 先判形：不带 .log 的名字是 400 而不是 404
        self.assertEqual(ci._get_http_proof(_API + "logs/", _API + "logs/{log}"),
                         self._absent("logs/", ".log", "404"))
        self.assertEqual(ci._get_http_proof(_API + "ingest/jobs/", _API + "ingest/jobs/{job}"),
                         self._absent("ingest/jobs/", "", "404"))

    def test_required_query_is_named(self):
        # recap 历史必须点名 key；demo 现场 {recap} 恒 __absent__ → 只证 400 守卫那一支，
        # 200 路径由 flow:recaps 证（见 _GET_GUARD）
        history = _API + "recaps/history"
        self.assertEqual(ci._get_http_proof(history, history),
                         "http:GET %s?key={recap} expect=400" % history)
        self.assertIn("flow:recaps", ci._route_proof("GET", history, history, {}))
        board = _API + "board"
        self.assertEqual(ci._get_http_proof(board, board), "http:GET %s expect=200" % board)

    def test_noauth_variant_keeps_the_same_status(self):
        for fragment, expected in (("expect=404", "noauth expect=404"),
                                   ("?key={recap} expect=200", "?key={recap} noauth expect=200")):
            self.assertEqual(ci._noauth("http:GET /x " + fragment), "http:GET /x " + expected)

    def test_binary_body_routes_carry_their_content_type_into_the_401(self):
        # Content-Type 闸排在 token 闸之前（server/app.py _check_write_auth）：不带 = 415
        upload = _API + "attachments"
        self.assertEqual(ci._write_401_fragment("POST", upload, "image/png"),
                         "http:POST %s noauth ctype=image/png expect=401" % upload)
        self.assertEqual(ci._write_401_fragment("PUT", upload, ""),
                         "http:PUT %s noauth expect=401" % upload)

    def test_media_types_come_from_the_app_table_not_a_hand_copy(self):
        self.assertEqual(ci.raw_media_types(_ROOT), {_API + "attachments": "image/png"})


class CommittedInventoryTestCase(unittest.TestCase):
    """committed 的 qa/coverage_inventory.json：形状 + 不陈旧（生成器只读文件，无子进程）。"""

    @classmethod
    def setUpClass(cls):
        with open(ci.INVENTORY_PATH, "r", encoding="utf-8") as fh:
            cls.doc = json.load(fh)
        cls.rows = cls.doc["scenarios"]

    def test_shape_is_sorted_unique_and_typed(self):
        ids = [row["id"] for row in self.rows]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))
        for row in self.rows:
            self.assertEqual(sorted(row), sorted(
                ["id", "source", "scenario", "proof", "tier", "status", "waive_reason", "note"]))
            self.assertIn(row["tier"], ("A", "B"))
            self.assertIn(row["status"], ("todo", "waived"))
            self.assertTrue(row["scenario"])
            if row["status"] == "waived":
                self.assertTrue(row["waive_reason"], row["id"])

    def test_every_source_is_represented_and_big_enough(self):
        by_source = {}
        for row in self.rows:
            by_source[row["source"]] = by_source.get(row["source"], 0) + 1
        for source in ci.SOURCES:
            self.assertGreater(by_source.get(source, 0), 0, source)
        self.assertGreaterEqual(len(self.rows), 900)

    def test_waive_reasons_use_the_allowed_vocabulary(self):
        for row in self.rows:
            if row["status"] != "waived":
                continue
            reason = row["waive_reason"]
            self.assertTrue(reason == ci.WAIVE_TOMBSTONE
                            or reason.startswith("covered-by-")
                            or reason.startswith("design-not-carried D"), row["id"])

    def test_file_is_not_stale(self):
        """五个清点源的行必须与生成器当下的产出逐字相同（B 档 fixture 行不参与）。"""
        fresh = [row for row in ci.build_rows() if row["source"] in ci.SOURCES]
        committed = [row for row in self.rows if row["source"] in ci.SOURCES]
        self.assertEqual(committed, fresh,
                         "run `python3 scripts/qa/coverage_inventory.py --write`")


if __name__ == "__main__":
    unittest.main()
