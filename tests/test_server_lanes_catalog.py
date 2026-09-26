"""GET /api/lanes —— 列说明文案的 server-owned 目录（CONTRACT §49 路由表 /
§54 web 看板 parity / §78 提案车道退役；防腐十条 #10：文案进 server-owned catalog）。

钉住：形状（lanes[] 每项 slug + help.zh/help.en 非空）、slug 覆盖看板五列
（潜在任务 | 运行中 | 待验收 | 阶段性完成 | 永久性完成）且顺序 = 看板
从左到右、zh 文案与原生 shared/Sources/Lanes.swift 的关键词一致（原生是冻结
规格）、每次调用返回独立副本（调用方改不到常量）、token-light GET + no-store。

§78（D80.1）：提案列退役后 ``needs_approval`` 不再是一列，目录里没有它那一条
（wire 上 dashboard.json 仍恒发空的 ``needs_approval: []``，那是 add-only 的键，
不是列）；机器卡一律落 ``debt``，它的 help 必须讲清新的两步（研究并提议 → 促成
运行）。这里钉的就是「目录不得再发 needs_approval」这条退役事实。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from server import lanes
from tests.test_server_common import get_json, http_request, start_server

# §78：提案列退役，needs_approval 从这张表里消失（顺序仍 = 看板从左到右）
EXPECTED_ORDER = ["debt", "running", "review", "completed", "archived"]


class CatalogShapeTestCase(unittest.TestCase):
    def test_every_lane_has_bilingual_help(self):
        doc = lanes.catalog()
        self.assertEqual([lane["slug"] for lane in doc["lanes"]], EXPECTED_ORDER)
        # §78：退役的列不得再有列说明——有说明就等于 web 会画出那一列
        self.assertNotIn("needs_approval", [lane["slug"] for lane in doc["lanes"]])
        for lane in doc["lanes"]:
            self.assertEqual(set(lane), {"slug", "help"})
            self.assertEqual(set(lane["help"]), {"zh", "en"})
            self.assertTrue(lane["help"]["zh"].strip())
            self.assertTrue(lane["help"]["en"].strip())

    def test_copy_mirrors_native_lane_help_keywords(self):
        # 原生 LaneHelp（shared/Sources/Lanes.swift）每条的锚点词——文案改了必须两边一起改
        by_slug = {lane["slug"]: lane["help"] for lane in lanes.catalog()["lanes"]}
        self.assertIn("研究并提议", by_slug["debt"]["zh"])
        self.assertIn("Research & propose", by_slug["debt"]["en"])
        # §78：潜在任务现在是机器卡的唯一落点，两步动词都要出现在列说明里，
        # 否则 owner 在看板上看不出「先补计划、再一键开跑」这条路（原 needs_approval
        # 那条锚点随列一起退役）。
        self.assertIn("促成运行", by_slug["debt"]["zh"])
        self.assertIn("Run it", by_slug["debt"]["en"])
        self.assertIn("需输入", by_slug["running"]["zh"])
        self.assertIn("draft PR", by_slug["review"]["zh"])
        self.assertIn("永久完成", by_slug["completed"]["zh"])
        self.assertIn("放回看板", by_slug["archived"]["zh"])
        self.assertIn("Put back", by_slug["archived"]["en"])

    def test_catalog_returns_a_fresh_copy(self):
        first = lanes.catalog()
        first["lanes"][0]["help"]["zh"] = "tampered"
        self.assertNotEqual(lanes.catalog()["lanes"][0]["help"]["zh"], "tampered")


class CatalogRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-lanes-"))
        (self.home / "state").mkdir()
        _, self.port = start_server(self, self.home)

    def test_get_lanes_is_token_light_and_no_store(self):
        status, headers, data = http_request(self.port, "GET", "/api/lanes")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIn(b'"slug": "debt"', data)
        # §78：退役的列不能从路由上漏回来（web 靠 slug 建列）
        self.assertNotIn(b'"slug": "needs_approval"', data)

    def test_get_lanes_body_equals_catalog(self):
        status, obj = get_json(self.port, "/api/lanes")
        self.assertEqual(status, 200)
        self.assertEqual(obj, lanes.catalog())

    def test_lanes_does_not_need_a_dashboard(self):
        # 目录是 server 常量，dashboard.json 缺席也能读（首启前列头就有说明）
        status, _obj = get_json(self.port, "/api/lanes")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
