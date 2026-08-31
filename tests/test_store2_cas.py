"""store2 Store API 层测试（BUILD-CONTRACT §3）：CAS 三件套与 D3 权限墙走
B2 的真实写路径（test_store2_schema.py 钉的是同一批不变量的裸 SQL 面；
这里证明 store.py 的 helper 与 trigger 执法接得上——B5 集成绿灯的关键一环）。
"""
import importlib.util
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env 先于任何 act.* import

_STORE_LANDED = importlib.util.find_spec("act.lib.store2.store") is not None
_SKIP_REASON = "act.lib.store2.store (B2) not importable"

if _STORE_LANDED:
    from act.lib.store2 import (IntegrityViolation, NotFound, Store,
                                StoreError, TransitionDenied, VersionConflict)

NOW = "2026-08-30T12:00:00Z"


@unittest.skipUnless(_STORE_LANDED, _SKIP_REASON)
class _StoreFixture(unittest.TestCase):
    """共享脚手架：临时库 + 铸卡 helper（本类无测试方法，仅供继承）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="store2-cas-"))
        self.store = Store(self.tmp / "store2.db", now_fn=lambda: NOW)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mint(self, rid="R-001", status="card_sent", **kw):
        card = {"id": rid, "status": status, "title": f"card {rid}"}
        card.update(kw)
        return self.store.create_card(card, actor_type="system")


class StoreApiTestCase(_StoreFixture):

    # -- (c) CAS 冲突 ------------------------------------------------------ #
    def test_stale_version_raises_conflict_first_writer_wins(self):
        self._mint()
        self.store.update_card_fields(
            "R-001", 1, {"deadline": "2026-09-01"}, actor_type="user")
        with self.assertRaises(VersionConflict) as cm:
            self.store.update_card_fields(
                "R-001", 1, {"deadline": "2026-12-31"}, actor_type="user")
        self.assertEqual(cm.exception.details["expected_version"], 1)
        self.assertEqual(cm.exception.details["actual_version"], 2)
        card = self.store.get_card("R-001")
        self.assertEqual(card["deadline"], "2026-09-01")  # 后写者没有静默覆盖
        self.assertEqual(card["version"], 2)
        # 重读拿新 version 重试 = 正确恢复路径
        self.store.update_card_fields(
            "R-001", 2, {"deadline": "2026-12-31"}, actor_type="user")
        self.assertEqual(self.store.get_card("R-001")["version"], 3)

    def test_missing_card_is_not_found_not_conflict(self):
        with self.assertRaises(NotFound):
            self.store.update_card_fields(
                "R-999", 1, {"deadline": "2026-09-01"}, actor_type="user")

    def test_tombstoned_card_reads_as_gone_for_writers(self):
        self._mint("R-001", "trashed", prev_status="detected")
        self.store.purge_trashed("R-001")
        with self.assertRaises(NotFound):
            self.store.update_card_fields(
                "R-001", 1, {"deadline": "2026-09-01"}, actor_type="user")

    # -- (d) D3 权限墙经真实 API ------------------------------------------- #
    def test_agent_approve_denied_user_approve_passes(self):
        self._mint()
        with self.assertRaises(TransitionDenied) as cm:
            self.store.transition("R-001", "approve", "agent", None)
        self.assertEqual(cm.exception.code, "AGENT_TRANSITION_FORBIDDEN")
        self.assertEqual(self.store.get_card("R-001")["status"], "card_sent")
        card = self.store.transition("R-001", "approve", "user", None)
        self.assertEqual(card["status"], "approved")

    def test_system_approve_hits_whitelist(self):
        self._mint()
        with self.assertRaises(TransitionDenied) as cm:
            self.store.transition("R-001", "approve", "system", None)
        self.assertEqual(cm.exception.code, "ILLEGAL_TRANSITION")

    def test_agent_accept_denied(self):
        self._mint("R-001", "review")
        with self.assertRaises(TransitionDenied) as cm:
            self.store.transition("R-001", "accept", "agent", None)
        self.assertEqual(cm.exception.code, "AGENT_TRANSITION_FORBIDDEN")

    # -- 回程票与 (e) tombstone 增量 --------------------------------------- #
    def test_trash_then_restore_returns_to_exact_prev_status(self):
        self._mint()
        self.store.transition("R-001", "trash", "user", None)
        card = self.store.get_card("R-001")
        self.assertEqual((card["status"], card["prev_status"]),
                         ("trashed", "card_sent"))
        card = self.store.transition("R-001", "restore", "user", None)
        self.assertEqual(card["status"], "card_sent")  # §9 精确复位

    def test_changes_since_carries_purge_tombstone(self):
        self._mint("R-001", "trashed", prev_status="detected")
        self._mint("R-002", "detected")
        cursor = self.store.current_revision()
        self.store.purge_trashed("R-001")
        delta = self.store.changes_since(cursor)
        self.assertEqual([(c["id"], c["tombstone"]) for c in delta["cards"]],
                         [("R-001", 1)])           # 增量客户端学到删除
        self.assertGreater(delta["revision"], cursor)
        self.assertEqual([c["id"] for c in self.store.list_cards()], ["R-002"])
        # purge 幂等：再删一次 = no-op，游标不再前进
        rev = self.store.current_revision()
        self.store.purge_trashed("R-001")
        self.assertEqual(self.store.current_revision(), rev)

    def test_noop_transition_is_idempotent(self):
        # 对已在 review 的卡再点 stop_to_review：不 bump 版本、不推游标
        self._mint("R-001", "review")
        before = self.store.get_card("R-001")["version"]
        rev = self.store.current_revision()
        card = self.store.transition("R-001", "stop_to_review", "user", None)
        self.assertEqual(card["version"], before)
        self.assertEqual(self.store.current_revision(), rev)


class AgentCreatePermissionTestCase(_StoreFixture):
    """组合权限旁路（store API 面）：agent 铸卡不得带 prev_status 回程票，
    也不得直接铸批准后各态；system（migration）与用户 restore 语义不受伤。"""

    def test_agent_create_cannot_carry_prev_status(self):
        with self.assertRaises(StoreError) as cm:
            self.store.create_card(
                {"id": "R-001", "status": "trashed", "title": "t",
                 "prev_status": "approved"}, actor_type="agent")
        self.assertEqual(cm.exception.code, "UNKNOWN_FIELD")
        with self.assertRaises(NotFound):
            self.store.get_card("R-001")

    def test_agent_create_post_approval_status_denied(self):
        for status in ("approved", "delivered", "executing", "review"):
            with self.assertRaises(TransitionDenied) as cm:
                self.store.create_card(
                    {"id": f"R-{status}", "status": status, "title": "t"},
                    actor_type="agent")
            self.assertEqual(cm.exception.code, "AGENT_TRANSITION_FORBIDDEN")

    def test_system_migration_shape_and_user_restore_stay_intact(self):
        # migration（system actor）铸带票 trashed 卡 + 用户 restore 精确复位：
        # 墙只挡 agent，合法回程票语义分毫不动
        self.store.create_card(
            {"id": "R-001", "status": "trashed", "title": "t",
             "prev_status": "approved"}, actor_type="system")
        card = self.store.transition("R-001", "restore", "user", None)
        self.assertEqual(card["status"], "approved")


class PurgeVsDispatchTestCase(_StoreFixture):
    """purge 与运行中 dispatch 的互斥：卡先冻结会让收尾账无处落（曾是死锁），
    两面都钉——purge 拒绝活 session，收尾账在卡已冻结时也照常落。"""

    def test_purge_refused_while_dispatch_running(self):
        self._mint("R-001", "trashed", prev_status="detected")
        did = self.store.open_dispatch("R-001")
        with self.assertRaises(IntegrityViolation) as cm:
            self.store.purge_trashed("R-001")
        self.assertEqual(cm.exception.code, "DISPATCH_ACTIVE")
        self.assertEqual(self.store.get_card("R-001")["tombstone"], 0)
        # 收尾后 purge 放行
        self.store.close_dispatch(did, "stopped")
        self.assertEqual(self.store.purge_trashed("R-001")["tombstone"], 1)

    def test_close_dispatch_survives_purged_card(self):
        # 模拟 crash-window 竞态：绕过 purge 闸门直接 tombstone（schema 对
        # trashed 卡放行），close_dispatch 仍必须把台账收干净、不许炸
        self._mint("R-001", "trashed", prev_status="detected")
        did = self.store.open_dispatch("R-001")
        self.store._conn().execute(
            "UPDATE cards SET tombstone = 1, payload = '{}',"
            " last_actor_type = 'system' WHERE id = 'R-001'")
        self.store.close_dispatch(did, "failed", exit_code=1)
        d = self.store.get_dispatches("R-001")[0]
        self.assertEqual((d["status"], d["exit_code"]), ("failed", 1))
        self.assertEqual(self.store.get_card("R-001")["tombstone"], 1)

    def test_note_receipts_survive_purged_card(self):
        # 回执 set-once 是诚实账（§32.2）：卡 purge 后补记也不许炸
        self._mint("R-001", "trashed", prev_status="detected")
        nid = self.store.add_note("R-001", "comment", "改方向", "user")
        self.store.purge_trashed("R-001")
        self.store.mark_note_delivered(nid)
        self.store.mark_note_acked(nid)
        note = self.store.get_notes("R-001")[0]
        self.assertIsNotNone(note["delivered_at"])
        self.assertIsNotNone(note["acked_at"])


if __name__ == "__main__":
    unittest.main()
