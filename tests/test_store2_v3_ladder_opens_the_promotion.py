"""「促成运行」在一台**升上来的**库上也点得动：v2 → v3 梯子补齐 §78 的白名单行。

契约：CONTRACT **§78** / **§78.4**（梯子 v2 → v3，只加白名单行、不碰任何一张卡，
D80.15）/ §53.1（schema 版本与梯子）/ §53.2（白名单即法条，add-only）/ §0 第 1 条
（只有 actd 主循环能转移卡片状态——梯子不是写卡的地方）。

为什么专门为「升上来的库」写一条：CI 和每一条新判例用的都是**全新建**的库，
schema.sql 从头跑一遍，九条补行天然都在。而真实安装里的库是 v1/v2 一路升上来
的——梯子那一级要是忘了写（或写漏一行），全新库上一切正常、开发机上一切正常，
**只有 owner 的机器**会在他点「促成运行」时撞上 ILLEGAL_TRANSITION。梯子是这次
退役里最容易被「反正 schema.sql 改了」这句话糊弄过去的一环。

起点是真 v2 形（现行 schema.sql 去掉 §78 那批补行、版本钉回 2），不是手搓的
近似库；终点与全新 v3 库**逐行比对**（§78.4 的「两处必须收敛」）。纯 sqlite3 +
store.Store，不碰沙箱注册表。
"""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib.store2 import store as store_mod
from act.lib.store2.store import SCHEMA_VERSION, Store

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "act" / "lib" / "store2" / "schema.sql"

# §78 之前**不存在**的两条关键行：v2 fixture 必须真的没有它们，否则这条判例
# 会在一个假的起点上「通过」。
_V3_ONLY = (("detected", "approved", "system"),     # §65 lane 免批
            ("card_sent", "detected", "system"))    # §78 一次性归并扫描

NOW = "2026-09-26T12:00:00Z"


def _v2_schema_sql() -> str:
    """从现行 schema.sql 反推 v2 形：切掉 §78 那一条 INSERT + 版本钉回 2。"""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    start = sql.index("-- §78 提案车道退役")
    insert_at = sql.index("INSERT OR IGNORE INTO transition_whitelist", start)
    sql = sql[:start] + sql[sql.index(";", insert_at) + 1:]
    sql = sql.replace(f"PRAGMA user_version = {SCHEMA_VERSION};",
                      "PRAGMA user_version = 2;")
    assert "§78 提案车道退役" not in sql, "v2 fixture still carries the §78 rows"
    return sql


def _whitelist(db: Path) -> set:
    con = sqlite3.connect(db)
    try:
        return set(con.execute("SELECT old_status, new_status, actor_type"
                               " FROM transition_whitelist").fetchall())
    finally:
        con.close()


def _objects(db: Path) -> dict:
    con = sqlite3.connect(db)
    try:
        return {(t, n): sql for t, n, sql in con.execute(
            "SELECT type, name, sql FROM sqlite_master"
            " WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type, name")}
    finally:
        con.close()


def _card_row(db: Path, card_id: str) -> dict:
    con = sqlite3.connect(db)
    try:
        con.row_factory = sqlite3.Row
        return dict(con.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone())
    finally:
        con.close()


class V3LadderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="store2-v3-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _v2_db(self, name="v2.db") -> Path:
        db = self.tmp / name
        con = sqlite3.connect(db)
        con.executescript(_v2_schema_sql())
        con.execute(
            "INSERT INTO cards (id, status, title, created, updated, payload,"
            " last_actor_type) VALUES ('P-1', 'detected', '潜在任务里的一张卡',"
            f" '{NOW}', '{NOW}', '{{\"id\":\"P-1\",\"status\":\"detected\"}}', 'system')")
        con.commit()
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 2)
        con.close()
        return db

    def test_the_fixture_really_starts_without_the_new_rows(self):
        """起点自检：v2 库上 §78 的两条关键行确实不存在（否则下面全是空转）。"""
        rows = _whitelist(self._v2_db())
        for row in _V3_ONLY:
            self.assertNotIn(row, rows)

    def test_the_ladder_has_a_step_for_v2(self):
        self.assertIn(2, store_mod._UPGRADES)

    def test_opening_a_v2_db_upgrades_it_and_lands_every_new_row(self):
        db = self._v2_db()
        store = Store(db)
        try:
            self.assertEqual(store._conn().execute("PRAGMA user_version").fetchone()[0],
                             SCHEMA_VERSION)
        finally:
            store.close()
        rows = _whitelist(db)
        for row in store_mod._V3_TRANSITIONS:
            self.assertIn(row, rows, f"{row} missing after the v2→v3 upgrade")

    def test_the_upgraded_db_converges_with_a_fresh_one(self):
        """§78.4：schema.sql 那批 INSERT 与梯子里的 ``_V3_TRANSITIONS`` 必须逐行
        相同——全新装的机器与升上来的机器不能有两套法条。"""
        old = self._v2_db("old.db")
        Store(old).close()
        fresh = self.tmp / "fresh.db"
        Store(fresh).close()
        self.assertEqual(_whitelist(old), _whitelist(fresh))
        self.assertEqual(_objects(old), _objects(fresh))

    def test_the_ladder_never_touches_a_card_row(self):
        """D80.15 / 宪法第 1 条：搬卡是 §78.5 那次扫描的活，梯子只管法条。"""
        db = self._v2_db()
        before = _card_row(db, "P-1")
        Store(db).close()
        self.assertEqual(_card_row(db, "P-1"), before)

    def test_the_upgrade_is_idempotent(self):
        db = self._v2_db()
        Store(db).close()
        rows = _whitelist(db)
        Store(db).close()
        self.assertEqual(_whitelist(db), rows)

    def test_no_card_sent_row_was_removed_on_the_way_up(self):
        """退役 ≠ 删行：存量落单卡还要能被搬走、被 restore、被 done_external。"""
        db = self._v2_db()
        before = {r for r in _whitelist(db) if "card_sent" in (r[0], r[1])}
        Store(db).close()
        self.assertTrue(before)
        self.assertTrue(before <= _whitelist(db))


class PromotionOnAnUpgradedDatabaseTestCase(unittest.TestCase):
    """真正要证明的那件事：owner 在一台升上来的库上点得动「促成运行」。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="store2-v3-promote-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        db = self.tmp / "upgraded.db"
        con = sqlite3.connect(db)
        con.executescript(_v2_schema_sql())
        con.commit()
        con.close()
        self.store = Store(db)          # ← 这一步走梯子
        self.addCleanup(self.store.close)

    def _card(self, card_id, status):
        return self.store.create_card(
            {"id": card_id, "status": status, "title": f"卡 {card_id}",
             "payload": {"id": card_id, "status": status}}, actor_type="system")

    def _move(self, card_id, status, actor):
        return self.store.put_card(card_id, {"id": card_id, "status": status},
                                   {"status": status}, [], actor_type=actor)

    def test_owner_promotes_a_detected_card_straight_to_approved(self):
        self._card("P-100", "detected")
        self.assertEqual(self._move("P-100", "approved", "user")["status"], "approved")

    def test_the_self_improve_lane_promotes_the_same_card_as_system(self):
        """§65 免批：actor=system 的同一跳——这条行 v2 库上根本不存在。"""
        self._card("P-101", "detected")
        self.assertEqual(self._move("P-101", "approved", "system")["status"], "approved")

    def test_the_fold_sweep_move_is_legal_on_an_upgraded_db(self):
        self._card("P-102", "card_sent")
        self.assertEqual(self._move("P-102", "detected", "system")["status"], "detected")

    def test_abort_execution_returns_a_running_card_to_the_backlog(self):
        for i, live in enumerate(("approved", "executing", "review"), start=1):
            with self.subTest(status=live):
                cid = f"P-11{i}"
                self._card(cid, live)
                self.assertEqual(self._move(cid, "detected", "user")["status"], "detected")

    def test_done_external_from_the_backlog_is_legal(self):
        self._card("P-120", "detected")
        self.assertEqual(self._move("P-120", "delivered", "user")["status"], "delivered")

    def test_an_agent_still_cannot_promote(self):
        """权限墙一个字没松：白名单里 ``actor_type='agent'`` 恒零行。"""
        self._card("P-130", "detected")
        with self.assertRaises(store_mod.TransitionDenied):
            self._move("P-130", "approved", "agent")
