"""server /api/cards/{id} 的 store2 真源路由判例（CONTRACT §49/§53；R2.1 g）。

激活标记在 → 详情增补从 SQLite payload 读（act/lib/store2/readonly 的
mode=ro 物理只读面），标记在时**不**回落 YAML（那是迁移冻结件）；标记不在 →
既有 YAML 扫描路径逐字保留。server 侧仍零写（§44 单写者）。
"""
import json
import unittest
from pathlib import Path
import tempfile

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib.store2.store import Store
from act.lib.store2 import hot as store2_hot
from act.lib.store2.export_yaml import normalize_card
from server import board_source, paths


def _mk_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="server-store2-"))
    (home / "state").mkdir(parents=True)
    (home / "act" / "registry").mkdir(parents=True)
    (home / "state" / "dashboard.json").write_text(json.dumps({
        "generated_at": "2026-09-01T00:00:00Z",
        "needs_approval": [{"id": "R-001", "title": "投影标题", "tier": "T1"}],
    }), encoding="utf-8")
    return home


def _seed_store2(home: Path, card: dict) -> None:
    norm = normalize_card(card)
    hot, _w, errs = store2_hot.derive(norm)
    assert not errs, errs
    st = Store(paths.store2_db_path(home))
    st.put_card(norm["id"], norm, hot, [], actor_type="system")
    st.close()
    paths.store2_truth_path(home).write_text(
        json.dumps({"activated_at": "2026-09-01T00:00:00Z"}), encoding="utf-8")


class Store2DetailTestCase(unittest.TestCase):
    def test_detail_reads_payload_from_sqlite_when_active(self):
        home = _mk_home()
        _seed_store2(home, {
            "id": "R-001", "title": "真源标题", "status": "card_sent",
            "plan": ["step 1"], "notes": "一条备注",
            "sources": [{"channel": "meeting", "date": "2026-08-30",
                         "quote": "q", "who": "m"}]})
        detail = board_source.card_detail(home, "R-001")
        self.assertEqual(detail["lane"], "needs_approval")
        self.assertEqual(detail["title"], "投影标题")   # 投影字段绝不被覆盖
        self.assertEqual(detail["plan"], ["step 1"])    # payload 增补字段
        self.assertEqual(detail["notes"], "一条备注")

    def test_marker_present_never_falls_back_to_frozen_yaml(self):
        home = _mk_home()
        _seed_store2(home, {"id": "R-001", "title": "真源标题",
                            "status": "card_sent"})
        # 冻结的 YAML 残件带着旧数据——绝不能被当作真相读出来
        (home / "act" / "registry" / "R-002.yaml").write_text(
            "id: R-002\ntitle: 旧世界的卡\nstatus: card_sent\n", encoding="utf-8")
        with self.assertRaises(Exception):
            board_source.card_detail(home, "R-002")

    def test_yaml_path_intact_without_marker(self):
        home = _mk_home()
        (home / "act" / "registry" / "R-001.yaml").write_text(
            "id: R-001\ntitle: yaml 增补\nstatus: card_sent\nnotes: 旧路健在\n",
            encoding="utf-8")
        detail = board_source.card_detail(home, "R-001")
        self.assertEqual(detail["notes"], "旧路健在")


if __name__ == "__main__":
    unittest.main()
