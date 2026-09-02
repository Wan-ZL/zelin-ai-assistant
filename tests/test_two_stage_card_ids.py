"""两段式卡片编号判例（CONTRACT §60，owner 决策 D21，issue #127）。

D21 原话：「如果这个卡片没有执行,就不算是真正的卡片,不需要给它 R 编号;只有我
approve 跑了的,才给编号。」

钉住的行为（两后端逐条跑）：
  * 出生 = ``P-<n>`` 主键（next_id），detected/card_sent/raising/merge/trash 一律
    **不**分配工作编号；
  * 进入 approved 的每条路径都分配 ``R-<m>``：owner approve、§51 免批、capture[run]
    出生即 approved、restore 精确复位回 approved；
  * 工作序列稠密、单调、永不复用（含 sqlite tombstone / yaml 硬删 + 高水位）；
  * set-once：退回提案再批准、trash→restore 都不换号；
  * resolve() 主键与工作编号双向可达；inbox/merge 入口按两种 ref 都能找到卡且
    lineage 只落主键；
  * legacy ``R-<n>`` 主键：从未批准 → id_kind=legacy、display_id=主键；批准 →
    work_id 采纳自己的主键（不另发号）；
  * 投影行 display_id / id_kind / work_id；executor 的 prompt 头 / 会话名 / 日志名
    用显示编号，analytics 仍记主键；
  * store2 schema v1 → v2 升级梯子：加列 + 唯一索引 + set-once 触发器，crash window
    幂等重跑，全新库与升级库形状收敛；
  * 导出 ↔ 迁移 round-trip 带 work_id；
  * 跨命名空间序键（auto_merge / quick_capture / actd FIFO）。
跨进程的序列判例住 tests/integration/test_work_seq_cross_process.py。
"""
import json
import re
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env 先于任何 act.* import
from tests import store2_testkit

from act import actd, executor
from act.lib import auto_merge, config, dashboard, quick_capture, registry
from act.lib.registry import Requirement, State
from act.lib.store2 import export_yaml, hot, migrate_yaml
from act.lib.store2.store import (SCHEMA_VERSION, IntegrityViolation, Store,
                                  StoreError)

BACKENDS = ("yaml", "sqlite")
SCHEMA_PATH = Path(registry.__file__).parent / "store2" / "schema.sql"


def _card(rid, title, status=State.CARD_SENT.value, **kw):
    base = dict(id=rid, title=title, type="dev", tier="T1", status=status,
                hardness="soft", repeated_mentions=1,
                sources=[{"channel": "meeting", "date": "2026-08-30",
                          "ref": f"ref-{rid}", "quote": f"quote {title}",
                          "who": "manager"}],
                plan=["step 1"], summary=f"summary of {title}")
    base.update(kw)
    return Requirement.from_dict(base)


def _approve(rid):
    """owner approve 的 actd 路径（inbox → _apply_decision）。"""
    with registry.acting_as("user"):
        return actd._apply_decision(registry.load(rid), "approve", None)


class _Both(unittest.TestCase):
    """每条判例在两后端各跑一遍（subTest）。"""

    def for_each_backend(self, body):
        for backend in BACKENDS:
            with self.subTest(backend=backend):
                store2_testkit.use_backend(self, backend)
                body(backend)


# --------------------------------------------------------------------------- #
# 出生 = P- 主键，检测/合并/回收站不发工作号
# --------------------------------------------------------------------------- #
class BirthNeverConsumesWorkNumberTestCase(_Both):
    def test_next_id_is_provisional_and_dense(self):
        def body(_b):
            self.assertEqual(registry.next_id(), "P-001")
            registry.upsert(_card("P-001", "a", status=State.DETECTED.value))
            self.assertEqual(registry.next_id(), "P-002")
            # 存量 legacy 主键不影响 P 序列
            registry.upsert(_card("R-040", "legacy", status=State.DETECTED.value))
            self.assertEqual(registry.next_id(), "P-002")
        self.for_each_backend(body)

    def test_detection_merge_trash_allocate_nothing(self):
        def body(_b):
            kind, new = registry.merge_or_new_with_kind(
                {"title": "全新的事", "sources": [
                    {"channel": "gmail", "date": "2026-08-31", "ref": "m-1",
                     "quote": "ask", "who": "hr"}]})
            self.assertEqual(kind, "proposed")
            self.assertTrue(new.id.startswith("P-"))
            self.assertIsNone(new.work_id)
            # 纯重述折叠：仍无号
            _kind, folded = registry.merge_or_new_with_kind(
                {"title": "全新的事", "sources": [
                    {"channel": "slack", "date": "2026-08-31", "ref": "s-1",
                     "quote": "again", "who": "hr"}]})
            self.assertIsNone(folded.work_id)
            # raising / card_sent / trash 都不发
            r = registry.load(new.id)
            r.set_status(State.RAISING)
            registry.save(r)
            r.set_status(State.CARD_SENT)
            registry.save(r)
            with registry.acting_as("user"):
                registry.trash(registry.load(new.id), "rejected")
            self.assertIsNone(registry.load(new.id).work_id)
            self.assertEqual(registry.next_work_id(), "R-001")
        self.for_each_backend(body)

    def test_merge_secondary_gets_no_number_and_lineage_uses_keys(self):
        def body(_b):
            registry.upsert(_card("P-001", "主卡"))
            registry.upsert(_card("P-002", "副卡"))
            with registry.acting_as("user"):
                sec = registry.load("P-002")
                sec.set_status(State.MERGED)
                sec.merged_into = "P-001"
                registry.save(sec)
            self.assertIsNone(registry.load("P-002").work_id)
            self.assertEqual(registry.load("P-002").merged_into, "P-001")
        self.for_each_backend(body)


# --------------------------------------------------------------------------- #
# 进入 approved 的每条路径都分配；set-once；稠密单调
# --------------------------------------------------------------------------- #
class ApprovalAllocatesTestCase(_Both):
    def setUp(self):
        self.cfg = config.Config()
        self.cfg.memory_inject = False

    def test_owner_approve_allocates(self):
        def body(_b):
            registry.upsert(_card("P-001", "写周报"))
            self.assertEqual(_approve("P-001"), "running")
            saved = registry.load("P-001")
            self.assertEqual(saved.status, State.APPROVED.value)
            self.assertEqual(saved.work_id, "R-001")
            self.assertEqual(registry.display_id(saved), "R-001")
            self.assertEqual(registry.id_kind(saved), registry.ID_KIND_WORK)
        self.for_each_backend(body)

    def test_policy_auto_dispatch_allocates(self):
        def body(_b):
            hand = _card("P-001", "手打卡", type="other", cost_estimate_usd=1.0,
                         sources=[{"who": "zelin", "channel": "quick",
                                   "date": "2026-08-31", "quote": "原话"}])
            registry.upsert(hand)
            with mock.patch.object(actd.notify, "notify"):
                self.assertEqual(actd.auto_dispatch_pass(self.cfg), 1)
            saved = registry.load("P-001")
            self.assertEqual(saved.status, State.APPROVED.value)
            self.assertEqual(saved.work_id, "R-001")
        self.for_each_backend(body)

    def test_capture_run_born_approved_allocates(self):
        def body(_b):
            for p in config.INBOX_DIR.glob("*.json"):
                p.unlink()
            payload = {"action": "capture", "text": "直接跑：整理周报",
                       "mode": "run", "ts": "2026-09-01T00:00:00Z"}
            (config.INBOX_DIR / f"capture-{uuid.uuid4()}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            actd._SYNC_ACTIVE_CACHE = None
            self.assertEqual(actd.process_inbox(), 1)
            req = [r for r in registry.load_all() if r.title == "直接跑：整理周报"][0]
            self.assertTrue(req.id.startswith("P-"))
            self.assertEqual(req.status, State.APPROVED.value)
            self.assertEqual(req.work_id, "R-001")
        self.for_each_backend(body)

    def test_restore_into_approved_allocates_and_keeps_existing(self):
        def body(_b):
            # ① 从未批准过的卡：trash（prev=card_sent）→ restore 回 card_sent = 无号
            registry.upsert(_card("P-001", "提案"))
            with registry.acting_as("user"):
                registry.trash(registry.load("P-001"), "rejected")
                registry.restore(registry.load("P-001"))
            self.assertIsNone(registry.load("P-001").work_id)
            # ② 批准过的卡：trash → restore 精确复位回 approved，号不变（set-once）
            _approve("P-001")
            self.assertEqual(registry.load("P-001").work_id, "R-001")
            with registry.acting_as("user"):
                registry.trash(registry.load("P-001"), "deleted")
                self.assertEqual(registry.load("P-001").work_id, "R-001")  # 回收站保号
                registry.restore(registry.load("P-001"))
            saved = registry.load("P-001")
            self.assertEqual(saved.status, State.APPROVED.value)
            self.assertEqual(saved.work_id, "R-001")
            # ③ 手编回程票：trashed + prev_status=approved 的卡 restore 进 approved 也分号
            registry.upsert(_card("P-002", "带票", status=State.TRASHED.value,
                                  prev_status=State.APPROVED.value,
                                  trashed_at="2026-09-01T00:00:00Z"))
            with registry.acting_as("user"):
                registry.restore(registry.load("P-002"))
            self.assertEqual(registry.load("P-002").work_id, "R-002")
        self.for_each_backend(body)

    def test_abort_and_reapprove_keeps_the_same_number(self):
        def body(_b):
            registry.upsert(_card("P-001", "反复"))
            _approve("P-001")
            with registry.acting_as("user"):
                actd._apply_decision(registry.load("P-001"), "abort_execution", None)
            back = registry.load("P-001")
            self.assertEqual(back.status, State.CARD_SENT.value)
            self.assertEqual(back.work_id, "R-001")      # 退回提案不收回号
            _approve("P-001")
            self.assertEqual(registry.load("P-001").work_id, "R-001")
            self.assertEqual(registry.next_work_id(), "R-002")
        self.for_each_backend(body)

    def test_sequence_is_dense_monotonic_and_never_reused(self):
        def body(backend):
            for i in range(1, 5):
                registry.upsert(_card(f"P-{i:03d}", f"卡{i}"))
            for i in (3, 1, 4, 2):
                _approve(f"P-{i:03d}")
            got = [registry.load(f"P-{i:03d}").work_id for i in (3, 1, 4, 2)]
            self.assertEqual(got, ["R-001", "R-002", "R-003", "R-004"])
            # 最大号的卡进回收站并硬删：号不复用（sqlite tombstone 保热列；yaml 靠
            # state/work_seq.json 高水位）
            with registry.acting_as("user"):
                registry.trash(registry.load("P-002"), "deleted")
            self.assertTrue(registry.delete(registry.load("P-002")))
            self.assertEqual(registry.next_work_id(), "R-005")
            seq = json.loads((config.STATE_DIR / registry.WORK_SEQ_NAME)
                             .read_text(encoding="utf-8"))
            self.assertEqual(seq, {"work_seq": 4})
        self.for_each_backend(body)

    def test_sequence_starts_above_legacy_keys(self):
        def body(_b):
            registry.upsert(_card("R-278", "存量最大号", status=State.DETECTED.value))
            registry.upsert(_card("P-001", "新卡"))
            _approve("P-001")
            self.assertEqual(registry.load("P-001").work_id, "R-279")
            # resolve 无歧义：R-278 是 legacy 主键，R-279 是工作编号
            self.assertEqual(registry.resolve("R-278").id, "R-278")
            self.assertEqual(registry.resolve("R-279").id, "P-001")
        self.for_each_backend(body)

    def test_allocation_failure_does_not_break_save(self):
        def body(_b):
            registry.upsert(_card("P-001", "健壮"))
            with mock.patch.object(registry, "next_work_id",
                                   side_effect=RuntimeError("boom")):
                _approve("P-001")
            saved = registry.load("P-001")
            self.assertEqual(saved.status, State.APPROVED.value)
            self.assertIsNone(saved.work_id)          # 卡照常落盘，号下次补
            registry.save(saved)                      # 下一次 approved 落盘补号
            self.assertEqual(registry.load("P-001").work_id, "R-001")
        self.for_each_backend(body)

    def test_failed_write_releases_the_number(self):
        # sqlite：put_card 抛 → 内存里刚分的号清掉，重试不会带着同一个号撞 UNIQUE
        store2_testkit.use_backend(self, "sqlite")
        registry.upsert(_card("P-001", "写失败"))
        r = registry.load("P-001")
        r.set_status(State.APPROVED)
        with mock.patch.object(Store, "put_card",
                               side_effect=StoreError("X", "disk full")):
            with self.assertRaises(StoreError):
                registry.save(r)
        self.assertIsNone(r.work_id)
        registry.save(r)
        self.assertEqual(registry.load("P-001").work_id, "R-001")


# --------------------------------------------------------------------------- #
# legacy 主键
# --------------------------------------------------------------------------- #
class LegacyKeysTestCase(_Both):
    def test_unapproved_legacy_card_is_greyed_not_renumbered(self):
        def body(_b):
            registry.upsert(_card("R-050", "存量提案"))
            r = registry.load("R-050")
            self.assertIsNone(r.work_id)
            self.assertEqual(registry.display_id(r), "R-050")
            self.assertEqual(registry.id_kind(r), registry.ID_KIND_LEGACY)
        self.for_each_backend(body)

    def test_approved_legacy_card_adopts_its_own_key(self):
        def body(_b):
            registry.upsert(_card("R-050", "存量提案"))
            _approve("R-050")
            r = registry.load("R-050")
            self.assertEqual(r.work_id, "R-050")
            self.assertEqual(registry.id_kind(r), registry.ID_KIND_WORK)
            # 采纳不消耗序列：下一张新卡拿 R-051
            registry.upsert(_card("P-001", "新卡"))
            _approve("P-001")
            self.assertEqual(registry.load("P-001").work_id, "R-051")
        self.for_each_backend(body)

    def test_legacy_cards_past_approval_count_as_work_and_adopt_key_on_save(self):
        def body(_b):
            # 已交付的存量卡：升级后首次落盘采纳主键作 work_id；落盘前 id_kind 也已按
            # 状态判 work（不灰显——它的 R 号是批准后跑出来的）
            registry.upsert(_card("R-030", "已交付", status=State.DELIVERED.value))
            r = registry.load("R-030")
            self.assertEqual(r.work_id, "R-030")
            self.assertEqual(registry.id_kind(r), registry.ID_KIND_WORK)
            # 带 approved 回程票进回收站的存量卡同样算 work
            registry.upsert(_card("R-031", "回收站里的已批卡", status=State.TRASHED.value,
                                  prev_status=State.EXECUTING.value,
                                  trashed_at="2026-09-01T00:00:00Z"))
            self.assertEqual(registry.load("R-031").work_id, "R-031")
            # 从未批准的存量卡：raising / card_sent / 带 card_sent 票的回收站 = legacy
            for rid, st, prev in (("R-032", State.RAISING.value, None),
                                  ("R-033", State.TRASHED.value, State.CARD_SENT.value)):
                registry.upsert(_card(rid, "噪音", status=st, prev_status=prev,
                                      trashed_at="2026-09-01T00:00:00Z" if prev else None))
                got = registry.load(rid)
                self.assertIsNone(got.work_id, rid)
                self.assertEqual(registry.id_kind(got), registry.ID_KIND_LEGACY, rid)
            # 序列下界仍是 legacy 最大号（采纳不消耗）
            self.assertEqual(registry.next_work_id(), "R-034")
        self.for_each_backend(body)

    def test_legacy_approved_card_keeps_log_name(self):
        # 存量 approved 卡的派发日志仍叫 R-<n>.log（display_id 回落主键）
        store2_testkit.use_backend(self, "yaml")
        req = _card("R-060", "存量排队卡", status=State.APPROVED.value)
        self.assertEqual(registry.display_id(req), "R-060")
        self.assertEqual(executor.session_name(req), "R-060 · 存量排队卡")


# --------------------------------------------------------------------------- #
# resolve：inbox / merge 入口
# --------------------------------------------------------------------------- #
class ResolveTestCase(_Both):
    def test_resolve_by_key_or_work_id(self):
        def body(_b):
            registry.upsert(_card("P-001", "a"))
            _approve("P-001")
            self.assertEqual(registry.resolve("P-001").id, "P-001")
            self.assertEqual(registry.resolve("R-001").id, "P-001")
            self.assertIsNone(registry.resolve("R-999"))
            self.assertIsNone(registry.resolve(""))
            self.assertIsNone(registry.load_by_work_id("P-001"))   # 不是工作号形
        self.for_each_backend(body)

    def test_inbox_decision_by_work_id(self):
        def body(_b):
            for p in config.INBOX_DIR.glob("*.json"):
                p.unlink()
            registry.upsert(_card("P-001", "a"))
            _approve("P-001")
            payload = {"action": "abort_execution", "id": "R-001", "comment": None,
                       "ts": "2026-09-01T00:00:00Z"}
            (config.INBOX_DIR / f"{uuid.uuid4()}.json").write_text(
                json.dumps(payload), encoding="utf-8")
            actd._SYNC_ACTIVE_CACHE = None
            self.assertEqual(actd.process_inbox(), 1)
            self.assertEqual(registry.load("P-001").status, State.CARD_SENT.value)
        self.for_each_backend(body)

    def test_merge_force_by_work_ids_writes_key_lineage(self):
        def body(_b):
            registry.upsert(_card("P-001", "主"))
            registry.upsert(_card("P-002", "副"))
            _approve("P-001")
            _approve("P-002")
            with registry.acting_as("user"), \
                    mock.patch.object(actd, "_stop_live_session"):
                result = actd._apply_merge_force(["R-001", "R-002"], "R-001")
            self.assertEqual(result, "running")
            sec = registry.load("P-002")
            self.assertEqual(sec.status, State.MERGED.value)
            self.assertEqual(sec.merged_into, "P-001")   # lineage 只认主键
        self.for_each_backend(body)

    def test_canonical_ids_dedupes_key_and_work_id_of_same_card(self):
        store2_testkit.use_backend(self, "yaml")
        registry.upsert(_card("P-001", "a"))
        _approve("P-001")
        ids, missing = actd._canonical_ids(["P-001", "R-001", "R-777"])
        self.assertEqual(ids, ["P-001"])
        self.assertEqual(missing, ["R-777"])


# --------------------------------------------------------------------------- #
# 投影 / executor / 排序
# --------------------------------------------------------------------------- #
class ProjectionAndNamingTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_dashboard_rows_carry_display_id_and_kind(self):
        registry.upsert(_card("P-001", "提案"))
        registry.upsert(_card("P-002", "备选", status=State.DETECTED.value))
        registry.upsert(_card("R-050", "存量提案"))
        registry.upsert(_card("P-003", "已批"))
        _approve("P-003")
        with registry.acting_as("user"):
            registry.trash(registry.load("P-001"), "rejected")
        dash = dashboard.build_dashboard(agents=[], cfg=config.Config())
        rows = {r["id"]: r for sec in ("needs_approval", "running", "debt", "trash")
                for r in dash[sec]}
        self.assertEqual(rows["R-050"]["display_id"], "R-050")
        self.assertEqual(rows["R-050"]["id_kind"], "legacy")
        self.assertNotIn("work_id", rows["R-050"])
        self.assertEqual(rows["P-002"]["display_id"], "P-002")
        self.assertEqual(rows["P-002"]["id_kind"], "proposal")
        # 工作序列从 legacy 上界 R-050 之上起 → 第一张批准卡拿 R-051
        self.assertEqual(rows["P-003"]["display_id"], "R-051")
        self.assertEqual(rows["P-003"]["work_id"], "R-051")
        self.assertEqual(rows["P-003"]["id_kind"], "work")
        self.assertEqual(rows["P-003"]["id"], "P-003")       # 动作回传键不动
        self.assertEqual(rows["P-001"]["display_id"], "P-001")  # 回收站行同样带

    def test_executor_uses_display_id_for_human_faces(self):
        req = _card("P-007", "写周报", status=State.APPROVED.value, work_id="R-280")
        cfg = config.Config()
        cfg.memory_inject = False
        with mock.patch.object(executor, "has_remote", return_value=False):
            prompt = executor.build_prompt(req, cfg, target=Path(tempfile.mkdtemp()))
        self.assertIn("# Requirement R-280: 写周报", prompt)
        self.assertNotIn("# Requirement P-007", prompt)
        self.assertEqual(executor.session_name(req), "R-280 · 写周报")

    def test_dispatch_log_named_by_work_id_and_analytics_by_key(self):
        target = Path(tempfile.mkdtemp(prefix="ids-target-"))
        (target / "keep.txt").write_text("x", encoding="utf-8")
        req = _card("P-007", "派发", status=State.APPROVED.value,
                    target_repo=str(target))
        registry.save(req)                       # approved 落盘 → work_id R-001
        cfg = config.Config()
        cfg.memory_inject = False
        proc = mock.Mock(returncode=0, stdout="backgrounded · e88561e5\n", stderr="")
        with mock.patch.object(executor, "has_remote", return_value=False), \
                mock.patch.object(executor.notify, "notify"):
            out = executor.dispatch(registry.load("P-007"), cfg,
                                    runner=mock.Mock(return_value=proc))
        self.assertEqual(Path(out.execution["log"]).name, "R-001.log")
        self.assertIn("# dispatch R-001 (P-007)",
                      Path(out.execution["log"]).read_text(encoding="utf-8"))
        events = [json.loads(ln) for ln in
                  (config.STATE_DIR / "analytics" / "events.jsonl")
                  .read_text(encoding="utf-8").splitlines() if ln.strip()]
        disp = [e for e in events if e.get("event") == "dispatch" and e.get("req") == "P-007"]
        self.assertTrue(disp, "analytics 仍按主键记账")

    def test_cross_namespace_sort_key(self):
        ids = ["P-002", "R-300", "P-001", "R-002", "weird", "R-010"]
        self.assertEqual(sorted(ids, key=registry.id_sort_key),
                         ["R-002", "R-010", "R-300", "P-001", "P-002", "weird"])
        # auto_merge / quick_capture 的「更老」判断同源
        self.assertLess(auto_merge._idnum("R-300"), auto_merge._idnum("P-001"))

    def test_raising_fifo_prefers_legacy_backlog(self):
        registry.upsert(_card("P-001", "新的", status=State.RAISING.value))
        registry.upsert(_card("R-200", "老的", status=State.RAISING.value))
        picked = []
        with mock.patch.object(actd, "analyze") as an:
            an.expand_debt = lambda r: picked.append(r.id)
            actd.process_raising(config.Config())
        self.assertEqual(picked, ["R-200"])

    def test_inventory_orders_legacy_before_provisional(self):
        registry.upsert(_card("P-001", "新提案"))
        registry.upsert(_card("R-200", "老提案"))
        text = quick_capture.registry_inventory_text()
        self.assertLess(text.index("R-200"), text.index("P-001"))


# --------------------------------------------------------------------------- #
# store2 schema v1 → v2
# --------------------------------------------------------------------------- #
def _v1_schema_sql() -> str:
    """从 v2 schema.sql 反推 v1 形状（去掉 §60 的三件 + 版本钉回 1）——升级判例
    的起点必须是「真正没有 work_id 列」的库。"""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    sql = re.sub(r"\n  work_id\s+TEXT,\n", "\n", sql)
    sql = re.sub(r"CREATE UNIQUE INDEX IF NOT EXISTS cards_work_id\n.*?;\n", "", sql,
                 flags=re.S)
    sql = re.sub(r"CREATE TRIGGER IF NOT EXISTS cards_work_id_set_once\n.*?END;\n", "",
                 sql, flags=re.S)
    sql = sql.replace(f"PRAGMA user_version = {SCHEMA_VERSION};",
                      "PRAGMA user_version = 1;")
    assert "work_id" not in re.sub(r"--[^\n]*", "", sql), "v1 fixture still mentions work_id"
    return sql


def _objects(db: Path) -> dict:
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"
                           " WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
                           " ORDER BY type, name").fetchall()
        cols = [r[1] for r in con.execute("PRAGMA table_info(cards)")]
    finally:
        con.close()
    return {"objects": {(t, n): tbl for t, n, tbl, _sql in rows}, "cards_cols": cols}


class SchemaUpgradeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="store2-v2-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _v1_db(self, name="v1.db") -> Path:
        db = self.tmp / name
        con = sqlite3.connect(db)
        con.executescript(_v1_schema_sql())
        con.execute("INSERT INTO cards (id, status, title, created, updated, payload)"
                    " VALUES ('R-001', 'approved', 't', 'x', 'x', '{\"id\":\"R-001\"}')")
        con.commit()
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
        con.close()
        return db

    def test_v1_db_upgrades_in_place_and_keeps_rows(self):
        db = self._v1_db()
        store = Store(db)
        try:
            con = store._conn()
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0],
                             SCHEMA_VERSION)
            self.assertIn("work_id", [r[1] for r in con.execute("PRAGMA table_info(cards)")])
            card = store.get_card("R-001")
            self.assertIsNone(card["work_id"])          # legacy 行不回填
            self.assertEqual(card["status"], "approved")
        finally:
            store.close()

    def test_upgraded_db_matches_fresh_db_shape(self):
        old = self._v1_db("old.db")
        Store(old).close()
        fresh = self.tmp / "fresh.db"
        Store(fresh).close()
        self.assertEqual(_objects(old)["objects"], _objects(fresh)["objects"])
        self.assertEqual(_objects(old)["cards_cols"], _objects(fresh)["cards_cols"])
        self.assertEqual(_objects(fresh)["cards_cols"][-1], "work_id")

    def test_crash_window_column_added_but_version_not_bumped(self):
        # ALTER 落了、版本没钉（升级途中崩）：重开必须幂等补全，不能 duplicate column
        db = self._v1_db()
        con = sqlite3.connect(db)
        con.execute("ALTER TABLE cards ADD COLUMN work_id TEXT")
        con.commit()
        con.close()
        store = Store(db)
        try:
            self.assertEqual(store._conn().execute("PRAGMA user_version").fetchone()[0],
                             SCHEMA_VERSION)
        finally:
            store.close()

    def test_future_version_still_refused(self):
        db = self._v1_db()
        con = sqlite3.connect(db)
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        con.close()
        with self.assertRaises(StoreError) as cm:
            Store(db)
        self.assertEqual(cm.exception.code, "SCHEMA_VERSION_MISMATCH")

    def test_set_once_trigger_and_unique_index(self):
        store = Store(self.tmp / "t.db")
        try:
            base = {"id": "P-001", "title": "t", "type": "", "tier": "T1",
                    "status": "approved", "sources": [], "plan": None}
            hot_cols, _w, _e = hot.derive(export_yaml.normalize_card(
                dict(base, work_id="R-001")))
            store.put_card("P-001", dict(base, work_id="R-001"), hot_cols, [])
            # 改号 → WORK_ID_SET_ONCE
            hot2, _w, _e = hot.derive(export_yaml.normalize_card(dict(base, work_id="R-002")))
            with self.assertRaises(IntegrityViolation) as cm:
                store.put_card("P-001", dict(base, work_id="R-002"), hot2, [])
            self.assertEqual(cm.exception.code, "WORK_ID_SET_ONCE")
            # 清空 → 同样拒绝（payload 丢号不许静默抹掉）
            hot3, _w, _e = hot.derive(export_yaml.normalize_card(dict(base)))
            with self.assertRaises(IntegrityViolation):
                store.put_card("P-001", dict(base), hot3, [])
            # 另一张卡拿同号 → WORK_ID_DUPLICATE
            with self.assertRaises(IntegrityViolation) as cm:
                store.put_card("P-002", dict(base, id="P-002", work_id="R-001"),
                               dict(hot_cols), [])
            self.assertEqual(cm.exception.code, "WORK_ID_DUPLICATE")
            # tombstone 后热列仍在 → 序列不复用
            with registry.acting_as("user"):
                pass
            store.put_card("P-001", dict(base, work_id="R-001", status="trashed",
                                         prev_status="approved"),
                           dict(hot_cols, status="trashed", prev_status="approved"),
                           [], actor_type="user")
            store.purge_trashed("P-001")
            row = store.get_card("P-001")
            self.assertEqual((row["tombstone"], row["work_id"]), (1, "R-001"))
        finally:
            store.close()

    def test_migrate_check_target_accepts_current_version_only(self):
        fresh = self.tmp / "f.db"
        Store(fresh).close()
        self.assertEqual(migrate_yaml.check_target(fresh), "schema-only")
        old = self._v1_db()
        with self.assertRaises(migrate_yaml.MigrateError):
            migrate_yaml.check_target(old)


# --------------------------------------------------------------------------- #
# 导出 ↔ 迁移 round-trip 带 work_id
# --------------------------------------------------------------------------- #
class ExportImportParityTestCase(unittest.TestCase):
    def test_work_id_survives_yaml_migration_and_export(self):
        tmp = Path(tempfile.mkdtemp(prefix="ids-rt-"))
        reg = tmp / "registry"
        reg.mkdir()
        (reg / "P-001.yaml").write_text(
            "id: P-001\ntitle: 带号的卡\ntype: dev\ntier: T1\nstatus: executing\n"
            "hardness: soft\ndeadline: null\nrepeated_mentions: 1\n"
            "green_sign_required: false\ndisagreement: null\ncost_estimate_usd: null\n"
            "sources: []\nplan: null\nwork_id: R-280\n", encoding="utf-8")
        (reg / "P-002.yaml").write_text(
            "id: P-002\ntitle: 无号\ntype: dev\ntier: T1\nstatus: detected\n"
            "hardness: soft\ndeadline: null\nrepeated_mentions: 1\n"
            "green_sign_required: false\ndisagreement: null\ncost_estimate_usd: null\n"
            "sources: []\nplan: null\n", encoding="utf-8")
        db = tmp / "s.db"
        self.assertEqual(migrate_yaml.main(["--registry", str(reg), "--db", str(db)]), 0)
        con = sqlite3.connect(db)
        self.assertEqual(con.execute("SELECT work_id FROM cards WHERE id='P-001'")
                         .fetchone()[0], "R-280")
        self.assertIsNone(con.execute("SELECT work_id FROM cards WHERE id='P-002'")
                          .fetchone()[0])
        con.close()
        out = tmp / "export"
        self.assertEqual(export_yaml.export_db(db, out), 0)
        self.assertEqual((out / "P-001.yaml").read_text(encoding="utf-8"),
                         (reg / "P-001.yaml").read_text(encoding="utf-8"))
        self.assertEqual((out / "P-002.yaml").read_text(encoding="utf-8"),
                         (reg / "P-002.yaml").read_text(encoding="utf-8"))

    def test_normalize_card_mirrors_from_dict_for_work_id(self):
        raw = {"id": "P-001", "title": "t", "work_id": 280}     # 手编成 int
        want = Requirement.from_dict(raw).to_dict()
        got = export_yaml.normalize_card(raw)
        self.assertEqual(list(got.items()), list(want.items()))
        self.assertEqual(got["work_id"], "280")


if __name__ == "__main__":
    unittest.main()
