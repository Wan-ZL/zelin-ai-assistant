"""GET /api/notifications —— 系统通知目录（CONTRACT §28 / §49 路由表 / §66.2 探针 notify_catalog）。

钉住：
- 壳直发的每句系统通知（title + body）与 shell/Sources 的 ``L("zh","en")`` 对逐字一致
  （占位只差名字：目录 `{n}` vs Swift `\\(overflow.count)`）；引用 §25 FailureCatalog 的
  正文与 act/lib/failures.py plain_zh / plain_en 逐字一致（单源，不复制第二份）；
- §66 清单里每个 ``notification:<kind>`` 都登记在 ``kinds``，每条 gated 的
  ``control:notifications:*`` 都能在 ``shell_notices`` 里找到同一对 zh / en；
- 守护进程真写的 kind（act/recap NOTIFY_KIND、NotifyRelay 过滤的 review_ready）在词表里；
- 路由：token-light GET、no-store、body == catalog()、不需要 dashboard。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import recap
from act.lib import failures
from server import notify_catalog as nc
from tests.test_server_common import get_json, http_request, start_server

REPO_ROOT = Path(__file__).resolve().parent.parent
_UI_DIR = str(REPO_ROOT / "scripts" / "ui")
if _UI_DIR not in sys.path:
    sys.path.insert(0, _UI_DIR)
import ui_common as uc  # noqa: E402


def _shell_l_pairs() -> list:
    """shell/Sources 全部 L() 对（zh, en），插值已成 {expr} 占位。"""
    pairs = []
    for name in sorted(os.listdir(REPO_ROOT / "shell" / "Sources")):
        if not name.endswith(".swift"):
            continue
        raw = uc.read_text(str(REPO_ROOT / "shell" / "Sources" / name))
        stripped, masked = uc.scan_views(raw)
        pairs.extend((zh, en) for _off, zh, en in uc.find_l_calls(stripped, masked))
    return pairs


def _has_template(pairs: list, zh: str, en: str) -> bool:
    """title / 静态 body：某个 L() 对与目录句只差占位名。"""
    return any(nc.same_template(zh, pzh) and nc.same_template(en, pen) for pzh, pen in pairs)


def _contains_fragments(pairs: list, zh: str, en: str) -> bool:
    """插值 body（Swift 嵌套括号的插值提取不稠密）：目录句的每个静态片段都是某个 L() 对的子串。"""
    for pzh, pen in pairs:
        if all(f in pzh for f in nc.fragments(zh)) and all(f in pen for f in nc.fragments(en)):
            return True
    return False


class ShellMirrorTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pairs = _shell_l_pairs()
        cls.inventory = uc.load_json(uc.INVENTORY_PATH)

    def test_every_title_is_an_l_pair_in_shell_sources(self):
        for notice in nc.SHELL_NOTICES:
            with self.subTest(notice=notice["id"]):
                self.assertTrue(_has_template(self.pairs, notice["title"]["zh"], notice["title"]["en"]),
                                "title of %s is not what the shell posts" % notice["id"])

    def test_bodies_are_the_shell_sentence_or_the_failure_catalog_sentence(self):
        for notice in nc.SHELL_NOTICES:
            body = nc.resolve_notice(notice)["body"]
            with self.subTest(notice=notice["id"]):
                self.assertTrue(body["zh"] and body["en"], "body must be bilingual and non-empty")
                fid = notice["body_failure_id"]
                if fid is not None:
                    self.assertEqual(body, {"zh": failures.FAILURES[fid]["plain_zh"],
                                            "en": failures.FAILURES[fid]["plain_en"]})
                else:
                    self.assertTrue(_contains_fragments(self.pairs, body["zh"], body["en"]),
                                    "body of %s drifted from shell/Sources" % notice["id"])

    def test_inventory_kinds_and_notice_controls_are_all_registered(self):
        names = nc.kind_names()
        for item in self.inventory["notifications"]:
            with self.subTest(item=item["id"]):
                self.assertIn(item["kind"] or "general", names)
        gated = [c for c in self.inventory["controls"] if c["screen"] == "notifications" and c.get("gated")]
        self.assertGreaterEqual(len(gated), 5)   # 三句录制通知的 title + 汇总句的 title / body
        for control in gated:
            with self.subTest(control=control["id"]):
                self.assertEqual(control.get("probe"), "notify_catalog")
                self.assertTrue(nc.has_sentence(control["zh"], control["en"]),
                                "%s has no server-owned sentence" % control["id"])

    def test_slot_values_are_l_pairs_in_shell_sources(self):
        """回滚句的插值词表（模式名 / 死因）每个取值都是壳真有的 L() 对，且清单把它们判为 notifications。"""
        reverted = next(n for n in nc.SHELL_NOTICES if n["id"] == "recording_mode_reverted")
        self.assertEqual(set(reverted["slots"]), {"failed", "kept", "cause"})
        for slot, values in reverted["slots"].items():
            self.assertTrue(values, slot)
            for value in values:
                with self.subTest(slot=slot, value=value["en"]):
                    self.assertTrue(_has_template(self.pairs, value["zh"], value["en"]),
                                    "slot value %r is not what the shell composes" % value["en"])
        self.assertTrue(nc.has_sentence("缺 Node.js", "Node.js is missing"))
        self.assertTrue(nc.has_sentence("屏幕 + 音频", "Screen + Audio"))
        self.assertFalse(nc.has_sentence("屏幕+音频", "Screen + audio"))   # header 按钮词是另一对，web 自己渲
        rollback = [c for c in self.inventory["controls"]
                    if c["source"] in ("Recording.swift:348", "Recording.swift:350", "Recording.swift:159")]
        self.assertEqual(len(rollback), 3)
        for control in rollback:
            self.assertEqual((control["screen"], control["owner"], control.get("probe")),
                             ("notifications", "shell", "notify_catalog"), control["id"])

    def test_daemon_written_kinds_are_in_the_vocabulary(self):
        names = nc.kind_names()
        self.assertIn(recap.NOTIFY_KIND, names)
        self.assertIn("review_ready", names)
        self.assertIn("general", names)
        review = next(k for k in nc.KINDS if k["kind"] == "review_ready")
        self.assertEqual(review["preference"], "review_notify")   # §28 v0.46 三档偏好键

    def test_template_helpers(self):
        self.assertEqual(nc.fragments("还有 {n} 条通知"), ["还有 ", " 条通知"])
        self.assertTrue(nc.same_template("+{n} more notifications", "+{overflow.count} more notifications"))
        self.assertFalse(nc.same_template("+{n} more notifications", "{n} more notifications"))
        self.assertEqual(nc.fragments("plain"), ["plain"])
        self.assertTrue(nc.has_sentence("打开 App 查看看板", "Open the app to see the board"))   # body 也算一句
        self.assertFalse(nc.has_sentence("打开 App 查看看板", "Open the app"))


class CatalogShapeTestCase(unittest.TestCase):
    def test_shape_and_fresh_copy(self):
        doc = nc.catalog()
        self.assertEqual(set(doc), {"shell_notices", "kinds"})
        ids = [n["id"] for n in doc["shell_notices"]]
        self.assertEqual(len(ids), len(set(ids)))
        for notice in doc["shell_notices"]:
            self.assertEqual(set(notice), {"id", "title", "body", "slots", "source"})
            for key in ("title", "body"):
                self.assertTrue(notice[key]["zh"] and notice[key]["en"])
        for kind in doc["kinds"]:
            self.assertTrue(kind["title"]["zh"] and kind["help"]["en"])
        doc["shell_notices"][0]["title"]["zh"] = "tampered"
        doc["kinds"][0]["title"]["zh"] = "tampered"
        fresh = nc.catalog()
        self.assertNotEqual(fresh["shell_notices"][0]["title"]["zh"], "tampered")
        self.assertNotEqual(fresh["kinds"][0]["title"]["zh"], "tampered")

    def test_unknown_failure_id_body_is_empty_not_a_crash(self):
        notice = dict(nc.SHELL_NOTICES[1], body_failure_id="no_such_failure")
        self.assertEqual(nc.resolve_notice(notice)["body"], {"zh": "", "en": ""})


class CatalogRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-notify-"))
        (self.home / "state").mkdir()
        _, self.port = start_server(self, self.home)

    def test_get_notifications_is_token_light_and_no_store(self):
        status, headers, data = http_request(self.port, "GET", "/api/notifications")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIn(b'"kind": "review_ready"', data)

    def test_body_equals_catalog_without_a_dashboard(self):
        status, obj = get_json(self.port, "/api/notifications")
        self.assertEqual(status, 200)
        self.assertEqual(obj, nc.catalog())


if __name__ == "__main__":
    unittest.main()
