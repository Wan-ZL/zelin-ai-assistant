"""server /api/cards/{id} 的 store2 真源路由判例（CONTRACT §49/§53；R2.1 g）。

真源判定镜像 registry.backend()：env/config 的 §53.6 回滚开关 > 激活标记。
激活标记在（无开关）→ 详情增补从 SQLite payload 读（act/lib/store2/readonly
的 mode=ro 物理只读面），**不**回落 YAML（那是迁移冻结件）；回滚开关强制
yaml → 详情增补跟着回到 YAML 目录（曾经 server 只看标记：文档化回滚后
/api/cards/{id} 永远读已废弃的 store2.db——B2 判例）；标记不在 → 既有 YAML
扫描路径逐字保留。server 侧仍零写（§44 单写者）。
"""
import json
import os
import unittest
from pathlib import Path
import tempfile
from unittest import mock

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


def _write_yaml_card(home: Path, rid: str, body: str) -> None:
    (home / "act" / "registry" / f"{rid}.yaml").write_text(body,
                                                           encoding="utf-8")


class Store2DetailTestCase(unittest.TestCase):
    def setUp(self):
        # 套件级沙箱强制 ZAI_REGISTRY_BACKEND=yaml；这里模拟真实部署（env
        # 未设）——server 的真源判定现在镜像 registry.backend()，含这个 env。
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("ZAI_REGISTRY_BACKEND", None)

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
        _write_yaml_card(home, "R-002",
                         "id: R-002\ntitle: 旧世界的卡\nstatus: card_sent\n")
        with self.assertRaises(Exception):
            board_source.card_detail(home, "R-002")

    def test_yaml_path_intact_without_marker(self):
        home = _mk_home()
        _write_yaml_card(
            home, "R-001",
            "id: R-001\ntitle: yaml 增补\nstatus: card_sent\nnotes: 旧路健在\n")
        detail = board_source.card_detail(home, "R-001")
        self.assertEqual(detail["notes"], "旧路健在")

    def test_rollback_config_switch_returns_detail_reads_to_yaml(self):
        """§53.6 文档化回滚（标记留在原地 + config 开关强制 yaml）：server
        详情读必须跟着回 YAML——曾经只看标记，回滚后永远读死 DB（B2）。"""
        home = _mk_home()
        _seed_store2(home, {"id": "R-001", "title": "废弃 DB 里的旧值",
                            "status": "card_sent", "notes": "stale-db-value"})
        paths.config_path(home).write_text("registry:\n  backend: yaml\n",
                                           encoding="utf-8")
        _write_yaml_card(
            home, "R-001",
            "id: R-001\ntitle: 回滚后的真相\nstatus: card_sent\n"
            "notes: 回滚后新写\n")
        detail = board_source.card_detail(home, "R-001")
        self.assertEqual(detail["notes"], "回滚后新写")
        # 回滚窗口内 YAML 真源上新铸的卡必须可读（曾 404）
        _write_yaml_card(home, "R-900",
                         "id: R-900\ntitle: 回滚窗口新卡\nstatus: detected\n")
        self.assertEqual(board_source.card_detail(home, "R-900")["title"],
                         "回滚窗口新卡")

    def test_rollback_env_switch_returns_detail_reads_to_yaml(self):
        home = _mk_home()
        _seed_store2(home, {"id": "R-001", "title": "废弃 DB 里的旧值",
                            "status": "card_sent", "notes": "stale-db-value"})
        _write_yaml_card(
            home, "R-001",
            "id: R-001\ntitle: 回滚后的真相\nstatus: card_sent\n"
            "notes: env 开关生效\n")
        os.environ["ZAI_REGISTRY_BACKEND"] = "yaml"
        detail = board_source.card_detail(home, "R-001")
        self.assertEqual(detail["notes"], "env 开关生效")

    def test_garbage_config_falls_back_to_marker(self):
        """坏 config / 词表外值 = auto（看标记），与 registry._coerce 同口径。"""
        home = _mk_home()
        _seed_store2(home, {"id": "R-001", "title": "真源标题",
                            "status": "card_sent", "notes": "db 真相"})
        paths.config_path(home).write_text("registry:\n  backend: banana\n",
                                           encoding="utf-8")
        detail = board_source.card_detail(home, "R-001")
        self.assertEqual(detail["notes"], "db 真相")


if __name__ == "__main__":
    unittest.main()
