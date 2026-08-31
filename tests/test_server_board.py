"""GET /api/board 透传 + GET /api/cards/{id} 增补（BUILD-CONTRACT §2.1/§2.3）。

覆盖：
- 六个 demo 场景（demo_seed --scene）逐一透传：响应 bytes 与 dashboard.json
  磁盘 bytes 完全一致（零改写），且 hero 卡 R-101 落在场景对应分区；
- dashboard.json 缺席 → 404 NOT_FOUND envelope；未知 /api/* 路由 → 404；
- /api/cards/{id}：投影行字段 verbatim + ``lane`` + registry YAML add-only
  增补（投影已有键绝不被覆盖）；archive/ 优先于 active（crash 残留判例）；
  list 批次文件可命中；R-000-example.yaml 永不加载；
- card id 闸门：穿越形 id（含 URL 编码）→ 400 INVALID_FIELD，查无此卡 → 404。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first
from tests.test_server_common import (DEMO_SEED_PATH, SCENES, assert_envelope,
                                      dashboard_path, get_json, http_request,
                                      seed_scene, start_server, write_text)

from server import board_source

HERO = "R-101"

# 场景 → hero 卡所在分区（demo_seed 的管线走位，UI 各列渲染的判据）
_HERO_LANE = {
    "captured": "needs_approval",
    "initial": "needs_approval",
    "approved": "running",
    "running": "running",
    "review": "review",
    "done": "completed",
}


@unittest.skipUnless(DEMO_SEED_PATH, "scripts/demo_seed.py not found")
class BoardPassthroughTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-g5-board-"))
        seed_scene(self.home, "initial")
        _, self.port = start_server(self, self.home)

    def test_every_scene_passes_through_byte_identical(self):
        for scene in SCENES:
            with self.subTest(scene=scene):
                dash = seed_scene(self.home, scene)
                status, headers, body = http_request(
                    self.port, "GET", "/api/board")
                self.assertEqual(status, 200)
                self.assertIn("application/json",
                              headers.get("Content-Type", ""))
                self.assertEqual(headers.get("Cache-Control"), "no-store")
                # 透传 = 与磁盘文件逐字节一致（不 reserialize、不改字段名）
                self.assertEqual(body, dashboard_path(self.home).read_bytes())
                # hero 卡走位正确（分区词表 = demo_seed SECTIONS）
                lane = _HERO_LANE[scene]
                ids = [row["id"] for row in dash[lane]]
                self.assertIn(HERO, ids,
                              f"scene={scene}: {HERO} should sit in {lane}")
                for sec in dash["counts"]:
                    self.assertEqual(dash["counts"][sec], len(dash[sec]))

    def test_hero_scene_shapes(self):
        # captured = raising 占位（processing true）；approved = queued 无 session
        dash = seed_scene(self.home, "captured")
        hero = [c for c in dash["needs_approval"] if c["id"] == HERO][0]
        self.assertTrue(hero["processing"])

        dash = seed_scene(self.home, "approved")
        hero = [c for c in dash["running"] if c["id"] == HERO][0]
        self.assertEqual(hero["state"], "queued")
        self.assertNotIn("session_id", hero)
        self.assertNotIn("copy_cmd", hero)

    def test_unknown_api_route_404_envelope(self):
        status, obj = get_json(self.port, "/api/nonsense")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_unknown_post_route_404_envelope(self):
        import json as _json
        status, _headers, data = http_request(
            self.port, "POST", "/api/nonsense", body=b"{}",
            headers={"Content-Type": "application/json"})
        self.assertEqual(status, 404)
        assert_envelope(self, _json.loads(data.decode("utf-8")), "NOT_FOUND")


class BoardMissingTestCase(unittest.TestCase):
    def test_missing_dashboard_is_404_envelope(self):
        home = Path(tempfile.mkdtemp(prefix="zai-g5-empty-"))
        _, port = start_server(self, home)
        status, obj = get_json(port, "/api/board")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")


@unittest.skipUnless(DEMO_SEED_PATH, "scripts/demo_seed.py not found")
class CardDetailProjectionTestCase(unittest.TestCase):
    """投影行部分——不依赖 PyYAML（registry 增补缺席也必须可用）。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-g5-card-"))
        self.dash = seed_scene(self.home, "initial")
        _, self.port = start_server(self, self.home)

    def test_projection_row_verbatim_plus_lane(self):
        status, obj = get_json(self.port, f"/api/cards/{HERO}")
        self.assertEqual(status, 200)
        row = [c for c in self.dash["needs_approval"] if c["id"] == HERO][0]
        self.assertEqual(obj["lane"], "needs_approval")
        for k, v in row.items():  # 投影字段一个不少、一字不改
            self.assertEqual(obj[k], v, f"projection field {k} mutated")

    def test_unknown_card_404(self):
        status, obj = get_json(self.port, "/api/cards/R-424242")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_traversal_id_rejected(self):
        # URL 编码穿越：unquote 后含 "/"，SAFE_ID_RE 必拒
        for bad in ("..%2F..%2Fsecrets", "R-101%2F..%2Fx", "a.b", "-R101"):
            with self.subTest(bad=bad):
                status, obj = get_json(self.port, f"/api/cards/{bad}")
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "INVALID_FIELD")

    def test_overlong_id_rejected(self):
        status, obj = get_json(self.port, "/api/cards/" + "a" * 65)
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")

    def test_nul_in_path_rejected(self):
        status, obj = get_json(self.port, "/api/cards/R-1%0001")
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")


@unittest.skipUnless(DEMO_SEED_PATH, "scripts/demo_seed.py not found")
@unittest.skipUnless(board_source.yaml is not None,
                     "PyYAML unavailable — registry enrichment degrades")
class CardDetailEnrichmentTestCase(unittest.TestCase):
    """registry YAML 增补：add-only 合并 + archive 优先 + 批次文件 + 样例跳过。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-g5-enrich-"))
        self.dash = seed_scene(self.home, "initial")
        self.reg = self.home / "act" / "registry"
        _, self.port = start_server(self, self.home)

    def test_registry_fields_fill_gaps_only(self):
        write_text(self.reg / f"{HERO}.yaml", "\n".join([
            f"id: {HERO}",
            "status: card_sent",
            "title: REGISTRY-TITLE-MUST-NOT-WIN",
            "plan: REGISTRY-PLAN-MUST-NOT-WIN",
            "definition_of_done:",
            "  - 注册表补充的 DoD 第一条",
            "execution:",
            "  session_id: deadbeef-0000",
            "notes: 折叠备注一条",
            "",
        ]))
        status, obj = get_json(self.port, f"/api/cards/{HERO}")
        self.assertEqual(status, 200)
        row = [c for c in self.dash["needs_approval"] if c["id"] == HERO][0]
        # 投影已有键绝不被 registry 覆盖（add-only 铁律）
        self.assertEqual(obj["title"], row["title"])
        self.assertEqual(obj["plan"], row["plan"])
        self.assertEqual(obj["dod"], row["dod"])
        # registry 独有键补进来
        self.assertEqual(obj["status"], "card_sent")
        self.assertEqual(obj["definition_of_done"], ["注册表补充的 DoD 第一条"])
        self.assertEqual(obj["execution"], {"session_id": "deadbeef-0000"})
        self.assertEqual(obj["notes"], "折叠备注一条")

    def test_registry_only_card_has_null_lane(self):
        write_text(self.reg / "R-500.yaml",
                   "id: R-500\nstatus: detected\ntitle: 只在注册表里的卡\n")
        status, obj = get_json(self.port, "/api/cards/R-500")
        self.assertEqual(status, 200)
        self.assertIsNone(obj["lane"])
        self.assertEqual(obj["title"], "只在注册表里的卡")

    def test_archive_copy_wins_over_active(self):
        # crash-mid-move 残留判例：archive/ 副本 authoritative
        write_text(self.reg / "R-900.yaml",
                   "id: R-900\nstatus: detected\nmarker: active\n")
        write_text(self.reg / "archive" / "R-900.yaml",
                   "id: R-900\nstatus: archived\nmarker: archive\n")
        status, obj = get_json(self.port, "/api/cards/R-900")
        self.assertEqual(status, 200)
        self.assertEqual(obj["marker"], "archive")
        self.assertEqual(obj["status"], "archived")

    def test_list_batch_file_hit(self):
        write_text(self.reg / "R-777-batch.yaml", "\n".join([
            "- id: R-777",
            "  status: detected",
            "  title: 批次文件里的卡",
            "- id: R-778",
            "  status: detected",
            "  title: 批次文件里的另一张卡",
            "",
        ]))
        status, obj = get_json(self.port, "/api/cards/R-778")
        self.assertEqual(status, 200)
        self.assertEqual(obj["title"], "批次文件里的另一张卡")

    def test_example_file_never_loaded(self):
        write_text(self.reg / "R-000-example.yaml",
                   "id: R-901\nstatus: detected\ntitle: 文档样例不许出卡\n")
        status, obj = get_json(self.port, "/api/cards/R-901")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
