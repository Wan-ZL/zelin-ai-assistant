"""store2 schema 层执法测试（BUILD-CONTRACT §3；B1 schema.sql 的回归网）。

纯 sqlite3 + schema.sql，不依赖 B2 store.py / B3 migrate_yaml——这些测试
钉住 DDL 层不变量本身：
  * D3 权限墙：agent actor 的 approve/accept 类转移 RAISE（UPDATE + INSERT 两面）
  * transition_whitelist fail-closed（查不到 = ILLEGAL_TRANSITION）
  * CAS 三件套的 SQL 语义（dashi database.mjs:2181-2211 模式：
    WHERE id AND version → changes!=1 → 重查分 404/409）
  * tombstone 进 revision 流（增量游标 WHERE board_rev > :since 学到删除）
  * notes/activities append-only、set-once 回执、sources 去重、one_active
"""
import sqlite3
import unittest
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "act" / "lib" / "store2" / "schema.sql"

NOW = "2026-08-30T12:00:00Z"


def open_db() -> sqlite3.Connection:
    """新开一个 in-memory 库并套 schema。foreign_keys 是 per-connection
    PRAGMA（B2 约定），测试侧同样每连接显式开。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.row_factory = sqlite3.Row
    return conn


def insert_card(conn, card_id, status, *, actor="system", prev_status=None,
                merged_into_id=None, payload="{}", title=None):
    conn.execute(
        "INSERT INTO cards (id, status, prev_status, title, created, updated,"
        " merged_into_id, last_actor_type, payload)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (card_id, status, prev_status, title or f"card {card_id}",
         NOW, NOW, merged_into_id, actor, payload))


def set_status(conn, card_id, new_status, actor, *, prev_status=None):
    """B2 写路径纪律的最小复刻：status UPDATE 必须同语句 SET last_actor_type
    （trigger 读 NEW.last_actor_type 判 actor）。"""
    if prev_status is not None:
        return conn.execute(
            "UPDATE cards SET status = ?, prev_status = ?, last_actor_type = ?,"
            " updated = ? WHERE id = ?",
            (new_status, prev_status, actor, NOW, card_id))
    return conn.execute(
        "UPDATE cards SET status = ?, last_actor_type = ?, updated = ?"
        " WHERE id = ?",
        (new_status, actor, NOW, card_id))


def bump_rev(conn) -> int:
    conn.execute("UPDATE board_revision SET value = value + 1 WHERE id = 1")
    return conn.execute(
        "SELECT value FROM board_revision WHERE id = 1").fetchone()[0]


class TransitionWallTestCase(unittest.TestCase):
    """(d) trigger 测试：agent-actor 的 approve 转移 RAISE，user-actor 放行。"""

    def setUp(self):
        self.conn = open_db()

    def tearDown(self):
        self.conn.close()

    def _status(self, card_id):
        return self.conn.execute(
            "SELECT status FROM cards WHERE id = ?", (card_id,)).fetchone()[0]

    def test_user_approve_passes(self):
        insert_card(self.conn, "R-001", "card_sent")
        set_status(self.conn, "R-001", "approved", "user")
        self.assertEqual(self._status("R-001"), "approved")

    def test_agent_approve_update_raises(self):
        insert_card(self.conn, "R-001", "card_sent")
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            set_status(self.conn, "R-001", "approved", "agent")
        self.assertIn("AGENT_TRANSITION_FORBIDDEN", str(cm.exception))
        self.assertEqual(self._status("R-001"), "card_sent")  # 原状态未被污染

    def test_agent_deliver_update_raises_user_passes(self):
        insert_card(self.conn, "R-001", "review")
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            set_status(self.conn, "R-001", "delivered", "agent")
        self.assertIn("AGENT_TRANSITION_FORBIDDEN", str(cm.exception))
        set_status(self.conn, "R-001", "delivered", "user")  # 验收 = 用户专属
        self.assertEqual(self._status("R-001"), "delivered")

    def test_agent_insert_approved_or_delivered_raises(self):
        # INSERT 面的权限墙（cards_agent_insert_wall）：agent 不得直接铸批准/交付卡
        for status in ("approved", "delivered"):
            with self.assertRaises(sqlite3.IntegrityError) as cm:
                insert_card(self.conn, f"R-{status}", status, actor="agent")
            self.assertIn("AGENT_TRANSITION_FORBIDDEN", str(cm.exception))
        # 非 approve 类出生不受限（出生资格是应用层判断，schema 只挡权限墙）
        insert_card(self.conn, "R-002", "detected", actor="agent")
        self.assertEqual(self._status("R-002"), "detected")

    def test_agent_has_zero_whitelist_rows(self):
        # whitelist 里 agent 行数为零（宪法第 1 条单写者的 SQL 化）——
        # 即使是非 approve 类转移，agent 也一律 ILLEGAL_TRANSITION
        rows = self.conn.execute(
            "SELECT COUNT(*) FROM transition_whitelist WHERE actor_type = 'agent'"
        ).fetchone()[0]
        self.assertEqual(rows, 0)
        insert_card(self.conn, "R-001", "detected")
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            set_status(self.conn, "R-001", "raising", "agent")
        self.assertIn("ILLEGAL_TRANSITION", str(cm.exception))

    def test_system_approve_is_illegal(self):
        # 批准 = 用户专属（§3）：system 也不许替用户按批准键
        insert_card(self.conn, "R-001", "card_sent")
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            set_status(self.conn, "R-001", "approved", "system")
        self.assertIn("ILLEGAL_TRANSITION", str(cm.exception))

    def test_unlisted_transition_is_illegal(self):
        # fail-closed：表里没有的转移一律拒绝（delivered 不能直接回 executing）
        insert_card(self.conn, "R-001", "delivered")
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            set_status(self.conn, "R-001", "executing", "user")
        self.assertIn("ILLEGAL_TRANSITION", str(cm.exception))

    def test_forgot_set_actor_falls_back_to_stale_actor(self):
        # backstop 语义（schema.md）：writer 忘 SET last_actor_type 时 NEW 继承
        # OLD 值——agent 写过的行随后的裸 status UPDATE 仍按 agent 判、被拒
        insert_card(self.conn, "R-001", "detected", actor="agent")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE cards SET status = 'trashed', prev_status = 'detected'"
                " WHERE id = ?", ("R-001",))

    def test_lifecycle_chain_end_to_end(self):
        # 一条真实生命周期链全绿：detected→card_sent→approved→executing→review→delivered
        insert_card(self.conn, "R-001", "detected")
        for new_status, actor in (
                ("card_sent", "system"), ("approved", "user"),
                ("executing", "system"), ("review", "system"),
                ("delivered", "user")):
            set_status(self.conn, "R-001", new_status, actor)
        self.assertEqual(self._status("R-001"), "delivered")

    def test_card_id_immutable(self):
        insert_card(self.conn, "R-001", "detected")
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.conn.execute(
                "UPDATE cards SET id = 'R-999' WHERE id = 'R-001'")
        self.assertIn("CARD_ID_IMMUTABLE", str(cm.exception))

    def test_hard_delete_forbidden(self):
        insert_card(self.conn, "R-001", "detected")
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.conn.execute("DELETE FROM cards WHERE id = 'R-001'")
        self.assertIn("USE_TOMBSTONE", str(cm.exception))


class CasConflictTestCase(unittest.TestCase):
    """(c) CAS 冲突：WHERE id AND version 三件套（dashi database.mjs 模式）。
    B2 store.py 的 helper 落地后应复用同一语义；这里钉住 SQL 层的对错基线。"""

    def setUp(self):
        self.conn = open_db()
        insert_card(self.conn, "R-001", "detected")

    def tearDown(self):
        self.conn.close()

    def _cas_set_summary(self, card_id, version, summary):
        cur = self.conn.execute(
            "UPDATE cards SET payload = json_set(payload, '$.summary', ?),"
            " version = version + 1, updated = ? WHERE id = ? AND version = ?",
            (summary, NOW, card_id, version))
        if cur.rowcount == 1:
            return "ok"
        # changes != 1 → 重查分 404/409（行不存在 = missing；存在 = 版本冲突）
        row = self.conn.execute(
            "SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone()
        return "conflict" if row else "missing"

    def test_stale_version_conflicts_first_writer_wins(self):
        self.assertEqual(self._cas_set_summary("R-001", 1, "writer A"), "ok")
        self.assertEqual(self._cas_set_summary("R-001", 1, "writer B"), "conflict")
        row = self.conn.execute(
            "SELECT version, json_extract(payload, '$.summary') AS s"
            " FROM cards WHERE id = 'R-001'").fetchone()
        self.assertEqual(row["version"], 2)     # 只前进了一步
        self.assertEqual(row["s"], "writer A")  # 后写者没有静默覆盖

    def test_retry_with_fresh_version_succeeds(self):
        self.assertEqual(self._cas_set_summary("R-001", 1, "writer A"), "ok")
        # 409 后的正确姿势：重读拿新 version 再来一次
        version = self.conn.execute(
            "SELECT version FROM cards WHERE id = 'R-001'").fetchone()[0]
        self.assertEqual(self._cas_set_summary("R-001", version, "writer B"), "ok")

    def test_unknown_id_is_missing_not_conflict(self):
        self.assertEqual(self._cas_set_summary("R-999", 1, "x"), "missing")


class TombstoneRevisionTestCase(unittest.TestCase):
    """(e) tombstone + 增量游标：删除写 tombstone 进 revision 流，
    增量客户端（WHERE board_rev > :since）必须能学到删除。"""

    def setUp(self):
        self.conn = open_db()

    def tearDown(self):
        self.conn.close()

    def _purge(self, card_id):
        """schema.md 的 purge 设计：tombstone=1 + payload='{}' + bump board_rev。"""
        rev = bump_rev(self.conn)
        self.conn.execute(
            "UPDATE cards SET tombstone = 1, payload = '{}', board_rev = ?,"
            " last_actor_type = 'system' WHERE id = ?", (rev, card_id))
        return rev

    def test_purge_rides_the_revision_stream(self):
        insert_card(self.conn, "R-001", "trashed", prev_status="detected")
        insert_card(self.conn, "R-002", "detected")
        rev = bump_rev(self.conn)
        self.conn.execute("UPDATE cards SET board_rev = ?", (rev,))
        cursor = rev  # 客户端已同步到这里

        self._purge("R-001")
        delta = self.conn.execute(
            "SELECT id, tombstone FROM cards WHERE board_rev > ?",
            (cursor,)).fetchall()
        # 增量流里只有被删的卡，且 tombstone=1 → 客户端学到删除
        self.assertEqual([(r["id"], r["tombstone"]) for r in delta],
                         [("R-001", 1)])
        # 看板全量视图不再包含它
        live = [r["id"] for r in self.conn.execute(
            "SELECT id FROM cards WHERE tombstone = 0")]
        self.assertEqual(live, ["R-002"])
        # 内容确已清空，只剩 id + board_rev 骨架
        payload = self.conn.execute(
            "SELECT payload FROM cards WHERE id = 'R-001'").fetchone()[0]
        self.assertEqual(payload, "{}")

    def test_tombstone_row_is_frozen(self):
        insert_card(self.conn, "R-001", "trashed", prev_status="detected")
        self._purge("R-001")
        for sql in (
                "UPDATE cards SET title = 'revived' WHERE id = 'R-001'",
                "UPDATE cards SET status = 'detected', last_actor_type = 'user'"
                " WHERE id = 'R-001'",
                "UPDATE cards SET payload = '{\"a\":1}' WHERE id = 'R-001'"):
            with self.assertRaises(sqlite3.IntegrityError) as cm:
                self.conn.execute(sql)
            self.assertIn("TOMBSTONE_FROZEN", str(cm.exception))

    def test_only_trashed_cards_can_be_tombstoned(self):
        # §9 保留期硬删只作用于回收站；archived NEVER purge（§10）
        insert_card(self.conn, "R-001", "delivered")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE cards SET tombstone = 1 WHERE id = 'R-001'")

    def test_revision_cursor_is_monotonic(self):
        bump_rev(self.conn)
        bump_rev(self.conn)
        for sql in (
                "UPDATE board_revision SET value = value - 1 WHERE id = 1",
                "UPDATE board_revision SET value = value WHERE id = 1"):
            with self.assertRaises(sqlite3.IntegrityError) as cm:
                self.conn.execute(sql)
            self.assertIn("REVISION_MONOTONIC", str(cm.exception))


class AppendOnlyLedgersTestCase(unittest.TestCase):
    """notes / activities 台账：append-only + set-once 回执（§32.2 诚实语义）。"""

    def setUp(self):
        self.conn = open_db()
        insert_card(self.conn, "R-001", "executing")
        self.conn.execute(
            "INSERT INTO notes (card_id, kind, body, actor_type, created_at)"
            " VALUES ('R-001', 'steer', '改用 SQLite', 'user', ?)", (NOW,))
        self.conn.execute(
            "INSERT INTO activities (card_id, actor_type, actor_id, changes,"
            " created_at) VALUES ('R-001', 'system', 'dispatch',"
            " '[{\"field\":\"status\",\"before\":\"approved\",\"after\":\"executing\"}]', ?)",
            (NOW,))

    def tearDown(self):
        self.conn.close()

    def test_note_core_columns_immutable(self):
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.conn.execute("UPDATE notes SET body = '偷改' WHERE id = 1")
        self.assertIn("NOTES_APPEND_ONLY", str(cm.exception))
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.conn.execute("DELETE FROM notes WHERE id = 1")
        self.assertIn("NOTES_APPEND_ONLY", str(cm.exception))

    def test_receipts_are_set_once(self):
        self.conn.execute(
            "UPDATE notes SET delivered_at = ? WHERE id = 1", (NOW,))
        # 幂等重写同值放行（retry 无害）；改成别的时刻拒绝
        self.conn.execute(
            "UPDATE notes SET delivered_at = ? WHERE id = 1", (NOW,))
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.conn.execute(
                "UPDATE notes SET delivered_at = '2026-08-31T00:00:00Z'"
                " WHERE id = 1")
        self.assertIn("NOTES_RECEIPT_SET_ONCE", str(cm.exception))
        # acked_at 同规
        self.conn.execute("UPDATE notes SET acked_at = ? WHERE id = 1", (NOW,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE notes SET acked_at = '2026-08-31T00:00:00Z'"
                " WHERE id = 1")

    def test_activities_fully_immutable(self):
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.conn.execute(
                "UPDATE activities SET actor_type = 'user' WHERE id = 1")
        self.assertIn("ACTIVITIES_APPEND_ONLY", str(cm.exception))
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.conn.execute("DELETE FROM activities WHERE id = 1")
        self.assertIn("ACTIVITIES_APPEND_ONLY", str(cm.exception))


class DedupAndDispatchTestCase(unittest.TestCase):
    """sources 去重键 + dispatches one_active（§46 一卡一活 session）。"""

    def setUp(self):
        self.conn = open_db()
        insert_card(self.conn, "R-001", "executing")
        insert_card(self.conn, "R-002", "executing")

    def tearDown(self):
        self.conn.close()

    def _insert_source(self, card_id, channel, origin_key):
        self.conn.execute(
            "INSERT INTO sources (card_id, channel, quote, origin_key,"
            " created_at) VALUES (?, ?, 'q', ?, ?)",
            (card_id, channel, origin_key, NOW))

    def test_same_external_message_feeds_only_one_card(self):
        self._insert_source("R-001", "slack", "slack:1725000000.000100")
        with self.assertRaises(sqlite3.IntegrityError):
            # 同一条外部消息（同 channel+origin_key）不许再喂第二张卡
            self._insert_source("R-002", "slack", "slack:1725000000.000100")

    def test_null_origin_key_is_unconstrained(self):
        # 手工/meeting 引文无强信号（origin_key=NULL），partial-unique 不管
        self._insert_source("R-001", "meeting", None)
        self._insert_source("R-002", "meeting", None)

    def test_one_active_dispatch_per_card(self):
        self.conn.execute(
            "INSERT INTO dispatches (card_id, status, started_at)"
            " VALUES ('R-001', 'running', ?)", (NOW,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO dispatches (card_id, status, started_at)"
                " VALUES ('R-001', 'running', ?)", (NOW,))
        # 收尾上一轮后允许开新一轮（rework/re-raise 的多轮历史序列）
        self.conn.execute(
            "UPDATE dispatches SET status = 'completed', exit_code = 0,"
            " finished_at = ? WHERE card_id = 'R-001'", (NOW,))
        self.conn.execute(
            "INSERT INTO dispatches (card_id, status, started_at)"
            " VALUES ('R-001', 'running', ?)", (NOW,))

    def test_running_iff_unfinished(self):
        # CHECK ((status='running') = (finished_at IS NULL))：悬挂账两面都拒
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO dispatches (card_id, status, started_at,"
                " finished_at) VALUES ('R-001', 'running', ?, ?)", (NOW, NOW))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO dispatches (card_id, status, started_at)"
                " VALUES ('R-001', 'completed', ?)", (NOW,))


if __name__ == "__main__":
    unittest.main()
