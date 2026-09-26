"""§78 的一次性归并扫描：把退役车道上的存量卡搬进潜在任务，幂等、count-agnostic、
actor 合法、单张失败不带走整个 pass。

契约：CONTRACT **§78** / **§78.5**（扫描本体的「纪律四条」+ 「不发通知」）/
**§78.1** 第 3 条（straggler 投影是第二层兜底，不是替代品）/ §44（registry 单
写者：只有 actd 主循环能搬）/ §53.2（``('card_sent','detected','system')`` 白名单
补行）/ §9 §78 追记（回收站卡的 ``prev_status`` 刻意不碰）。

为什么两层都要有：dashboard 把落单的 ``card_sent`` 投进 ``debt[]``，保证它**看
得见**；但看得见不等于**用得了**——卡本身不搬过来，`detected → approved` 那条
白名单行就对不上，owner 点「促成运行」会撞上 ILLEGAL_TRANSITION。投影管可见，
扫描管可用。

「count-agnostic」不是文风要求（防腐第 5 条）：存量多少张是每台安装自己的事，
把数字写进代码或文档，装机第二天它就是假话。所以本文件用**多个不同的张数**跑
同一条断言，而不是钉一个数。

沙箱 AIASSISTANT_HOME；notify mock；sqlite 半边走 tests/store2_testkit。零子进程。
"""
import sqlite3
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.store2_testkit import use_backend

from act import actd
from act.lib import analytics, config, dashboard, registry
from act.lib.actd import dispatch
from act.lib.registry import Requirement, State
from act.lib.store2.store import TransitionDenied


def _mk(rid, status=State.CARD_SENT.value, **kw):
    req = Requirement(id=rid, title=f"存量卡 {rid}", status=status, **kw)
    registry.save(req)
    return req


def _statuses() -> dict:
    return {r.id: str(r.status) for r in registry.load_all()}


class FoldSweepBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        # §78.5 的「每次开机至多一次」是**进程内** latch，整个测试进程只有一次
        # 「开机」——不拨回去的话本文件第二个判例起 `fold_retired_lane()` 恒返 0，
        # 整组判例会集体空转成假绿（正是本文件要防的那类静默）。
        actd._reset_fold_latch()
        self.addCleanup(actd._reset_fold_latch)
        mock.patch.object(actd.notify, "notify", mock.Mock(return_value=True)).start()
        self.addCleanup(mock.patch.stopall)


class CountAgnosticTestCase(FoldSweepBase):
    def test_the_sweep_moves_whatever_it_finds(self):
        """0 张 / 1 张 / 4 张——返回值永远等于查出来的张数，不是写死的数。"""
        for n in (0, 1, 4):
            with self.subTest(stragglers=n):
                for p in config.REGISTRY_DIR.glob("*.yaml"):
                    p.unlink()
                for i in range(n):
                    _mk(f"P-4{n}{i}")
                actd._reset_fold_latch()   # 每个 subTest 模拟一次「开机」（§78.5）
                self.assertEqual(actd.fold_retired_lane(), n)
                self.assertEqual(
                    [r.id for r in registry.load_all()
                     if str(r.status) == State.CARD_SENT.value], [])

    def test_an_empty_retired_lane_is_a_silent_no_op(self):
        _mk("P-410", status=State.DETECTED.value)
        with mock.patch.object(dispatch.analytics, "log_event") as ev:
            self.assertEqual(actd.fold_retired_lane(), 0)
        ev.assert_not_called()

    def test_the_move_is_recorded_as_an_event_carrying_the_count(self):
        # 事件台账在沙箱里是整轮共享的 append-only 文件——只看**本条**新写的那些
        before = len(list(analytics.read_events()))
        _mk("P-420")
        _mk("P-421")
        actd.fold_retired_lane()
        folded = [e for e in list(analytics.read_events())[before:]
                  if e.get("event") == "retired_lane_folded"]
        self.assertEqual([e.get("count") for e in folded], [2])


class IdempotenceTestCase(FoldSweepBase):
    def test_a_second_pass_changes_nothing(self):
        _mk("P-430")
        self.assertEqual(actd.fold_retired_lane(), 1)
        after_first = registry.load("P-430")
        self.assertEqual(actd.fold_retired_lane(), 0)
        after_second = registry.load("P-430")
        self.assertEqual(after_second.status, State.DETECTED.value)
        self.assertEqual(after_second.notes, after_first.notes)

    def test_the_note_is_appended_exactly_once(self):
        _mk("P-431", notes="[2026-01-01] 原本就有的一行")
        actd.fold_retired_lane()
        actd.fold_retired_lane()
        notes = registry.load("P-431").notes
        self.assertEqual(notes.count("提案车道退役，卡移入潜在任务（issue #447）"), 1)
        self.assertIn("[2026-01-01] 原本就有的一行", notes)     # 既有留痕不被覆盖
        self.assertIn("§78", notes)


class BootOnceLatchTestCase(FoldSweepBase):
    """§78.5 纪律「每次开机至多一次（不是每 pass 扫全表）」的执法面。

    幂等只保证「跑第二遍没有副作用」；这条额外保证「第二遍根本不去扫」——
    一次性迁移不该让 actd 每 pass 都多读一遍整本账（YAML 后端是整目录
    读+解析，本 pass 已经为 §65 免批闸读过一次）。"""

    def test_the_second_pass_does_not_scan_at_all(self):
        _mk("P-440")
        with mock.patch.object(dispatch, "fold_retired_lane",
                               wraps=dispatch.fold_retired_lane) as scan:
            self.assertEqual(actd.fold_retired_lane(), 1)
            self.assertEqual(actd.fold_retired_lane(), 0)
            self.assertEqual(actd.fold_retired_lane(), 0)
        self.assertEqual(scan.call_count, 1)      # 扫描只发生一次，后两次是闩

    def test_a_straggler_arriving_after_the_latch_waits_for_the_next_boot(self):
        # 诚实的边界：闩之后盘上再冒出退役状态的卡（旧客户端重放 / 云同步补发），
        # 本次开机不再搬它——投影兜底仍让它在潜在任务里看得见（§78 straggler 投影），
        # 下次开机的扫描把它收走。这不是 bug，是「一次性迁移」的定义。
        self.assertEqual(actd.fold_retired_lane(), 0)
        _mk("P-441")
        self.assertEqual(actd.fold_retired_lane(), 0)
        self.assertEqual(registry.load("P-441").status, State.CARD_SENT.value)
        dash = dashboard.build_dashboard(reqs=registry.load_all(), agents=[],
                                         cfg=config.Config(), archived=[])
        self.assertEqual([r["id"] for r in dash["debt"]], ["P-441"])  # 看得见
        self.assertEqual(dash["needs_approval"], [])                  # 不在退役列上
        actd._reset_fold_latch()                  # 下一次开机
        self.assertEqual(actd.fold_retired_lane(), 1)
        self.assertEqual(registry.load("P-441").status, State.DETECTED.value)


class ScopeTestCase(FoldSweepBase):
    def test_only_the_retired_lane_is_touched(self):
        for rid, status in (("P-440", State.DETECTED.value),
                            ("P-441", State.RAISING.value),
                            ("P-442", State.APPROVED.value),
                            ("P-443", State.EXECUTING.value),
                            ("P-444", State.REVIEW.value),
                            ("P-445", State.DELIVERED.value),
                            ("P-446", State.ARCHIVED.value)):
            _mk(rid, status=status)
        _mk("P-447")
        before = _statuses()
        self.assertEqual(actd.fold_retired_lane(), 1)
        after = _statuses()
        self.assertEqual(after.pop("P-447"), State.DETECTED.value)
        before.pop("P-447")
        self.assertEqual(after, before)

    def test_a_trashed_cards_return_ticket_is_left_alone(self):
        """§78.5 最后一条纪律 + D80.10：回程票是历史事实，扫描刻意不碰它——
        夹逼在 ``registry.restore`` 的读侧（判例见
        tests/test_backlog_is_the_return_landing_pad.py）。"""
        registry.trash(_mk("P-450"), "rejected")
        self.assertEqual(actd.fold_retired_lane(), 0)
        req = registry.load("P-450")
        self.assertEqual(req.status, State.TRASHED.value)
        self.assertEqual(req.prev_status, State.CARD_SENT.value)


class OneBadCardTestCase(FoldSweepBase):
    def test_a_failing_card_never_takes_the_pass_down(self):
        """宪法第 11 条：一张写不动的卡只记一行日志，其余照搬，下次开机重试。"""
        _mk("P-460")
        _mk("P-461")
        real_save = dispatch.registry.save

        def flaky(req):
            if req.id == "P-460":
                raise OSError("disk full")
            return real_save(req)

        logged = []
        with mock.patch.object(dispatch.registry, "save", flaky), \
                mock.patch.object(actd, "_log", logged.append):
            folded = actd.fold_retired_lane()
        self.assertEqual(folded, 1)
        self.assertEqual(registry.load("P-461").status, State.DETECTED.value)
        self.assertEqual(registry.load("P-460").status, State.CARD_SENT.value)
        self.assertTrue([line for line in logged if "P-460" in line and "FAILED" in line])


class MigrationIsNotNewsTestCase(FoldSweepBase):
    """§78.5「不发通知」：被搬的卡搬之前就已经在 ``debt[]`` 里（straggler 投影），
    所以 diff 天然不把它当新卡——一次迁移不是一件新事。"""

    def test_folding_a_straggler_fires_no_new_card_notification(self):
        _mk("P-470")
        cfg = config.Config()
        prev = dashboard.build_dashboard(reqs=registry.load_all(), agents=[],
                                         cfg=cfg, archived=[])
        self.assertEqual([r["id"] for r in prev["debt"]], ["P-470"])
        actd.fold_retired_lane()
        curr = dashboard.build_dashboard(reqs=registry.load_all(), agents=[],
                                         cfg=cfg, archived=[])
        self.assertEqual([r["id"] for r in curr["debt"]], ["P-470"])
        self.assertEqual(actd.detect_transitions(prev, curr), [])


class SqliteBackendTestCase(FoldSweepBase):
    """§53.2 / §44：sqlite 后端上这次搬运必须是**合法转移**，且 actor 是
    ``system``（自主管线的搬运，不是 owner 动作，更不是 agent）。"""

    def setUp(self):
        super().setUp()
        use_backend(self, "sqlite")

    def _row(self, card_id):
        con = sqlite3.connect(registry.store2_db_path())
        try:
            con.row_factory = sqlite3.Row
            return dict(con.execute(
                "SELECT status, last_actor_type FROM cards WHERE id = ?",
                (card_id,)).fetchone())
        finally:
            con.close()

    def test_the_transition_is_whitelisted_for_the_system_actor(self):
        con = sqlite3.connect(registry.store2_db_path())
        try:
            rows = con.execute(
                "SELECT actor_type FROM transition_whitelist"
                " WHERE old_status = 'card_sent' AND new_status = 'detected'"
            ).fetchall()
        finally:
            con.close()
        self.assertIn("system", [r[0] for r in rows])

    def test_the_sweep_moves_the_card_under_the_system_actor(self):
        _mk("P-480")
        self.assertEqual(actd.fold_retired_lane(), 1)
        row = self._row("P-480")
        self.assertEqual(row["status"], State.DETECTED.value)
        self.assertEqual(row["last_actor_type"], "system")

    def test_the_same_move_is_illegal_for_an_agent(self):
        """权限墙还在：搬卡的合法性系在 actor 上，不是「反正是个转移」——
        白名单里一行 ``actor_type='agent'`` 都没有（宪法第 1 条的 SQL 化）。"""
        _mk("P-481")
        req = registry.load("P-481")
        req.set_status(State.DETECTED)
        with self.assertRaises(TransitionDenied) as cm:
            with registry.acting_as("agent"):
                registry.save(req)
        self.assertEqual(cm.exception.code, "AGENT_TRANSITION_FORBIDDEN")
        self.assertEqual(self._row("P-481")["status"], State.CARD_SENT.value)

    def test_the_whitelist_grants_the_agent_nothing(self):
        con = sqlite3.connect(registry.store2_db_path())
        try:
            rows = con.execute("SELECT COUNT(*) FROM transition_whitelist"
                               " WHERE actor_type = 'agent'").fetchone()
        finally:
            con.close()
        self.assertEqual(rows[0], 0)
