"""store2.store 变异存活体判例（CONTRACT §53；R-292，夜报 2026-09-07：116 体 / 57 存活）。

夜报 57 体里有两大族本来就有判例，只是没进靶区（qa/mutation_targets.toml）：
* 纯 helper 一族（verb 表兜底 / 审计行 / 行参数装配 / assignment 收集）——
  tests/test_store2_store_helpers.py；
* schema v1→v2 升级梯子一族（_UPGRADES 查表 / _has_column / 写锁下复核版本 /
  after == from+1）—— tests/test_two_stage_card_ids.py 的 SchemaUpgradeTestCase。
本文件钉住两族之外仍活着的体，按源码位置分组：

* VERBS 表三处 bool 字面量（reject.stash_prev / archive.stash_prev /
  unarchive.to_prev）：表值 + 真库来回走一遍，回程票必须存进去、用掉即清空。
* ``_transition_changes`` 的下标族（old[1]/new[1]/old[2]/new[2] 互换）：三列同时
  真变化且六个值互异，逐行比对 before/after，光比 field 名抓不到换下标。
* ``_dump_json(ensure_ascii=False)``：payload 列里 CJK 必须按字面落盘（可读、
  不膨胀），读回相等不算证据，直接看 SELECT payload 的原文。
* ``_translate_integrity`` 三条 return：trigger 码 → TransitionDenied /
  IntegrityViolation；UNIQUE 按索引名或列名细分；兜底 INTEGRITY_ERROR。
* ``_check_known_version`` / ``pre_upgrade_snapshot_path`` / ``_parse_payload`` /
  ``db_path`` 四处 return 值。
* ``_upgrade_one_level`` 的并发复核分支（cur != version）：另一开库者已升完时
  必须 COMMIT 放锁、返回**现行**版本、并且**不拍快照**（拍到的会是升级后形状）。

夜报预算只跑到 235 个 site 的 116 个；本地全量复跑（R-292）把余下 119 个也过了一遍，
新露出的体一并钉在这里：
* ``actor_id or "<verb>"`` 十一处默认值（create / put_card / transition / update /
  purge / note / note 回执 / source / dispatch 开与收）：审计行 actor_id 缺省 = 动词名，
  显式传入则原样落账。
* ``_require_live_card`` 的 ``row is None or row["tombstone"]``：缺卡与 tombstone 卡
  的子表写入都必须是 NotFound，不能漏到 FK 错误或写进骨架行。
* ``get_card_by_work_id`` 的 None 分流、``create_card`` / ``purge_trashed`` 幂等路径 /
  ``add_source`` / ``get_sources`` / ``_require_live_card`` 的返回值。
* ``_cas_update`` 件三：changes != 1 时重查分 404 / 409（公开 API 走不到——预检与
  UPDATE 同在 BEGIN IMMEDIATE 写锁下，所以直接调 helper）。
* ``close()``：真的关掉本线程连接、清空槽位、二次 close 无害。
* ``_insert_sources`` 五列顺序（date / ref 相邻，换下标只有五值互异才露）。
* ``SCHEMA_UPGRADE_BROKEN`` 的 details.expected = from + 1。

等价变异体（不补判例，理由记在这里，夜报再报不用再判）：
* ``fetchone()[0] → [-1]``（store.py ``PRAGMA user_version`` 三处 + board_revision
  三处）——单列行的首尾同一元素；``[1]`` 已被 IndexError 杀掉。
* WAL 转换重试 ``range(100)`` 的 99 / 101 与 ``break → continue``：只改重试上限或
  多发 99 次无锁 pragma，行为不变、只有耗时——正确性本就不依赖 WAL（见 _conn 注释）。
"""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib.store2 import store as st
from act.lib.store2 import (IntegrityViolation, NotFound, Store, StoreError,
                            TransitionDenied, VersionConflict)

NOW = "2026-09-08T12:00:00Z"


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="store2-kills-"))
        self.db = self.tmp / "s.db"
        self.store = Store(self.db, now_fn=lambda: NOW)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create(self, card_id, status, **extra):
        card = {"id": card_id, "status": status, "title": "t", **extra}
        return self.store.create_card(card, actor_type="system")


class VerbTableFlagsTestCase(_StoreCase):
    """store.py:307/318/319 —— reject / archive 进站存票，unarchive 按票回程。"""

    def test_verb_table_flags_pinned(self):
        self.assertTrue(st.VERBS["reject"].stash_prev)
        self.assertTrue(st.VERBS["archive"].stash_prev)
        self.assertTrue(st.VERBS["unarchive"].to_prev)
        self.assertIsNone(st.VERBS["unarchive"].to)
        self.assertEqual(st.VERBS["unarchive"].fallback, "delivered")
        self.assertTrue(st.VERBS["restore"].to_prev)
        self.assertEqual(st.VERBS["restore"].fallback, "detected")

    def test_reject_stashes_return_ticket_and_restore_uses_it(self):
        self._create("R-1", "card_sent")
        rejected = self.store.transition("R-1", "reject", "user", 1)
        self.assertEqual((rejected["status"], rejected["prev_status"]), ("trashed", "card_sent"))
        restored = self.store.transition("R-1", "restore", "user", 2)
        self.assertEqual((restored["status"], restored["prev_status"]), ("card_sent", None))

    def test_archive_stashes_return_ticket_and_unarchive_uses_it(self):
        self._create("R-2", "delivered")
        archived = self.store.transition("R-2", "archive", "user", 1)
        self.assertEqual((archived["status"], archived["prev_status"]), ("archived", "delivered"))
        back = self.store.transition("R-2", "unarchive", "user", 2)
        self.assertEqual((back["status"], back["prev_status"]), ("delivered", None))
        # unarchive 的目标来自回程票：detected 封存的卡解封必须回 detected，不是兜底 delivered
        self._create("R-3", "detected")
        self.store.transition("R-3", "archive", "user", 1)
        back3 = self.store.transition("R-3", "unarchive", "user", 2)
        self.assertEqual(back3["status"], "detected")

    def test_fixed_target_pure_helper(self):
        self.assertEqual(st._fixed_target(st.VERBS["reject"], "card_sent", None),
                         ("trashed", "card_sent"))
        self.assertEqual(st._fixed_target(st.VERBS["archive"], "delivered", None),
                         ("archived", "delivered"))
        self.assertEqual(st._prev_target(st.VERBS["unarchive"], "detected"), ("detected", None))


class TransitionChangesIndexTestCase(unittest.TestCase):
    """store.py:396-400 —— 审计行逐字段 before/after，六值互异，换任一下标即露馅。"""

    def test_all_three_columns_change(self):
        rows = st._transition_changes(("a", "p", "m"), ("b", "q", "n"))
        self.assertEqual(rows, [
            {"field": "status", "before": "a", "after": "b"},
            {"field": "prev_status", "before": "p", "after": "q"},
            {"field": "merged_into_id", "before": "m", "after": "n"},
        ])

    def test_only_prev_status_changes_besides_status(self):
        rows = st._transition_changes(("a", "p", "m"), ("b", "q", "m"))
        self.assertEqual(rows, [
            {"field": "status", "before": "a", "after": "b"},
            {"field": "prev_status", "before": "p", "after": "q"},
        ])

    def test_only_merged_into_changes_besides_status(self):
        rows = st._transition_changes(("a", "p", "m"), ("b", "p", "n"))
        self.assertEqual(rows, [
            {"field": "status", "before": "a", "after": "b"},
            {"field": "merged_into_id", "before": "m", "after": "n"},
        ])


class DumpJsonEnsureAsciiTestCase(_StoreCase):
    """store.py:338 —— payload 落盘保留 CJK 字面（ensure_ascii=False）。"""

    def test_dump_json_keeps_non_ascii_literal(self):
        self.assertEqual(st._dump_json({"t": "中文"}), '{"t": "中文"}')

    def test_payload_column_stores_cjk_literally(self):
        self._create("R-1", "detected", payload={"t": "中文标题"})
        raw = self.store._conn().execute(
            "SELECT payload FROM cards WHERE id = 'R-1'").fetchone()[0]
        self.assertIn("中文标题", raw)
        self.assertNotIn("\\u", raw)


class TranslateIntegrityTestCase(unittest.TestCase):
    """store.py:152/155/156 —— IntegrityError 消息 → 错误族与码。"""

    def test_trigger_code_families(self):
        denied = st._translate_integrity(sqlite3.IntegrityError("ILLEGAL_TRANSITION"))
        self.assertIsInstance(denied, TransitionDenied)
        self.assertEqual((denied.code, denied.message), ("ILLEGAL_TRANSITION", "ILLEGAL_TRANSITION"))
        frozen = st._translate_integrity(sqlite3.IntegrityError("TOMBSTONE_FROZEN"))
        self.assertIsInstance(frozen, IntegrityViolation)
        self.assertNotIsInstance(frozen, TransitionDenied)
        self.assertEqual(frozen.code, "TOMBSTONE_FROZEN")

    def test_unique_constraints_split_by_index_or_column_name(self):
        by_index = st._translate_integrity(
            sqlite3.IntegrityError("UNIQUE constraint failed: index 'sources_dedup'"))
        by_column = st._translate_integrity(
            sqlite3.IntegrityError("UNIQUE constraint failed: cards.work_id"))
        self.assertEqual((by_index.code, by_column.code), ("SOURCE_DUPLICATE", "WORK_ID_DUPLICATE"))
        self.assertIsInstance(by_index, IntegrityViolation)
        self.assertIn("sources_dedup", by_index.message)

    def test_unknown_message_falls_back_to_integrity_error(self):
        other = st._translate_integrity(sqlite3.IntegrityError("CHECK constraint failed: cards"))
        self.assertIsInstance(other, IntegrityViolation)
        self.assertEqual((other.code, other.message), ("INTEGRITY_ERROR", "CHECK constraint failed: cards"))


class SmallReturnValuesTestCase(_StoreCase):
    """store.py:198/206/346/467 —— 四处 return 值本身被调用方使用。"""

    def test_check_known_version_returns_the_version(self):
        self.assertEqual(st._check_known_version(1), 1)
        self.assertEqual(st._check_known_version(st.SCHEMA_VERSION), st.SCHEMA_VERSION)
        with self.assertRaises(StoreError) as cm:
            st._check_known_version(st.SCHEMA_VERSION + 1)
        self.assertEqual(cm.exception.code, "SCHEMA_VERSION_MISMATCH")

    def test_pre_upgrade_snapshot_path_shape(self):
        self.assertEqual(st.pre_upgrade_snapshot_path("/x/y.db", 1), Path("/x/y.db.pre-v1"))
        self.assertEqual(st.pre_upgrade_snapshot_path(Path("/x/y.db"), "2"), Path("/x/y.db.pre-v2"))

    def test_parse_payload_tolerant_read(self):
        self.assertEqual(st._parse_payload('{"a": 1}'), {"a": 1})
        self.assertEqual(st._parse_payload("not json"), {})
        self.assertEqual(st._parse_payload(None), {})
        self.assertEqual(st._parse_payload("[1, 2]"), {})

    def test_db_path_property(self):
        self.assertEqual(self.store.db_path, str(self.db))


class UpgradeConcurrentRecheckTestCase(_StoreCase):
    """store.py:508/510 —— 写锁下复核发现别人已升完：放锁、返回现行版本、不拍快照。"""

    def test_already_upgraded_level_is_a_no_op(self):
        conn = self.store._conn()
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], st.SCHEMA_VERSION)
        result = self.store._upgrade_one_level(conn, st.SCHEMA_VERSION - 1)
        self.assertEqual(result, st.SCHEMA_VERSION)
        self.assertFalse(conn.in_transaction)
        self.assertFalse(st.pre_upgrade_snapshot_path(self.db, st.SCHEMA_VERSION - 1).exists())


HOT = {"status": "detected", "prev_status": None, "tier": "T1", "type": "", "title": "t",
       "origin_trust": "hand", "target_repo": None, "deadline": None,
       "merged_into_id": None, "work_id": None}


class ActorIdDefaultsTestCase(_StoreCase):
    """store.py:744/810/834/892/924/954/992/1011/1047/1075/1107 —— ``actor_id or "<verb>"``。"""

    def _last(self, card_id):
        return self.store.get_activities(card_id)[-1]

    def test_create_card_actor_id(self):
        created = self._create("R-1", "detected")
        self.assertEqual((created["id"], created["status"], created["version"]), ("R-1", "detected", 1))
        self.assertEqual(self._last("R-1")["actor_id"], "create")
        self.store.create_card({"id": "R-2", "status": "detected", "title": "t"},
                               actor_type="system", actor_id="radar")
        self.assertEqual(self._last("R-2")["actor_id"], "radar")

    def test_put_card_actor_id_insert_and_update(self):
        self.store.put_card("P-1", {"id": "P-1"}, HOT, [])
        self.assertEqual(self._last("P-1")["actor_id"], "put_card")
        self.store.put_card("P-1", {"id": "P-1", "a": 1}, HOT, [], actor_id="migrate")
        self.assertEqual(self._last("P-1")["actor_id"], "migrate")
        self.store.put_card("P-1", {"id": "P-1", "a": 2}, HOT, [])
        self.assertEqual(self._last("P-1")["actor_id"], "put_card")
        self.store.put_card("P-2", {"id": "P-2"}, HOT, [], actor_id="migrate")
        self.assertEqual(self._last("P-2")["actor_id"], "migrate")

    def test_transition_actor_id_defaults_to_verb(self):
        self._create("R-1", "card_sent")
        self.store.transition("R-1", "approve", "user", 1)
        self.assertEqual(self._last("R-1")["actor_id"], "approve")
        self.store.transition("R-1", "dispatch", "system", 2, actor_id="actd")
        self.assertEqual(self._last("R-1")["actor_id"], "actd")

    def test_update_card_fields_actor_id(self):
        self._create("R-1", "card_sent")
        self.store.update_card_fields("R-1", None, {"tier": "T2"}, "system")
        self.assertEqual(self._last("R-1")["actor_id"], "update")
        self.store.update_card_fields("R-1", None, {"tier": "T0"}, "system", actor_id="inbox")
        self.assertEqual(self._last("R-1")["actor_id"], "inbox")

    def test_purge_actor_id_change_row_and_idempotent_return(self):
        self._create("R-1", "card_sent")
        self.store.transition("R-1", "trash", "user", 1)
        purged = self.store.purge_trashed("R-1")
        self.assertEqual((purged["id"], purged["tombstone"], purged["payload"]), ("R-1", 1, {}))
        last = self._last("R-1")
        self.assertEqual(last["actor_id"], "purge")
        self.assertEqual(last["changes"], [{"field": "tombstone", "before": 0, "after": 1}])
        again = self.store.purge_trashed("R-1", actor_id="retention")
        self.assertEqual((again["id"], again["tombstone"]), ("R-1", 1))
        self.assertEqual(len(self.store.get_activities("R-1")), 3)      # create / trash / purge
        self._create("R-2", "card_sent")
        self.store.transition("R-2", "trash", "user", 1)
        self.store.purge_trashed("R-2", actor_id="retention")
        self.assertEqual(self._last("R-2")["actor_id"], "retention")

    def test_note_and_receipt_actor_ids(self):
        self._create("R-1", "card_sent")
        nid = self.store.add_note("R-1", "comment", "hi", "user")
        self.assertEqual(self._last("R-1")["actor_id"], "note:comment")
        self.store.mark_note_delivered(nid)
        self.assertEqual(self._last("R-1")["actor_id"], "note.delivered_at")
        self.store.mark_note_acked(nid, actor_id="session-1")
        self.assertEqual(self._last("R-1")["actor_id"], "session-1")
        nid2 = self.store.add_note("R-1", "steer", "go", "user", actor_id="zelin")
        self.assertEqual(self._last("R-1")["actor_id"], "zelin")
        self.store.mark_note_acked(nid2)
        self.assertEqual(self._last("R-1")["actor_id"], "note.acked_at")
        self.store.mark_note_delivered(nid2, actor_id="executor")
        self.assertEqual(self._last("R-1")["actor_id"], "executor")

    def test_source_actor_id_and_return_value(self):
        self._create("R-1", "card_sent")
        sid = self.store.add_source("R-1", "slack", who="z")
        self.assertEqual(self._last("R-1")["actor_id"], "source")
        self.assertIsInstance(sid, int)
        self.assertEqual([(s["id"], s["channel"]) for s in self.store.get_sources("R-1")],
                         [(sid, "slack")])
        sid2 = self.store.add_source("R-1", "gmail", actor_id="radar_gmail")
        self.assertEqual(self._last("R-1")["actor_id"], "radar_gmail")
        self.assertEqual([s["id"] for s in self.store.get_sources("R-1")], [sid, sid2])
        self.assertEqual(self.store.get_sources("R-none"), [])

    def test_dispatch_open_close_actor_ids(self):
        self._create("R-1", "card_sent")
        did = self.store.open_dispatch("R-1")
        self.assertEqual(self._last("R-1")["actor_id"], "dispatch")
        self.store.close_dispatch(did, "completed", exit_code=0, actor_id="reaper")
        self.assertEqual(self._last("R-1")["actor_id"], "reaper")
        did2 = self.store.open_dispatch("R-1", actor_id="executor")
        self.assertEqual(self._last("R-1")["actor_id"], "executor")
        self.store.close_dispatch(did2, "failed", exit_code=1)
        self.assertEqual(self._last("R-1")["actor_id"], "dispatch")


class LiveCardGuardTestCase(_StoreCase):
    """store.py:973/975 —— 子表写入前的活卡门：缺卡 / tombstone 卡都是 NotFound。"""

    def test_missing_card_is_not_found_for_every_subtable_write(self):
        with self.assertRaises(NotFound):
            self.store.add_note("R-404", "comment", "x", "user")
        with self.assertRaises(NotFound):
            self.store.add_source("R-404", "slack")
        with self.assertRaises(NotFound):
            self.store.open_dispatch("R-404")

    def test_tombstone_card_is_not_found_for_every_subtable_write(self):
        self._create("R-1", "card_sent")
        self.store.transition("R-1", "trash", "user", 1)
        self.store.purge_trashed("R-1")
        with self.assertRaises(NotFound):
            self.store.add_note("R-1", "comment", "x", "user")
        with self.assertRaises(NotFound):
            self.store.add_source("R-1", "slack")
        with self.assertRaises(NotFound):
            self.store.open_dispatch("R-1")
        self.assertEqual(self.store.get_notes("R-1"), [])

    def test_live_card_returns_its_row(self):
        self._create("R-2", "card_sent")
        with self.store._write() as conn:
            row = self.store._require_live_card(conn, "R-2")
        self.assertEqual((row["id"], row["tombstone"]), ("R-2", 0))


class ReadPathsTestCase(_StoreCase):
    """store.py:694 —— 工作编号查卡：有 → 卡 dict，无 → None（不抛）。"""

    def test_get_card_by_work_id(self):
        self._create("R-1", "detected", work_id="W-7")
        found = self.store.get_card_by_work_id("W-7")
        self.assertEqual((found["id"], found["work_id"]), ("R-1", "W-7"))
        self.assertIsNone(self.store.get_card_by_work_id("W-404"))


class CasUpdateSplitTestCase(_StoreCase):
    """store.py:856 —— CAS 件三：UPDATE 没命中时重查，卡没了 = 404，卡在 = 409。"""

    SQL = "UPDATE cards SET tier = ? WHERE id = ? AND version = ?"

    def test_stale_version_is_conflict_with_expected_and_actual(self):
        self._create("R-1", "card_sent")
        with self.assertRaises(VersionConflict) as cm:
            with self.store._write() as conn:
                self.store._cas_update(conn, "R-1", 99, self.SQL, ("T2", "R-1", 99))
        self.assertEqual(cm.exception.details, {"expected_version": 99, "actual_version": 1})
        self.assertEqual(self.store.get_card("R-1")["tier"], "T1")

    def test_vanished_card_is_not_found(self):
        with self.assertRaises(NotFound) as cm:
            with self.store._write() as conn:
                self.store._cas_update(conn, "R-404", 1, self.SQL, ("T2", "R-404", 1))
        self.assertEqual(cm.exception.details, {"kind": "card", "key": "R-404"})


class CloseConnectionTestCase(_StoreCase):
    """store.py:621 —— close() 关掉本线程连接、清槽位；二次 close 无害，之后可重开。"""

    def test_close_really_closes_and_is_idempotent(self):
        conn = self.store._conn()
        self.store.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
        self.assertIsNone(getattr(self.store._local, "conn", None))
        self.store.close()
        self.assertEqual(self.store.list_cards(), [])


class SourceColumnOrderTestCase(_StoreCase):
    """store.py:795 —— sources 投影五列顺序 (channel, who, date, ref, quote)。"""

    def test_insert_sources_keeps_column_order(self):
        self.store.put_card("P-1", {"id": "P-1"}, HOT,
                            [{"channel": "slack", "who": "w", "date": "d", "ref": "r", "quote": "q"}])
        row = self.store._conn().execute(
            "SELECT channel, who, date, ref, quote FROM sources WHERE card_id = 'P-1'").fetchone()
        self.assertEqual(tuple(row), ("slack", "w", "d", "r", "q"))
        self.assertEqual(self.store._src_rows([{"channel": "c", "date": "d", "ref": "r"}]),
                         [("c", None, "d", "r", None)])


class UpgradeBrokenDetailsTestCase(_StoreCase):
    """store.py:522/527 —— 升级函数忘钉版本：拒绝，且 details 说清期望的是 from+1。"""

    def test_upgrade_broken_details_name_expected_version(self):
        self.store.close()
        con = sqlite3.connect(self.db)
        con.execute(f"PRAGMA user_version = {st.SCHEMA_VERSION - 1}")
        con.close()
        with mock.patch.dict(st._UPGRADES, {st.SCHEMA_VERSION - 1: lambda conn: None}, clear=True):
            with self.assertRaises(StoreError) as cm:
                Store(self.db, now_fn=lambda: NOW)
        self.assertEqual(cm.exception.code, "SCHEMA_UPGRADE_BROKEN")
        self.assertEqual(cm.exception.details,
                         {"db_version": st.SCHEMA_VERSION - 1, "expected": st.SCHEMA_VERSION})


if __name__ == "__main__":
    unittest.main()
